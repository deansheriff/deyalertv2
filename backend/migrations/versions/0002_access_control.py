"""RLS, public views, and the feed RPC.

Supabase's default privileges grant ALL on new public tables and views to anon and
authenticated. The views below run with their owner's rights and are auto-updatable,
so every grant is revoked first and only the listed reads are given back.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = [
    "lgas",
    "wards",
    "incident_types",
    "profiles",
    "devices",
    "trusted_contacts",
    "verifiers",
    "incidents",
    "incident_reporters",
    "incident_media",
    "incident_events",
    "corroborations",
    "incident_flags",
    "sos_alerts",
    "sos_location_pings",
    "sos_deliveries",
    "notifications",
    "notification_log",
    "audit_log",
]

VIEWS = ["incidents_public", "incident_events_public", "incident_media_public"]

CREATE_OBJECTS = [
    """
    CREATE VIEW incidents_public AS
    SELECT i.id, i.type_code, i.description,
           i.public_location AS location,
           ST_Y(i.public_location::geometry) AS lat,
           ST_X(i.public_location::geometry) AS lng,
           i.location_name, i.lga_id, i.ward_id, i.status, i.severity,
           i.corroboration_count, i.occurred_at, i.created_at, i.expires_at,
           i.last_activity_at, i.confirmed_at, i.resolved_at
    FROM incidents i
    WHERE i.is_hidden = false
      AND i.status NOT IN ('false_report','under_review')
      AND i.created_at > NOW() - INTERVAL '7 days'
    """,
    # No actor_id or note: the 'created' event's actor is the reporter.
    """
    CREATE VIEW incident_events_public AS
    SELECT e.id, e.incident_id, e.event_type, e.from_status, e.to_status, e.created_at
    FROM incident_events e
    JOIN incidents_public p ON p.id = e.incident_id
    WHERE e.event_type IN ('created','corroborated','confirmed','extended','resolved','expired')
    """,
    """
    CREATE VIEW incident_media_public AS
    SELECT m.id, m.incident_id, m.kind, m.storage_path, m.thumb_path, m.width, m.height,
           m.moderation_status, m.created_at
    FROM incident_media m
    JOIN incidents_public p ON p.id = m.incident_id
    WHERE m.moderation_status IN ('approved','blurred')
    """,
    # The live feed drops expired and resolved incidents, including any the expiry job
    # has not reached yet. History is opt-in.
    """
    CREATE FUNCTION nearby_incidents(
        p_lat DOUBLE PRECISION,
        p_lng DOUBLE PRECISION,
        p_radius_m INT DEFAULT 5000,
        p_since TIMESTAMPTZ DEFAULT NOW() - INTERVAL '24 hours',
        p_include_history BOOLEAN DEFAULT false
    )
    RETURNS SETOF incidents_public
    LANGUAGE sql STABLE AS $$
        SELECT v.* FROM incidents_public v
        WHERE v.created_at >= p_since
          AND (p_include_history
               OR (v.status NOT IN ('expired','resolved') AND v.expires_at > NOW()))
          AND ST_DWithin(v.location, ST_SetSRID(ST_MakePoint(p_lng, p_lat), 4326)::geography,
                         LEAST(p_radius_m, 20000))
        ORDER BY v.created_at DESC
        LIMIT 200;
    $$
    """,
]

GRANTS_AND_POLICIES = [
    "GRANT SELECT ON incidents_public, incident_events_public, incident_media_public "
    "TO authenticated",
    "REVOKE ALL ON FUNCTION nearby_incidents(DOUBLE PRECISION, DOUBLE PRECISION, INT, "
    "TIMESTAMPTZ, BOOLEAN) FROM PUBLIC, anon",
    "GRANT EXECUTE ON FUNCTION nearby_incidents(DOUBLE PRECISION, DOUBLE PRECISION, INT, "
    "TIMESTAMPTZ, BOOLEAN) TO authenticated",
    "GRANT SELECT ON lgas, wards, incident_types TO authenticated",
    "CREATE POLICY lgas_read ON lgas FOR SELECT TO authenticated USING (true)",
    "CREATE POLICY wards_read ON wards FOR SELECT TO authenticated USING (true)",
    "CREATE POLICY incident_types_read ON incident_types FOR SELECT TO authenticated "
    "USING (is_active)",
    "GRANT SELECT ON profiles, trusted_contacts, sos_alerts, notifications TO authenticated",
    "CREATE POLICY profiles_self_read ON profiles FOR SELECT TO authenticated "
    "USING (id = auth.uid())",
    "CREATE POLICY contacts_self_read ON trusted_contacts FOR SELECT TO authenticated "
    "USING (user_id = auth.uid())",
    "CREATE POLICY sos_self_read ON sos_alerts FOR SELECT TO authenticated "
    "USING (user_id = auth.uid())",
    "CREATE POLICY notif_self_read ON notifications FOR SELECT TO authenticated "
    "USING (user_id = auth.uid())",
    "GRANT UPDATE (read_at) ON notifications TO authenticated",
    "CREATE POLICY notif_self_update ON notifications FOR UPDATE TO authenticated "
    "USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid())",
]


def upgrade() -> None:
    for statement in CREATE_OBJECTS:
        op.execute(statement)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, anon, authenticated")
    for view in VIEWS:
        op.execute(f"REVOKE ALL ON {view} FROM PUBLIC, anon, authenticated")
    for statement in GRANTS_AND_POLICIES:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION nearby_incidents(DOUBLE PRECISION, DOUBLE PRECISION, INT, "
        "TIMESTAMPTZ, BOOLEAN)"
    )
    for view in reversed(VIEWS):
        op.execute(f"DROP VIEW {view}")
    for table in TABLES:
        op.execute(
            f"""
            DO $$
            DECLARE policy_name TEXT;
            BEGIN
                FOR policy_name IN
                    SELECT policyname FROM pg_policies WHERE tablename = '{table}'
                LOOP
                    EXECUTE format('DROP POLICY %I ON {table}', policy_name);
                END LOOP;
            END;
            $$
            """
        )
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
