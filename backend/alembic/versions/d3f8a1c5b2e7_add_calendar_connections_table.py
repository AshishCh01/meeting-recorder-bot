"""add_calendar_connections_table

Revision ID: d3f8a1c5b2e7
Revises: b2c3d4e5f6a7
Create Date: 2026-08-24 00:00:00.000000

Stores one Google Calendar OAuth connection per user (see
docs/google-calendar-integration-plan.md). refresh_token_encrypted
holds a Fernet-encrypted refresh token - never plaintext - decrypted
on demand in app/services/google_oauth_service.py.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd3f8a1c5b2e7'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS calendar_connections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            google_email CHARACTER VARYING NOT NULL,
            refresh_token_encrypted CHARACTER VARYING NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        );
    """)

    # Same owner-only model as every other per-user table - see
    # e6a4d8f0b2c1_enable_rls_and_policies.py. The backend's own DB
    # access bypasses RLS regardless (service-role/pooler connection);
    # this is defense-in-depth for any other client.
    op.execute("ALTER TABLE calendar_connections ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS calendar_connections_select_own ON calendar_connections;")
    op.execute("""
        CREATE POLICY calendar_connections_select_own ON calendar_connections
        FOR SELECT
        USING (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS calendar_connections_insert_own ON calendar_connections;")
    op.execute("""
        CREATE POLICY calendar_connections_insert_own ON calendar_connections
        FOR INSERT
        WITH CHECK (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS calendar_connections_update_own ON calendar_connections;")
    op.execute("""
        CREATE POLICY calendar_connections_update_own ON calendar_connections
        FOR UPDATE
        USING (user_id = auth.uid())
        WITH CHECK (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS calendar_connections_delete_own ON calendar_connections;")
    op.execute("""
        CREATE POLICY calendar_connections_delete_own ON calendar_connections
        FOR DELETE
        USING (user_id = auth.uid());
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS calendar_connections;")
