"""enable_rls_and_policies

Revision ID: e6a4d8f0b2c1
Revises: c3d9f2a1b4e7
Create Date: 2026-08-21 00:00:00.000000

RLS was applied by hand in the Supabase dashboard on 2026-08-21 (see
docs/rls-audit.md) - this migration documents that change in version
control rather than introducing new behavior. Every statement is
idempotent (ENABLE ROW LEVEL SECURITY is already a no-op if RLS is
already enabled; policies are dropped-and-recreated since Postgres
has no CREATE POLICY IF NOT EXISTS), so this is safe to run against
the already-configured production database as well as a fresh one.

Note: the FastAPI backend's own DB access - the Supabase service-role
client and the direct Postgres connection (DB-owner role via the
Supabase pooler) - bypasses RLS unconditionally either way (see
docs/rls-audit.md, Part B). These policies are defense-in-depth for
any other client (anon/authenticated Supabase roles), not something
the backend itself relies on.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e6a4d8f0b2c1'
down_revision: Union[str, None] = 'c3d9f2a1b4e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MEETING_CHUNK_OWNERSHIP = """
    EXISTS (
        SELECT 1 FROM meetings m
        WHERE m.id = meeting_chunks.meeting_id
        AND m.user_id = auth.uid()
    )
"""


def upgrade() -> None:
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE meeting_chunks ENABLE ROW LEVEL SECURITY;")

    # users: a user may read/update only their own row. Row creation
    # is handled by the backend (service role, bypasses RLS) or a
    # Supabase auth trigger - not exposed to the authenticated role.
    op.execute("DROP POLICY IF EXISTS users_select_own ON users;")
    op.execute("""
        CREATE POLICY users_select_own ON users
        FOR SELECT
        USING (id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS users_update_own ON users;")
    op.execute("""
        CREATE POLICY users_update_own ON users
        FOR UPDATE
        USING (id = auth.uid())
        WITH CHECK (id = auth.uid());
    """)

    # meetings: full CRUD scoped to the owning user.
    op.execute("DROP POLICY IF EXISTS meetings_select_own ON meetings;")
    op.execute("""
        CREATE POLICY meetings_select_own ON meetings
        FOR SELECT
        USING (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS meetings_insert_own ON meetings;")
    op.execute("""
        CREATE POLICY meetings_insert_own ON meetings
        FOR INSERT
        WITH CHECK (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS meetings_update_own ON meetings;")
    op.execute("""
        CREATE POLICY meetings_update_own ON meetings
        FOR UPDATE
        USING (user_id = auth.uid())
        WITH CHECK (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS meetings_delete_own ON meetings;")
    op.execute("""
        CREATE POLICY meetings_delete_own ON meetings
        FOR DELETE
        USING (user_id = auth.uid());
    """)

    # meeting_chunks: no user_id column of its own - ownership is
    # derived by joining back to the parent meeting.
    op.execute("DROP POLICY IF EXISTS meeting_chunks_select_own ON meeting_chunks;")
    op.execute(f"""
        CREATE POLICY meeting_chunks_select_own ON meeting_chunks
        FOR SELECT
        USING ({_MEETING_CHUNK_OWNERSHIP});
    """)
    op.execute("DROP POLICY IF EXISTS meeting_chunks_insert_own ON meeting_chunks;")
    op.execute(f"""
        CREATE POLICY meeting_chunks_insert_own ON meeting_chunks
        FOR INSERT
        WITH CHECK ({_MEETING_CHUNK_OWNERSHIP});
    """)
    op.execute("DROP POLICY IF EXISTS meeting_chunks_update_own ON meeting_chunks;")
    op.execute(f"""
        CREATE POLICY meeting_chunks_update_own ON meeting_chunks
        FOR UPDATE
        USING ({_MEETING_CHUNK_OWNERSHIP})
        WITH CHECK ({_MEETING_CHUNK_OWNERSHIP});
    """)
    op.execute("DROP POLICY IF EXISTS meeting_chunks_delete_own ON meeting_chunks;")
    op.execute(f"""
        CREATE POLICY meeting_chunks_delete_own ON meeting_chunks
        FOR DELETE
        USING ({_MEETING_CHUNK_OWNERSHIP});
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS meeting_chunks_delete_own ON meeting_chunks;")
    op.execute("DROP POLICY IF EXISTS meeting_chunks_update_own ON meeting_chunks;")
    op.execute("DROP POLICY IF EXISTS meeting_chunks_insert_own ON meeting_chunks;")
    op.execute("DROP POLICY IF EXISTS meeting_chunks_select_own ON meeting_chunks;")
    op.execute("DROP POLICY IF EXISTS meetings_delete_own ON meetings;")
    op.execute("DROP POLICY IF EXISTS meetings_update_own ON meetings;")
    op.execute("DROP POLICY IF EXISTS meetings_insert_own ON meetings;")
    op.execute("DROP POLICY IF EXISTS meetings_select_own ON meetings;")
    op.execute("DROP POLICY IF EXISTS users_update_own ON users;")
    op.execute("DROP POLICY IF EXISTS users_select_own ON users;")
    op.execute("ALTER TABLE meeting_chunks DISABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE meetings DISABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY;")
