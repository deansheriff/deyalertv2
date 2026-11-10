"""Core v2 schema, incident type seeds, and housekeeping triggers.

Foreign keys to profiles are CASCADE or SET NULL throughout so that deleting an
account removes the person and anonymises what they authored (plan section 4.4).

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# asyncpg runs one statement per execute, so each statement is a separate entry.
UPGRADE = [
    "CREATE EXTENSION IF NOT EXISTS postgis",
    # Reference: administrative areas
    """
    CREATE TABLE lgas (
        id SERIAL PRIMARY KEY,
        state TEXT NOT NULL,
        name TEXT NOT NULL,
        inec_code TEXT,
        geom GEOMETRY(MultiPolygon, 4326),
        UNIQUE (state, name)
    )
    """,
    """
    CREATE TABLE wards (
        id SERIAL PRIMARY KEY,
        lga_id INT NOT NULL REFERENCES lgas(id),
        name TEXT NOT NULL,
        geom GEOMETRY(MultiPolygon, 4326),
        UNIQUE (lga_id, name)
    )
    """,
    "CREATE INDEX idx_lgas_geom ON lgas USING GIST (geom)",
    "CREATE INDEX idx_wards_geom ON wards USING GIST (geom)",
    # Reference: incident types
    """
    CREATE TABLE incident_types (
        code TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        default_severity TEXT NOT NULL
            CHECK (default_severity IN ('low','medium','high','critical')),
        ttl_minutes INT NOT NULL,
        sensitive BOOLEAN NOT NULL DEFAULT false,
        is_active BOOLEAN NOT NULL DEFAULT true
    )
    """,
    """
    INSERT INTO incident_types (code, label, default_severity, ttl_minutes, sensitive) VALUES
        ('kidnapping','Kidnapping','critical',1440,true),
        ('armed_robbery','Armed robbery','high',720,false),
        ('banditry','Banditry','critical',1440,false),
        ('cult_clash','Cult clash','high',720,false),
        ('gunshots','Gunshots heard','high',360,false),
        ('mob_action','Mob action','high',360,false),
        ('roadblock','Roadblock','medium',180,false),
        ('fire_outbreak','Fire outbreak','high',360,false),
        ('flooding','Flooding','medium',720,false),
        ('accident','Accident','medium',180,false),
        ('suspicious_activity','Suspicious activity','low',180,false),
        ('other','Other','low',360,false)
    """,
    # Users
    """
    CREATE TABLE profiles (
        id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
        phone TEXT UNIQUE NOT NULL,
        display_name TEXT,
        role TEXT NOT NULL DEFAULT 'member'
            CHECK (role IN ('member','verifier','moderator','admin')),
        language TEXT NOT NULL DEFAULT 'en' CHECK (language IN ('en','pcm','ha','yo','ig')),
        home_point GEOGRAPHY(POINT, 4326),
        home_lga_id INT REFERENCES lgas(id),
        home_ward_id INT REFERENCES wards(id),
        alert_radius_km NUMERIC(4,1) NOT NULL DEFAULT 5.0
            CHECK (alert_radius_km BETWEEN 1 AND 20),
        location_precision TEXT NOT NULL DEFAULT 'exact'
            CHECK (location_precision IN ('exact','ward','lga')),
        reputation_score INT NOT NULL DEFAULT 0,
        quiet_hours_start TIME,
        quiet_hours_end TIME,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE devices (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        fcm_token TEXT UNIQUE NOT NULL,
        platform TEXT NOT NULL CHECK (platform IN ('android','ios')),
        device_fingerprint TEXT,
        app_version TEXT,
        last_location GEOGRAPHY(POINT, 4326),
        last_location_at TIMESTAMPTZ,
        alert_point GEOGRAPHY(POINT, 4326),
        is_active BOOLEAN NOT NULL DEFAULT true,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_devices_alert_point ON devices USING GIST (alert_point) WHERE is_active",
    "CREATE INDEX idx_devices_user ON devices (user_id)",
    """
    CREATE TABLE trusted_contacts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        relationship TEXT,
        consent_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (consent_status IN ('pending','accepted','declined','revoked')),
        consent_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, phone)
    )
    """,
    """
    CREATE TABLE verifiers (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        lga_id INT NOT NULL REFERENCES lgas(id),
        ward_id INT REFERENCES wards(id),
        title TEXT,
        approved_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE UNIQUE INDEX uq_verifier_scope ON verifiers (user_id, lga_id, COALESCE(ward_id, 0))",
    # Incidents
    """
    CREATE TABLE incidents (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id UUID NOT NULL UNIQUE,
        type_code TEXT NOT NULL REFERENCES incident_types(code),
        description TEXT CHECK (char_length(description) <= 1000),
        location GEOGRAPHY(POINT, 4326) NOT NULL,
        public_location GEOGRAPHY(POINT, 4326) NOT NULL,
        location_name TEXT,
        lga_id INT REFERENCES lgas(id),
        ward_id INT REFERENCES wards(id),
        status TEXT NOT NULL DEFAULT 'unconfirmed'
            CHECK (status IN ('unconfirmed','corroborated','confirmed','under_review',
                              'resolved','expired','false_report')),
        severity TEXT NOT NULL DEFAULT 'medium'
            CHECK (severity IN ('low','medium','high','critical')),
        severity_set_by TEXT NOT NULL DEFAULT 'system'
            CHECK (severity_set_by IN ('system','reporter','verifier')),
        is_anonymous BOOLEAN NOT NULL DEFAULT false,
        corroboration_count INT NOT NULL DEFAULT 0,
        flag_weight NUMERIC(6,2) NOT NULL DEFAULT 0,
        is_hidden BOOLEAN NOT NULL DEFAULT false,
        hidden_reason TEXT,
        occurred_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ NOT NULL,
        last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        confirmed_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
        confirmed_at TIMESTAMPTZ,
        resolved_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_incidents_location ON incidents USING GIST (location)",
    "CREATE INDEX idx_incidents_public_location ON incidents USING GIST (public_location)",
    "CREATE INDEX idx_incidents_created_at ON incidents (created_at DESC)",
    """
    CREATE INDEX idx_incidents_expires_at ON incidents (expires_at)
        WHERE status NOT IN ('expired','resolved','false_report')
    """,
    "CREATE INDEX idx_incidents_ward ON incidents (ward_id, created_at DESC)",
    """
    CREATE TABLE incident_reporters (
        incident_id UUID PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE,
        reporter_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_incident_reporters_user ON incident_reporters (reporter_id)",
    """
    CREATE TABLE incident_media (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('image','video')),
        storage_path TEXT NOT NULL,
        thumb_path TEXT,
        width INT,
        height INT,
        size_bytes INT,
        moderation_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (moderation_status IN ('pending','approved','blurred','rejected')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE incident_events (
        id BIGSERIAL PRIMARY KEY,
        incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        actor_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
        event_type TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT,
        note TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_incident_events_incident ON incident_events (incident_id, created_at)",
    """
    CREATE TABLE corroborations (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        user_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
        device_id UUID REFERENCES devices(id) ON DELETE SET NULL,
        location GEOGRAPHY(POINT, 4326) NOT NULL,
        distance_m INT NOT NULL,
        weight NUMERIC(4,2) NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (incident_id, user_id)
    )
    """,
    """
    CREATE TABLE incident_flags (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        flagged_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
        reason TEXT NOT NULL
            CHECK (reason IN ('false_report','duplicate','spam','inappropriate',
                              'names_individual')),
        notes TEXT,
        weight NUMERIC(4,2) NOT NULL DEFAULT 1,
        outcome TEXT CHECK (outcome IN ('upheld','rejected')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (incident_id, flagged_by)
    )
    """,
    # SOS
    """
    CREATE TABLE sos_alerts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id UUID NOT NULL UNIQUE,
        user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        start_location GEOGRAPHY(POINT, 4326),
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('pending_cancel','active','resolved','cancelled','false_alarm')),
        share_token_hash TEXT,
        share_expires_at TIMESTAMPTZ,
        checkin_due_at TIMESTAMPTZ,
        triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        resolved_at TIMESTAMPTZ,
        resolved_by UUID REFERENCES profiles(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE sos_location_pings (
        id BIGSERIAL PRIMARY KEY,
        sos_id UUID NOT NULL REFERENCES sos_alerts(id) ON DELETE CASCADE,
        location GEOGRAPHY(POINT, 4326) NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE sos_deliveries (
        id BIGSERIAL PRIMARY KEY,
        sos_id UUID NOT NULL REFERENCES sos_alerts(id) ON DELETE CASCADE,
        contact_id UUID REFERENCES trusted_contacts(id) ON DELETE SET NULL,
        recipient_user_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
        channel TEXT NOT NULL CHECK (channel IN ('sms','push')),
        status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued','sent','delivered','failed')),
        provider_message_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # Notifications
    """
    CREATE TABLE notifications (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        incident_id UUID REFERENCES incidents(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT,
        read_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_notifications_user ON notifications (user_id, created_at DESC)",
    """
    CREATE TABLE notification_log (
        user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        channel TEXT NOT NULL,
        sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, incident_id, kind, channel)
    )
    """,
    # Audit
    """
    CREATE TABLE audit_log (
        id BIGSERIAL PRIMARY KEY,
        actor_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
        action TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT,
        reason TEXT,
        metadata JSONB,
        ip INET,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # Housekeeping
    """
    CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        NEW.updated_at := NOW();
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER trg_profiles_updated_at BEFORE UPDATE ON profiles
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """,
    """
    CREATE TRIGGER trg_incidents_updated_at BEFORE UPDATE ON incidents
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """,
    """
    CREATE FUNCTION set_device_alert_point() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        NEW.alert_point := COALESCE(
            NEW.last_location,
            (SELECT home_point FROM profiles WHERE id = NEW.user_id)
        );
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER trg_devices_alert_point BEFORE INSERT OR UPDATE OF last_location ON devices
        FOR EACH ROW EXECUTE FUNCTION set_device_alert_point()
    """,
    """
    CREATE FUNCTION sync_device_home_point() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        UPDATE devices SET alert_point = NEW.home_point
        WHERE user_id = NEW.id AND last_location IS NULL;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER trg_profiles_home_point AFTER UPDATE OF home_point ON profiles
        FOR EACH ROW EXECUTE FUNCTION sync_device_home_point()
    """,
]

TABLES_REVERSED = [
    "audit_log",
    "notification_log",
    "notifications",
    "sos_deliveries",
    "sos_location_pings",
    "sos_alerts",
    "incident_flags",
    "corroborations",
    "incident_events",
    "incident_media",
    "incident_reporters",
    "incidents",
    "verifiers",
    "trusted_contacts",
    "devices",
    "profiles",
    "incident_types",
    "wards",
    "lgas",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for table in TABLES_REVERSED:
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION sync_device_home_point()")
    op.execute("DROP FUNCTION set_device_alert_point()")
    op.execute("DROP FUNCTION set_updated_at()")
