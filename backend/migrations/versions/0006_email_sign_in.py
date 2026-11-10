"""Email sign-in: profiles keep the account email, and admins change platform settings
(phone sign-in switch, SMTP) in a server-only table.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UPGRADE = [
    "ALTER TABLE profiles ADD COLUMN email TEXT UNIQUE",
    """
    CREATE TABLE app_settings (
        key TEXT PRIMARY KEY,
        value JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_by UUID REFERENCES profiles(id) ON DELETE SET NULL
    )
    """,
    "ALTER TABLE app_settings ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON app_settings FROM PUBLIC, anon, authenticated",
]

DOWNGRADE = [
    "DROP TABLE app_settings",
    "ALTER TABLE profiles DROP COLUMN email",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
