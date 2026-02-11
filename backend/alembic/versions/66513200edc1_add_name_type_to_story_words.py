"""add name_type to story_words

Revision ID: 66513200edc1
Revises: 217c2a674f92
Create Date: 2026-02-11 15:56:20.515931

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '66513200edc1'
down_revision: Union[str, Sequence[str], None] = '217c2a674f92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('story_words', schema=None) as batch_op:
        batch_op.add_column(sa.Column('name_type', sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('story_words', schema=None) as batch_op:
        batch_op.drop_column('name_type')
