"""add pipeline_snapshots table

Revision ID: 08b6c78e0212
Revises: 9e0c82fb0b07
Create Date: 2026-03-04 00:07:41.993213

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '08b6c78e0212'
down_revision: Union[str, Sequence[str], None] = '9e0c82fb0b07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('pipeline_snapshots',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('date', sa.String(length=10), nullable=False),
    sa.Column('box_1_count', sa.Integer(), nullable=False),
    sa.Column('box_2_count', sa.Integer(), nullable=False),
    sa.Column('box_3_count', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('date')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('pipeline_snapshots')
