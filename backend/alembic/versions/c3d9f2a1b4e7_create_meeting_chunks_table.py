"""create_meeting_chunks_table

Revision ID: c3d9f2a1b4e7
Revises: 8f2c1a9d4b6e
Create Date: 2026-08-21 00:00:00.000000

This table already exists in production - it was created out-of-band
and has no tracked CREATE TABLE anywhere in history; 475e9ee29ad9
only ever ALTERs it. This migration exists to bring the live schema
under version control. It uses IF NOT EXISTS throughout, so it is a
no-op against the existing production database and only actually
creates anything against a fresh database that never had this table
(e.g. a new environment bootstrapped from this repo).

Column and index shapes match backend/app/db/models.py:40-53 and the
post-475e9ee29ad9 live schema (content/speakers/timestamp_* were
altered from TEXT to character varying, embedding from vector(1536)
to vector(768) - see that migration for the history). The
meeting_chunks_meeting_id_idx index that 475e9ee29ad9 dropped is
intentionally not recreated here, to match the live schema as-is.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3d9f2a1b4e7'
down_revision: Union[str, None] = '8f2c1a9d4b6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS meeting_chunks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_id UUID NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content CHARACTER VARYING NOT NULL,
            speakers CHARACTER VARYING[],
            timestamp_start CHARACTER VARYING,
            timestamp_end CHARACTER VARYING,
            embedding VECTOR(768) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS meeting_chunks_embedding_idx
        ON meeting_chunks
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = '100');
    """)


def downgrade() -> None:
    # Intentionally a no-op: this table predates this migration and
    # holds live production data (meeting transcript chunks). This
    # migration documents its schema, it does not own its lifecycle -
    # dropping it here would destroy real data on a downgrade.
    pass
