"""add_ask_ai_tables

Ask AI Phase 1 (docs/ask-ai-implementation-plan.md) - the schema for a chat
across all of a user's meetings.

- meetings.summary_embedding: one vector per meeting (title, summary and key
  points), so "which meeting discussed pricing?" can rank whole meetings. It is
  written in the same embedding call as the meeting's chunks, so
  meetings.embedding_provider describes both. Nullable: meetings indexed before
  this migration have none until the backfill reaches them.
- ix_meetings_user_meeting_date: a meeting's date is
  coalesce(scheduled_at, created_at). Ask AI's date filters use exactly that
  expression (ask_ai/tools/dates.py), and Postgres only uses an expression
  index for a query that repeats the expression exactly.
- users.ask_ai_instructions: the user's custom instructions for Ask AI. The
  1,000-character cap is enforced by the API, not here.
- ask_ai_conversations / ask_ai_messages: one row per chat, one row per turn.
  New tables rather than a nullable chat_messages.meeting_id: that table's
  index, cascade and every query assume one thread per meeting.

RLS follows the same defense-in-depth pattern as
e6a4d8f0b2c1_enable_rls_and_policies.py - the backend's own connection (DB
owner via the Supabase pooler) bypasses RLS either way, so these policies exist
for any other client holding an anon/authenticated role.

Revision ID: d5b8e1f3a7c2
Revises: c7e3a5b9d2f1
Create Date: 2026-09-19 00:00:00.000000
"""
from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd5b8e1f3a7c2'
down_revision: Union[str, None] = 'c7e3a5b9d2f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- meetings / users -------------------------------------------------

    op.add_column(
        "meetings",
        sa.Column("summary_embedding", pgvector.sqlalchemy.Vector(dim=768), nullable=True),
    )

    # No IF NOT EXISTS in op.create_index; raw SQL so a hand-created index of
    # the same name (e.g. while trying the plan out) doesn't fail the deploy.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_meetings_user_meeting_date
        ON meetings (user_id, (coalesce(scheduled_at, created_at)));
    """)

    op.add_column("users", sa.Column("ask_ai_instructions", sa.Text(), nullable=True))

    # --- ask_ai_conversations ---------------------------------------------

    op.create_table(
        "ask_ai_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(), server_default="New chat", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # NULL means the chat is empty: nothing has been asked in it yet. Set
        # when an exchange is saved.
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # The sidebar list: one user's chats, most recently used first.
    op.create_index(
        "ix_ask_ai_conversations_user_last_message",
        "ask_ai_conversations",
        ["user_id", sa.text("last_message_at DESC")],
    )

    # At most one empty chat per user. "New chat" reuses the empty one if it
    # exists; this makes that hold even when two tabs click it at the same
    # moment, since the second insert fails instead of making a duplicate.
    op.create_index(
        "ux_ask_ai_conversations_one_empty_per_user",
        "ask_ai_conversations",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("last_message_at IS NULL"),
    )

    # --- ask_ai_messages --------------------------------------------------

    op.create_table(
        "ask_ai_messages",
        # BIGSERIAL for the same reason as chat_messages: the thread is read
        # back in insertion order, and a question and its answer committed
        # together share a created_at to the microsecond.
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Redundant with the conversation's owner, so ownership checks and RLS
        # need no join.
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tools_used", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["ask_ai_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # The only access path: one conversation's thread, in order.
    op.create_index(
        "ix_ask_ai_messages_conversation",
        "ask_ai_messages",
        ["conversation_id", "id"],
    )

    # --- RLS --------------------------------------------------------------

    op.execute("ALTER TABLE ask_ai_conversations ENABLE ROW LEVEL SECURITY;")

    op.execute("DROP POLICY IF EXISTS ask_ai_conversations_select_own ON ask_ai_conversations;")
    op.execute("""
        CREATE POLICY ask_ai_conversations_select_own ON ask_ai_conversations
        FOR SELECT
        USING (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS ask_ai_conversations_insert_own ON ask_ai_conversations;")
    op.execute("""
        CREATE POLICY ask_ai_conversations_insert_own ON ask_ai_conversations
        FOR INSERT
        WITH CHECK (user_id = auth.uid());
    """)
    # UPDATE, unlike chat_messages: a chat is renamed, and its
    # last_message_at moves with every exchange.
    op.execute("DROP POLICY IF EXISTS ask_ai_conversations_update_own ON ask_ai_conversations;")
    op.execute("""
        CREATE POLICY ask_ai_conversations_update_own ON ask_ai_conversations
        FOR UPDATE
        USING (user_id = auth.uid())
        WITH CHECK (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS ask_ai_conversations_delete_own ON ask_ai_conversations;")
    op.execute("""
        CREATE POLICY ask_ai_conversations_delete_own ON ask_ai_conversations
        FOR DELETE
        USING (user_id = auth.uid());
    """)

    op.execute("ALTER TABLE ask_ai_messages ENABLE ROW LEVEL SECURITY;")

    op.execute("DROP POLICY IF EXISTS ask_ai_messages_select_own ON ask_ai_messages;")
    op.execute("""
        CREATE POLICY ask_ai_messages_select_own ON ask_ai_messages
        FOR SELECT
        USING (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS ask_ai_messages_insert_own ON ask_ai_messages;")
    op.execute("""
        CREATE POLICY ask_ai_messages_insert_own ON ask_ai_messages
        FOR INSERT
        WITH CHECK (user_id = auth.uid());
    """)
    op.execute("DROP POLICY IF EXISTS ask_ai_messages_delete_own ON ask_ai_messages;")
    op.execute("""
        CREATE POLICY ask_ai_messages_delete_own ON ask_ai_messages
        FOR DELETE
        USING (user_id = auth.uid());
    """)
    # No UPDATE policy on messages, as for chat_messages: a stored turn is a
    # record of what was actually asked and answered.


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS ask_ai_messages_delete_own ON ask_ai_messages;")
    op.execute("DROP POLICY IF EXISTS ask_ai_messages_insert_own ON ask_ai_messages;")
    op.execute("DROP POLICY IF EXISTS ask_ai_messages_select_own ON ask_ai_messages;")
    op.execute("DROP POLICY IF EXISTS ask_ai_conversations_delete_own ON ask_ai_conversations;")
    op.execute("DROP POLICY IF EXISTS ask_ai_conversations_update_own ON ask_ai_conversations;")
    op.execute("DROP POLICY IF EXISTS ask_ai_conversations_insert_own ON ask_ai_conversations;")
    op.execute("DROP POLICY IF EXISTS ask_ai_conversations_select_own ON ask_ai_conversations;")

    op.drop_index("ix_ask_ai_messages_conversation", table_name="ask_ai_messages")
    op.drop_table("ask_ai_messages")
    op.drop_index("ux_ask_ai_conversations_one_empty_per_user", table_name="ask_ai_conversations")
    op.drop_index("ix_ask_ai_conversations_user_last_message", table_name="ask_ai_conversations")
    op.drop_table("ask_ai_conversations")

    op.drop_column("users", "ask_ai_instructions")
    op.execute("DROP INDEX IF EXISTS ix_meetings_user_meeting_date;")
    op.drop_column("meetings", "summary_embedding")
