"""add_chat_messages_table

Revision ID: b8d2e6f4a1c9
Revises: c4e7a9b1f2d3
Create Date: 2026-09-07 00:00:00.000000

Persists the "Ask about this meeting" conversation, which until now lived only
in the LRUSessionCache inside rag/chat_service.py - an in-process dict that
died with the worker and was invisible to every other replica. The thread
disappeared on refresh, on logout and on restart, and the model's context could
silently outlive what the UI still showed.

One durable thread per (meeting, user), so there is no session bookkeeping to
get wrong: the row set IS the conversation.

RLS follows the same defense-in-depth pattern as
e6a4d8f0b2c1_enable_rls_and_policies.py - the backend's own connection (DB
owner via the Supabase pooler) bypasses RLS either way, so these policies exist
for any other client holding an anon/authenticated role.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b8d2e6f4a1c9'
down_revision: Union[str, None] = 'c4e7a9b1f2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        # BIGSERIAL, not the UUID the other tables use: this is an append-only
        # log that has to be read back in order, and created_at cannot provide
        # that order - Postgres now() is transaction time, so a question and
        # its answer (committed together) share a timestamp to the microsecond.
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tools_used", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # The only access path: one meeting's thread, for its owner, in order.
    op.create_index(
        "ix_chat_messages_meeting_user",
        "chat_messages",
        ["meeting_id", "user_id", "id"],
    )

    op.execute("ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;")

    # chat_messages carries its own user_id, so ownership is a direct check
    # rather than the join back to meetings that meeting_chunks needs.
    op.execute("DROP POLICY IF EXISTS chat_messages_select_own ON chat_messages;")
    op.execute("""
        CREATE POLICY chat_messages_select_own ON chat_messages
        FOR SELECT
        USING (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS chat_messages_insert_own ON chat_messages;")
    op.execute("""
        CREATE POLICY chat_messages_insert_own ON chat_messages
        FOR INSERT
        WITH CHECK (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS chat_messages_delete_own ON chat_messages;")
    op.execute("""
        CREATE POLICY chat_messages_delete_own ON chat_messages
        FOR DELETE
        USING (user_id = auth.uid());
    """)
    # No UPDATE policy: a stored turn is a record of what was actually asked
    # and answered, and nothing in the app edits one.


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS chat_messages_delete_own ON chat_messages;")
    op.execute("DROP POLICY IF EXISTS chat_messages_insert_own ON chat_messages;")
    op.execute("DROP POLICY IF EXISTS chat_messages_select_own ON chat_messages;")
    op.drop_index("ix_chat_messages_meeting_user", table_name="chat_messages")
    op.drop_table("chat_messages")
