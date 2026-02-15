"""Add page_number to sentences and story_words

Revision ID: ad1ca8ace671
Revises: u1v2w3x4y567
Create Date: 2026-02-15 23:44:22.261214

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ad1ca8ace671'
down_revision: Union[str, Sequence[str], None] = 'u1v2w3x4y567'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('sentences', schema=None) as batch_op:
        batch_op.add_column(sa.Column('page_number', sa.Integer(), nullable=True))

    with op.batch_alter_table('story_words', schema=None) as batch_op:
        batch_op.add_column(sa.Column('page_number', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('story_words', schema=None) as batch_op:
        batch_op.drop_column('page_number')

    with op.batch_alter_table('sentences', schema=None) as batch_op:
        batch_op.drop_column('page_number')
