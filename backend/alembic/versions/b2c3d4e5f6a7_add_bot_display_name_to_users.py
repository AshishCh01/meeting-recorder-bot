"""add_bot_display_name_to_users

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'bot_display_name',
            sa.String(),
            nullable=False,
            server_default="MeetIQ Notetaker",
            comment="Name the meeting bot shows in the participant list when it joins on this user's behalf.",
        ),
    )


def downgrade() -> None:
    op.drop_column('users', 'bot_display_name')
