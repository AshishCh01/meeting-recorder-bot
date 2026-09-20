"""add_billing_tables

Billing Phase 1 - the plan model: users.plan, plus the subscriptions and
payments tables. No quota is enforced yet (Phase 2) and no payment can be
taken yet (Phase 3); this migration only gives those phases somewhere to
write.

Three choices worth stating, because they are not obvious from the DDL:

- **users.plan is NOT NULL with a 'free' default.** Every existing user
  becomes a free user on upgrade, which is what they already were. A NULL
  plan would force every quota check to handle "unknown", and there is no
  such state: no subscription means free.

- **subscriptions.user_id is UNIQUE.** One current subscription per user;
  upgrades rewrite the row. The history lives in payments.

- **payments has no foreign keys**, like ai_usage_events. Deleting a user
  must not delete the record of what they paid. Its razorpay_order_id is
  unique, which is what makes the Phase 3 webhook idempotent: a replayed
  callback hits a constraint instead of creating a second charge row.

RLS follows the split already established in e6a4d8f0b2c1 and c7e3a5b9d2f1.
subscriptions gets an owner-scoped select policy - a user may see their own
plan - and nothing else, since only the backend may write one. payments gets
RLS with no policies at all: the anon and authenticated roles are denied
outright, because billing records are read through the backend (service role,
bypasses RLS) or not at all.

Revision ID: a9f3c7d21e84
Revises: d5b8e1f3a7c2
Create Date: 2026-09-21 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a9f3c7d21e84'
down_revision: Union[str, None] = 'd5b8e1f3a7c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("plan", sa.String(), server_default="free", nullable=False),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="active", nullable=False),
        sa.Column("seats", sa.Integer(), server_default="1", nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("razorpay_order_id", sa.String(), nullable=True),
        sa.Column("razorpay_payment_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_subscriptions_user_id"),
    )
    op.create_index(
        "ix_subscriptions_status_period_end",
        "subscriptions",
        ["status", "current_period_end"],
    )

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plan", sa.String(), nullable=False),
        sa.Column("seats", sa.Integer(), server_default="1", nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), server_default="INR", nullable=False),
        sa.Column("status", sa.String(), server_default="created", nullable=False),
        sa.Column("razorpay_order_id", sa.String(), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(), nullable=True),
        sa.Column("razorpay_signature", sa.String(), nullable=True),
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("razorpay_order_id", name="uq_payments_razorpay_order_id"),
    )
    op.create_index(
        "ix_payments_user_created",
        "payments",
        ["user_id", sa.text("created_at DESC")],
    )

    # subscriptions: a user may read their own plan; writes are backend-only.
    op.execute("ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS subscriptions_select_own ON subscriptions;")
    op.execute("""
        CREATE POLICY subscriptions_select_own ON subscriptions
        FOR SELECT TO authenticated
        USING (user_id = auth.uid());
    """)

    # payments: RLS on, no policies - denied to anon and authenticated alike.
    op.execute("ALTER TABLE payments ENABLE ROW LEVEL SECURITY;")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS subscriptions_select_own ON subscriptions;")
    op.drop_index("ix_payments_user_created", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_subscriptions_status_period_end", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_column("users", "plan")
