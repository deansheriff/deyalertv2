"""Staff accounts made for the admin panel sign in by email and have no phone number.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UPGRADE = ["ALTER TABLE profiles ALTER COLUMN phone DROP NOT NULL"]

# Fails while an email-only staff account exists; delete or give it a phone first.
DOWNGRADE = ["ALTER TABLE profiles ALTER COLUMN phone SET NOT NULL"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
