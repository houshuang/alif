"""add source_story_id to lemma

Revision ID: 217c2a674f92
Revises: n4f8a9b0c567
Create Date: 2026-02-11 15:36:21.457233

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '217c2a674f92'
down_revision: Union[str, Sequence[str], None] = 'n4f8a9b0c567'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add source_story_id FK to lemmas table."""
    with op.batch_alter_table('lemmas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_story_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove source_story_id from lemmas table."""
    with op.batch_alter_table('lemmas', schema=None) as batch_op:
        batch_op.drop_column('source_story_id')
