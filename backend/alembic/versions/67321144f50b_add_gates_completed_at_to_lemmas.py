"""add_gates_completed_at_to_lemmas

Revision ID: 67321144f50b
Revises: 83b0dd62728d
Create Date: 2026-04-01 07:00:52.586413

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '67321144f50b'
down_revision: Union[str, Sequence[str], None] = '83b0dd62728d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('lemmas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gates_completed_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('lemmas', schema=None) as batch_op:
        batch_op.drop_column('gates_completed_at')
