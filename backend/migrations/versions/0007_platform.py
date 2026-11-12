"""Platform expansion: super admin and partner roles, staff sessions and scopes, a tamper-evident
audit chain, moderation tools, sanctions, broadcasts, announcements, partners and webhooks,
verifier applications, incident updates, support, privacy requests and watch zones.

Every new table is server-only: clients reach it through the API, never directly.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

NEW_TABLES = [
    "partners",
    "staff_scopes",
    "staff_logins",
    "incident_notes",
    "incident_updates",
    "user_sanctions",
    "reputation_changes",
    "broadcasts",
    "announcements",
    "webhooks",
    "webhook_deliveries",
    "verifier_applications",
    "support_tickets",
    "support_messages",
    "privacy_requests",
    "watch_zones",
]

UPGRADE = [
    # Roles
    "ALTER TABLE profiles DROP CONSTRAINT profiles_role_check",
    "ALTER TABLE profiles ADD CONSTRAINT profiles_role_check CHECK (role IN "
    "('member','verifier','moderator','admin','super_admin','partner'))",
    "ALTER TABLE profiles ADD COLUMN session_version INT NOT NULL DEFAULT 0",
    "ALTER TABLE profiles ADD COLUMN require_two_step BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE profiles ADD COLUMN invited_by UUID REFERENCES profiles(id) ON DELETE SET NULL",
    "ALTER TABLE profiles ADD COLUMN reporting_paused_until TIMESTAMPTZ",
    # The longest-serving admin becomes the first super admin.
    """
    UPDATE profiles SET role = 'super_admin'
    WHERE id = (SELECT id FROM profiles WHERE role = 'admin' ORDER BY created_at LIMIT 1)
      AND NOT EXISTS (SELECT 1 FROM profiles WHERE role = 'super_admin')
    """,
    # Partners and staff
    """
    CREATE TABLE partners (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL DEFAULT 'other'
            CHECK (kind IN ('police','government','security_group','ngo','media','other')),
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE profiles ADD COLUMN partner_id INT REFERENCES partners(id) ON DELETE SET NULL",
    """
    CREATE TABLE staff_scopes (
        id BIGSERIAL PRIMARY KEY,
        profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
        partner_id INT REFERENCES partners(id) ON DELETE CASCADE,
        lga_id INT REFERENCES lgas(id) ON DELETE CASCADE,
        ward_id INT REFERENCES wards(id) ON DELETE CASCADE,
        CHECK (profile_id IS NOT NULL OR partner_id IS NOT NULL),
        CHECK (lga_id IS NOT NULL OR ward_id IS NOT NULL)
    )
    """,
    "CREATE INDEX idx_staff_scopes_profile ON staff_scopes (profile_id)",
    "CREATE INDEX idx_staff_scopes_partner ON staff_scopes (partner_id)",
    """
    CREATE TABLE staff_logins (
        id BIGSERIAL PRIMARY KEY,
        profile_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
        email TEXT,
        outcome TEXT NOT NULL,
        ip TEXT,
        user_agent TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_staff_logins_profile ON staff_logins (profile_id, created_at DESC)",
    "CREATE INDEX idx_staff_logins_time ON staff_logins (created_at DESC)",
    # Audit chain. The actor's name and role are copied into the entry, so deleting an
    # account (which clears actor_id) leaves the hashed record intact.
    "ALTER TABLE audit_log ADD COLUMN actor_label TEXT",
    "ALTER TABLE audit_log ADD COLUMN chain_seq BIGINT UNIQUE",
    "ALTER TABLE audit_log ADD COLUMN prev_hash TEXT",
    "ALTER TABLE audit_log ADD COLUMN hash TEXT",
    """
    CREATE FUNCTION audit_entry_hash(
        prev TEXT, seq BIGINT, created TIMESTAMPTZ, actor TEXT, action TEXT, etype TEXT,
        eid TEXT, reason TEXT, meta JSONB, ip INET
    ) RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
        SELECT encode(sha256(convert_to(jsonb_build_array(
            prev, seq, extract(epoch FROM created)::text, actor, action, etype, eid, reason,
            meta, host(ip)
        )::text, 'UTF8')), 'hex')
    $$
    """,
    """
    CREATE FUNCTION audit_actor_label(actor UUID) RETURNS TEXT LANGUAGE sql STABLE AS $$
        SELECT COALESCE(display_name, email, phone, id::text) || ' (' || role || ')'
        FROM profiles WHERE id = actor
    $$
    """,
    """
    DO $$
    DECLARE
        entry RECORD;
        previous TEXT := 'genesis';
        seq BIGINT := 0;
    BEGIN
        FOR entry IN SELECT * FROM audit_log ORDER BY id LOOP
            seq := seq + 1;
            UPDATE audit_log SET
                chain_seq = seq,
                actor_label = audit_actor_label(entry.actor_id),
                prev_hash = previous,
                hash = audit_entry_hash(previous, seq, entry.created_at,
                    audit_actor_label(entry.actor_id), entry.action, entry.entity_type,
                    entry.entity_id, entry.reason, entry.metadata, entry.ip)
            WHERE id = entry.id
            RETURNING hash INTO previous;
        END LOOP;
    END $$
    """,
    """
    CREATE FUNCTION audit_chain() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        last RECORD;
    BEGIN
        -- One writer at a time, so every entry links to the one before it.
        PERFORM pg_advisory_xact_lock(7243001);
        SELECT chain_seq, hash INTO last FROM audit_log ORDER BY chain_seq DESC NULLS LAST LIMIT 1;
        IF NEW.actor_label IS NULL AND NEW.actor_id IS NOT NULL THEN
            NEW.actor_label := audit_actor_label(NEW.actor_id);
        END IF;
        NEW.chain_seq := COALESCE(last.chain_seq, 0) + 1;
        NEW.prev_hash := COALESCE(last.hash, 'genesis');
        NEW.hash := audit_entry_hash(NEW.prev_hash, NEW.chain_seq, NEW.created_at,
            NEW.actor_label, NEW.action, NEW.entity_type, NEW.entity_id, NEW.reason,
            NEW.metadata, NEW.ip);
        RETURN NEW;
    END $$
    """,
    "CREATE TRIGGER audit_chain BEFORE INSERT ON audit_log "
    "FOR EACH ROW EXECUTE FUNCTION audit_chain()",
    """
    CREATE FUNCTION audit_protect() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            -- Retention may remove entries older than 24 months, oldest first.
            IF OLD.created_at < NOW() - INTERVAL '24 months' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit_log is append-only';
        END IF;
        -- Deleting an account clears actor_id; nothing else may change.
        IF (NEW.chain_seq, NEW.hash, NEW.prev_hash, NEW.actor_label, NEW.action,
            NEW.entity_type, NEW.entity_id, NEW.reason, NEW.metadata, NEW.ip, NEW.created_at)
           IS DISTINCT FROM
           (OLD.chain_seq, OLD.hash, OLD.prev_hash, OLD.actor_label, OLD.action,
            OLD.entity_type, OLD.entity_id, OLD.reason, OLD.metadata, OLD.ip, OLD.created_at)
        THEN
            RAISE EXCEPTION 'audit_log is append-only';
        END IF;
        RETURN NEW;
    END $$
    """,
    "CREATE TRIGGER audit_protect BEFORE UPDATE OR DELETE ON audit_log "
    "FOR EACH ROW EXECUTE FUNCTION audit_protect()",
    "CREATE INDEX idx_audit_actor ON audit_log (actor_id, created_at DESC)",
    "CREATE INDEX idx_audit_action ON audit_log (action)",
    # Moderation
    "ALTER TABLE incidents ADD COLUMN claimed_by UUID REFERENCES profiles(id) ON DELETE SET NULL",
    "ALTER TABLE incidents ADD COLUMN claimed_at TIMESTAMPTZ",
    "ALTER TABLE incidents ADD COLUMN merged_into UUID REFERENCES incidents(id) ON DELETE SET NULL",
    "ALTER TABLE incidents ADD COLUMN hold_reason TEXT",
    "CREATE INDEX idx_incidents_merged ON incidents (merged_into) WHERE merged_into IS NOT NULL",
    """
    CREATE TABLE incident_notes (
        id BIGSERIAL PRIMARY KEY,
        incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        author_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
        body TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 2000),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_incident_notes ON incident_notes (incident_id, created_at)",
    """
    CREATE TABLE incident_updates (
        id BIGSERIAL PRIMARY KEY,
        incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        author_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
        author_label TEXT NOT NULL,
        body TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 500),
        is_hidden BOOLEAN NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_incident_updates ON incident_updates (incident_id, created_at)",
    """
    CREATE TABLE user_sanctions (
        id BIGSERIAL PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('suspend','pause_reporting')),
        reason TEXT NOT NULL,
        ends_at TIMESTAMPTZ,
        created_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        lifted_at TIMESTAMPTZ,
        lifted_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
        appeal_text TEXT CHECK (char_length(appeal_text) <= 2000),
        appeal_status TEXT NOT NULL DEFAULT 'none'
            CHECK (appeal_status IN ('none','pending','accepted','rejected')),
        appeal_at TIMESTAMPTZ,
        appeal_decided_by UUID REFERENCES profiles(id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX idx_sanctions_user ON user_sanctions (user_id, created_at DESC)",
    """
    CREATE TABLE reputation_changes (
        id BIGSERIAL PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        change INT NOT NULL,
        reason TEXT NOT NULL,
        actor_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_reputation_changes ON reputation_changes (user_id, created_at DESC)",
    # Communication
    """
    CREATE TABLE broadcasts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 80),
        body TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 480),
        severity TEXT NOT NULL CHECK (severity IN ('low','medium','high','critical')),
        lga_ids INT[] NOT NULL DEFAULT '{}',
        ward_ids INT[] NOT NULL DEFAULT '{}',
        channels TEXT[] NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','approved','rejected','sending','sent','failed')),
        created_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
        decided_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
        decision_reason TEXT,
        recipients INT NOT NULL DEFAULT 0,
        push_sent INT NOT NULL DEFAULT 0,
        push_failed INT NOT NULL DEFAULT 0,
        sms_sent INT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        decided_at TIMESTAMPTZ,
        sent_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE announcements (
        id BIGSERIAL PRIMARY KEY,
        title TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 80),
        body TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 400),
        lga_id INT REFERENCES lgas(id) ON DELETE CASCADE,
        starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        ends_at TIMESTAMPTZ,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # Network
    """
    CREATE TABLE webhooks (
        id BIGSERIAL PRIMARY KEY,
        partner_id INT NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
        url TEXT NOT NULL CHECK (url LIKE 'https://%'),
        secret_enc TEXT NOT NULL,
        events TEXT[] NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE webhook_deliveries (
        id BIGSERIAL PRIMARY KEY,
        webhook_id BIGINT NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
        event TEXT NOT NULL,
        payload JSONB NOT NULL,
        status_code INT,
        error TEXT,
        attempts INT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        delivered_at TIMESTAMPTZ
    )
    """,
    "CREATE INDEX idx_webhook_deliveries ON webhook_deliveries (webhook_id, created_at DESC)",
    """
    CREATE TABLE verifier_applications (
        id BIGSERIAL PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        full_name TEXT NOT NULL CHECK (char_length(full_name) BETWEEN 2 AND 100),
        community_role TEXT NOT NULL CHECK (char_length(community_role) BETWEEN 2 AND 100),
        lga_id INT NOT NULL REFERENCES lgas(id),
        ward_id INT REFERENCES wards(id),
        reason TEXT NOT NULL CHECK (char_length(reason) BETWEEN 20 AND 1000),
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','approved','rejected')),
        reviewed_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
        reviewed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE UNIQUE INDEX uq_verifier_application_pending ON verifier_applications (user_id) "
    "WHERE status = 'pending'",
    # People
    """
    CREATE TABLE support_tickets (
        id BIGSERIAL PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        category TEXT NOT NULL CHECK (category IN ('bug','question','abuse','idea')),
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','waiting','closed')),
        app_info TEXT,
        assigned_to UUID REFERENCES profiles(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_support_tickets ON support_tickets (status, updated_at DESC)",
    """
    CREATE TABLE support_messages (
        id BIGSERIAL PRIMARY KEY,
        ticket_id BIGINT NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
        author_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
        from_staff BOOLEAN NOT NULL,
        is_internal BOOLEAN NOT NULL DEFAULT false,
        body TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 4000),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_support_messages ON support_messages (ticket_id, created_at)",
    """
    CREATE TABLE privacy_requests (
        id BIGSERIAL PRIMARY KEY,
        user_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
        contact TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('export','delete','correct')),
        details TEXT,
        status TEXT NOT NULL DEFAULT 'new'
            CHECK (status IN ('new','in_progress','done','rejected')),
        received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        due_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '30 days',
        handled_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
        done_at TIMESTAMPTZ,
        notes TEXT
    )
    """,
    # App
    """
    CREATE TABLE watch_zones (
        id BIGSERIAL PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 40),
        kind TEXT NOT NULL DEFAULT 'other' CHECK (kind IN ('home','work','school','other')),
        point GEOGRAPHY(POINT, 4326) NOT NULL,
        radius_m INT NOT NULL CHECK (radius_m BETWEEN 200 AND 10000),
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_watch_zones_point ON watch_zones USING GIST (point) WHERE is_active",
    "CREATE INDEX idx_watch_zones_user ON watch_zones (user_id)",
    "ALTER TABLE incident_types ADD COLUMN icon TEXT NOT NULL DEFAULT 'alert'",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)
    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, anon, authenticated")


