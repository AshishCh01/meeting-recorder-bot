"""add_ai_usage_events_table

Phase B4 (docs/scaling-plan.md) - AI spend as data rather than a debug line.

Until now every AI call ended in a `[cost] ...` print(): no user on it, Groq
priced at a hard-coded 0, the Sarvam fallback and chat-query embeddings not
logged at all, and nothing that could be summed per user. B1's rate limits
were sized from an estimated per-turn cost because there was nothing to
measure the real one with. This table is that measurement; the saved queries
that read it are in the plan's B4 section.

No foreign keys on user_id or meeting_id, on purpose: deleting a meeting or a
user must not delete what it cost.

RLS is enabled with no policies at all. The backend's own connection (DB owner
via the Supabase pooler) bypasses RLS, as for every other table (see
e6a4d8f0b2c1_enable_rls_and_policies.py); unlike those tables, nothing here is
meant for an end user to read, so the anon and authenticated roles are denied
outright rather than scoped to their own rows. The Supabase SQL editor runs as
the owner and can read it.

Revision ID: c7e3a5b9d2f1
Revises: f4a7c2e9b1d6
Create Date: 2026-09-14 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c7e3a5b9d2f1'
down_revision: Union[str, None] = 'f4a7c2e9b1d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("audio_seconds", sa.Float(), nullable=True),
        sa.Column("usd", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.Column("estimated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_usage_events_user_created", "ai_usage_events", ["user_id", "created_at"])
    op.create_index("ix_ai_usage_events_meeting", "ai_usage_events", ["meeting_id"])

    op.execute("ALTER TABLE ai_usage_events ENABLE ROW LEVEL SECURITY;")


def downgrade() -> None:
    op.drop_index("ix_ai_usage_events_meeting", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_events_user_created", table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
