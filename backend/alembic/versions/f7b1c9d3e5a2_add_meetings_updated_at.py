"""add_meetings_updated_at

Revision ID: f7b1c9d3e5a2
Revises: e6a4d8f0b2c1
Create Date: 2026-08-21 00:00:00.000000

Adds meetings.updated_at, which the new backend watchdog
(app/services/watchdog.py) uses to detect meetings stuck in a
non-terminal status (joining/recording/uploading/transcribing) past
their TTL - see docs/reliability-audit.md, finding #1. Existing rows
are backfilled from created_at, so a meeting that's already stuck at
migration time becomes immediately eligible for the next sweep rather
than silently exempt for having no updated_at yet.

Also adds an index on meetings.status, since that's the sweep's WHERE
clause on every run.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f7b1c9d3e5a2'
down_revision: Union[str, None] = 'e6a4d8f0b2c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE meetings
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
    """)
    op.execute("""
        UPDATE meetings SET updated_at = created_at WHERE updated_at IS NULL;
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS meetings_status_idx ON meetings (status);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS meetings_status_idx;")
    op.execute("ALTER TABLE meetings DROP COLUMN IF EXISTS updated_at;")