def downgrade() -> None:
    op.execute("ALTER TABLE incident_types DROP COLUMN icon")
    for table in reversed(NEW_TABLES):
        if table == "partners":
            op.execute("ALTER TABLE profiles DROP COLUMN partner_id")
        op.execute(f"DROP TABLE {table}")
    for column in ("hold_reason", "merged_into", "claimed_at", "claimed_by"):
        op.execute(f"ALTER TABLE incidents DROP COLUMN {column}")
    op.execute("DROP TRIGGER audit_protect ON audit_log")
    op.execute("DROP TRIGGER audit_chain ON audit_log")
    op.execute("DROP FUNCTION audit_protect()")
    op.execute("DROP FUNCTION audit_chain()")
    op.execute("DROP INDEX idx_audit_actor")
    op.execute("DROP INDEX idx_audit_action")
    for column in ("hash", "prev_hash", "chain_seq", "actor_label"):
        op.execute(f"ALTER TABLE audit_log DROP COLUMN {column}")
    op.execute("DROP FUNCTION audit_actor_label(UUID)")
    op.execute(
        "DROP FUNCTION audit_entry_hash(TEXT, BIGINT, TIMESTAMPTZ, TEXT, TEXT, TEXT, TEXT, "
        "TEXT, JSONB, INET)"
    )
    op.execute("UPDATE profiles SET role = 'admin' WHERE role = 'super_admin'")
    op.execute("UPDATE profiles SET role = 'member' WHERE role = 'partner'")
    for column in ("reporting_paused_until", "invited_by", "require_two_step", "session_version"):
        op.execute(f"ALTER TABLE profiles DROP COLUMN {column}")
    op.execute("ALTER TABLE profiles DROP CONSTRAINT profiles_role_check")
    op.execute(
        "ALTER TABLE profiles ADD CONSTRAINT profiles_role_check CHECK (role IN "
        "('member','verifier','moderator','admin'))"
    )
