"""add_meeting_chunks_meeting_id_index

Revision ID: c4e7a9b1f2d3
Revises: e9c2b6a4f1d8
Create Date: 2026-09-04 00:00:00.000000

Restores a btree index on meeting_chunks.meeting_id, which 475e9ee29ad9
dropped (as meeting_chunks_meeting_id_idx) and c3d9f2a1b4e7 deliberately
did not recreate.

Why this matters: every RAG chat question runs

    SELECT ... FROM meeting_chunks
    WHERE meeting_id = :id
    ORDER BY embedding <=> :query_embedding
    LIMIT :top_k

(app/rag/tools.py, search_transcript). With no index on meeting_id, and
pgvector's ivfflat index offering no efficient pre-filter, Postgres had to
either scan the global ANN index across every meeting's chunks and
post-filter, or sequentially scan the whole table. Either way the cost
scaled with the total number of chunks in the system rather than with the
one meeting being queried - so chat latency grew as unrelated users' data
accumulated.

The index is composite on (meeting_id, chunk_index) rather than
meeting_id alone: the leading column serves the vector search above and
the DELETE ... WHERE meeting_id = :id in embedding_service.index_transcript,
while the trailing column additionally satisfies search_by_speaker's
ORDER BY chunk_index without a separate sort.

Created without CONCURRENTLY on purpose: the app connects through
Supabase's connection pooler, where CREATE INDEX CONCURRENTLY (which
cannot run inside a transaction block) is unreliable. meeting_chunks is
small enough that the brief write lock during creation is acceptable, and
writes to it only occur during post-transcription indexing.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4e7a9b1f2d3'
down_revision: Union[str, None] = 'e9c2b6a4f1d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS meeting_chunks_meeting_id_chunk_index_idx
        ON meeting_chunks (meeting_id, chunk_index);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS meeting_chunks_meeting_id_chunk_index_idx;")
