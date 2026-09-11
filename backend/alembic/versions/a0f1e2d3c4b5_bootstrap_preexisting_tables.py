"""bootstrap_preexisting_tables

Revision ID: a0f1e2d3c4b5
Revises:
Create Date: 2026-09-04 00:00:00.000000

Makes the migration chain able to build a database from nothing.

`meetings` and `meeting_chunks` were originally created out-of-band (by hand
in the Supabase SQL editor),
so the first tracked migration, 475e9ee29ad9, assumes they already exist: it
opens with `TRUNCATE TABLE meeting_chunks`, then ALTERs columns on both
tables and drops an index. Against a genuinely empty database that fails on
its first statement, which meant `alembic upgrade head` could only ever be
used to bring the one pre-existing production database forward - never to
stand up a new environment (fresh staging, a local dev DB, disaster
recovery).

This migration is inserted *before* 475e9ee29ad9 and recreates those two
tables in the exact shape they had immediately before it ran - TEXT columns,
vector(1536) embeddings, and the meeting_chunks_meeting_id_idx index that
475e9ee29ad9 goes on to drop. The later migrations then transform them into
the current schema exactly as they did historically, so a fresh database
converges on the same result as production rather than a parallel one.

Everything is IF NOT EXISTS, so against the existing production database
this is a no-op. (In practice it never even executes there: it is an
ancestor of the recorded revision, so Alembic treats it as already applied.)
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a0f1e2d3c4b5'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # meeting_chunks.embedding needs this before the table can be created.
    # 475e9ee29ad9 also runs it (harmlessly) for the production path.
    op.execute('CREATE EXTENSION IF NOT EXISTS vector;')

    # Shape mirrors the original hand-run SQL - deliberately without
    # user_id/title/embedding_provider/scheduled_at/calendar_event_id/
    # updated_at, which later migrations add.
    op.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_url TEXT NOT NULL,
            platform TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            recording_url TEXT,
            duration_seconds INT,
            error_message TEXT,
            transcript JSONB,
            created_at TIMESTAMPTZ DEFAULT now()
        );
    """)

    # TEXT columns and vector(1536) are intentional: 475e9ee29ad9 ALTERs them
    # to String/vector(768), and would fail against columns already in the
    # final shape (its USING clause casts vector(1536) -> vector(768)).
    op.execute("""
        CREATE TABLE IF NOT EXISTS meeting_chunks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_id UUID NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            speakers TEXT[],
            timestamp_start TEXT,
            timestamp_end TEXT,
            embedding VECTOR(1536) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        );
    """)

    # Recreated only so 475e9ee29ad9's drop_index() has something to drop.
    # The current equivalent is added back later by c4e7a9b1f2d3.
    op.execute("""
        CREATE INDEX IF NOT EXISTS meeting_chunks_meeting_id_idx
        ON meeting_chunks (meeting_id);
    """)


def downgrade() -> None:
    # Intentionally a no-op. These tables hold real user data and predate the
    # migration chain; this revision documents and bootstraps them, it does
    # not own their lifecycle. Dropping them on a downgrade would destroy
    # every meeting and transcript chunk in the database.
    pass
