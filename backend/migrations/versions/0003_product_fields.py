"""Fields the API needs: reporter device, verifier label, SMS opt-in, contact consent token.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE = [
    "ALTER TABLE incident_reporters ADD COLUMN device_id UUID "
    "REFERENCES devices(id) ON DELETE SET NULL",
    "ALTER TABLE incidents ADD COLUMN confirmed_by_label TEXT",
    "ALTER TABLE profiles ADD COLUMN sms_alerts_opt_in BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE trusted_contacts ADD COLUMN consent_token_hash TEXT",
    "CREATE UNIQUE INDEX uq_contact_consent_token ON trusted_contacts (consent_token_hash)",
    "CREATE UNIQUE INDEX uq_sos_share_token ON sos_alerts (share_token_hash)",
    "CREATE INDEX idx_sos_checkin_due ON sos_alerts (checkin_due_at) "
    "WHERE status = 'pending_cancel'",
    """
    CREATE OR REPLACE VIEW incidents_public AS
    SELECT i.id, i.type_code, i.description,
           i.public_location AS location,
           ST_Y(i.public_location::geometry) AS lat,
           ST_X(i.public_location::geometry) AS lng,
           i.location_name, i.lga_id, i.ward_id, i.status, i.severity,
           i.corroboration_count, i.occurred_at, i.created_at, i.expires_at,
           i.last_activity_at, i.confirmed_at, i.resolved_at, i.confirmed_by_label
    FROM incidents i
    WHERE i.is_hidden = false
      AND i.status NOT IN ('false_report','under_review')
      AND i.created_at > NOW() - INTERVAL '7 days'
    """,
]

DOWNGRADE = [
    "DROP FUNCTION nearby_incidents(DOUBLE PRECISION, DOUBLE PRECISION, INT, "
    "TIMESTAMPTZ, BOOLEAN)",
    "DROP VIEW incident_media_public",
    "DROP VIEW incident_events_public",
    "DROP VIEW incidents_public",
    "DROP INDEX idx_sos_checkin_due",
    "DROP INDEX uq_sos_share_token",
    "DROP INDEX uq_contact_consent_token",
    "ALTER TABLE trusted_contacts DROP COLUMN consent_token_hash",
    "ALTER TABLE profiles DROP COLUMN sms_alerts_opt_in",
    "ALTER TABLE incidents DROP COLUMN confirmed_by_label",
    "ALTER TABLE incident_reporters DROP COLUMN device_id",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    from importlib import import_module

    for statement in DOWNGRADE:
        op.execute(statement)
    previous = import_module("migrations.versions.0002_access_control")
    for statement in previous.CREATE_OBJECTS:
        op.execute(statement)
    for view in previous.VIEWS:
        op.execute(f"REVOKE ALL ON {view} FROM PUBLIC, anon, authenticated")
    for statement in previous.GRANTS_AND_POLICIES[:3]:
        op.execute(statement)
