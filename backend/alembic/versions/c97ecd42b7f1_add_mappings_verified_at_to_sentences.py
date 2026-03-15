"""add mappings_verified_at to sentences

Revision ID: c97ecd42b7f1
Revises: 7aefcb0433a9
Create Date: 2026-03-14 21:00:01.369485

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c97ecd42b7f1'
down_revision: Union[str, Sequence[str], None] = '7aefcb0433a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('sentences', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mappings_verified_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('sentences', schema=None) as batch_op:
        batch_op.drop_column('mappings_verified_at')
