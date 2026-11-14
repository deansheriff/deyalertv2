"""Community follow-ups on reports, including confirmed ones. They share the public updates
table with verifier and staff updates; the poster is stored but never shown.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UPGRADE = [
    "ALTER TABLE incident_updates ADD COLUMN kind TEXT NOT NULL DEFAULT 'official' "
    "CHECK (kind IN ('official','community'))",
    "ALTER TABLE incident_updates ADD COLUMN hold_reason TEXT",
    "CREATE INDEX idx_incident_updates_author ON incident_updates (author_id, created_at DESC)",
    "CREATE INDEX idx_incident_updates_held ON incident_updates (created_at) "
    "WHERE hold_reason IS NOT NULL AND is_hidden",
]

DOWNGRADE = [
    "DELETE FROM incident_updates WHERE kind = 'community'",
    "DROP INDEX idx_incident_updates_held",
    "DROP INDEX idx_incident_updates_author",
    "ALTER TABLE incident_updates DROP COLUMN hold_reason",
    "ALTER TABLE incident_updates DROP COLUMN kind",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
