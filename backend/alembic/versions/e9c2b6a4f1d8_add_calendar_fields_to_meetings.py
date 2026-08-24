"""add_calendar_fields_to_meetings

Revision ID: e9c2b6a4f1d8
Revises: d3f8a1c5b2e7
Create Date: 2026-08-24 00:00:00.000000

Adds scheduled_at (when set, the meeting is a future-dated join that
app/services/scheduler.py's sweep picks up rather than joining
immediately) and calendar_event_id (the originating Google Calendar
event, for meetings created via POST /calendar/events/{event_id}/schedule)
- see docs/google-calendar-integration-plan.md. Manually-created
meetings leave both null and are unaffected.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e9c2b6a4f1d8'
down_revision: Union[str, None] = 'd3f8a1c5b2e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE meetings
        ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS calendar_event_id CHARACTER VARYING;
    """)
    # A given calendar event can only ever back one scheduled meeting
    # per user - prevents double-scheduling the same event (e.g. a
    # double-click on "Record"). Partial index since most rows have
    # calendar_event_id NULL (manually-created meetings).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS meetings_user_calendar_event_idx
        ON meetings (user_id, calendar_event_id)
        WHERE calendar_event_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS meetings_user_calendar_event_idx;")
    op.execute("ALTER TABLE meetings DROP COLUMN IF EXISTS calendar_event_id;")
    op.execute("ALTER TABLE meetings DROP COLUMN IF EXISTS scheduled_at;")
