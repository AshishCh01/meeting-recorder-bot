"""add_title_to_meetings

Revision ID: a1b2c3d4e5f6
Revises: f7b1c9d3e5a2
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f7b1c9d3e5a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'meetings',
        sa.Column(
            'title',
            sa.String(),
            nullable=True,
            comment="Short Gemini-generated meeting title (<60 chars). Null for meetings transcribed before this existed, or via the Sarvam fallback path - API falls back to meeting_url in that case.",
        ),
    )


def downgrade() -> None:
    op.drop_column('meetings', 'title')
