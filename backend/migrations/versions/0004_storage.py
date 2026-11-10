"""Storage buckets and upload policies. Skipped where the Supabase storage schema is absent.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE = """
DO $$
BEGIN
    IF to_regclass('storage.buckets') IS NULL THEN
        RETURN;
    END IF;
    INSERT INTO storage.buckets (id, name, public) VALUES
        ('quarantine', 'quarantine', false),
        ('incident-media', 'incident-media', true)
    ON CONFLICT (id) DO NOTHING;
    DROP POLICY IF EXISTS quarantine_own_upload ON storage.objects;
    CREATE POLICY quarantine_own_upload ON storage.objects FOR INSERT TO authenticated
        WITH CHECK (
            bucket_id = 'quarantine'
            AND (storage.foldername(name))[1] = auth.uid()::text
        );
END;
$$
"""

DOWNGRADE = """
DO $$
BEGIN
    IF to_regclass('storage.objects') IS NOT NULL THEN
        DROP POLICY IF EXISTS quarantine_own_upload ON storage.objects;
    END IF;
END;
$$
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
