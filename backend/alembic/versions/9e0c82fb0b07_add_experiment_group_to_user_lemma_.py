"""add experiment_group to user_lemma_knowledge

Revision ID: 9e0c82fb0b07
Revises: a1b2c3d4e567
Create Date: 2026-03-03 23:04:05.857995

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e0c82fb0b07'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e567'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('user_lemma_knowledge', schema=None) as batch_op:
        batch_op.add_column(sa.Column('experiment_group', sa.String(length=30), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('user_lemma_knowledge', schema=None) as batch_op:
        batch_op.drop_column('experiment_group')
