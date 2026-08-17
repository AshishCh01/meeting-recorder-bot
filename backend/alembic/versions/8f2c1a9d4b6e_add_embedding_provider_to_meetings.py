"""add_embedding_provider_to_meetings

Revision ID: 8f2c1a9d4b6e
Revises: 475e9ee29ad9
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8f2c1a9d4b6e'
down_revision: Union[str, None] = '475e9ee29ad9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'meetings',
        sa.Column(
            'embedding_provider',
            sa.String(),
            nullable=True,
            comment=(
                "Which embedding model indexed this meeting's chunks "
                "('gemini' or 'jina') - embed_query must match it, since "
                "the two providers' vectors are not comparable."
            ),
        ),
    )
    # Backfill: any meeting that already has indexed chunks was indexed
    # with Gemini, since Jina didn't exist as an option before this.
    op.execute(
        "UPDATE meetings SET embedding_provider = 'gemini' "
        "WHERE id IN (SELECT DISTINCT meeting_id FROM meeting_chunks)"
    )


def downgrade() -> None:
    op.drop_column('meetings', 'embedding_provider')