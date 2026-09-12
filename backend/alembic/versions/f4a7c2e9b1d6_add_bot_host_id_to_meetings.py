"""add_bot_host_id_to_meetings

Phase C3 (docs/scaling-plan.md) - the bot registry.

Which recorder a meeting was dispatched to. There was nowhere to put this
before, because there was only ever one recorder: MEETING_BOT_URL. With a
pool, stop/delete/re-upload have to reach the host that actually has the
session, and asking the wrong one 404s on a session it never had while the
real recording carries on.

Nullable on purpose, and it stays nullable. Three things legitimately have no
host: every meeting created before this migration, every meeting still
"queued" (no recorder has been chosen yet - that is the point of the queue),
and every meeting joined on the synchronous BOT_DISPATCH_USE_QUEUE=false path.
bot_registry.require_host resolves a null to the sole configured host when
exactly one is configured, which is why a single-host install needs no
backfill and behaves exactly as it did before.

No backfill for that reason. Stamping every existing row with today's only
host id would bake a name into history that means nothing if the host is
later renamed, and would be wrong for any row that never had a bot at all.

Revision ID: f4a7c2e9b1d6
Revises: b8d2e6f4a1c9
Create Date: 2026-09-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f4a7c2e9b1d6'
down_revision: Union[str, None] = 'b8d2e6f4a1c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'meetings',
        sa.Column(
            'bot_host_id',
            sa.String(),
            nullable=True,
            comment=(
                "Which recorder in the BOT_HOSTS pool has this meeting (Phase "
                "C3). The host's id, not its URL, so re-addressing a host does "
                "not orphan its in-flight meetings. NULL for meetings from "
                "before C3, for meetings still waiting in 'queued', and on the "
                "synchronous dispatch path."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column('meetings', 'bot_host_id')
