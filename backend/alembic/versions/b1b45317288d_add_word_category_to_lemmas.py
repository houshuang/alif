"""add word_category to lemmas

Revision ID: b1b45317288d
Revises: ad1ca8ace671
Create Date: 2026-02-16 07:33:03.739424

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1b45317288d'
down_revision: Union[str, Sequence[str], None] = 'ad1ca8ace671'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('lemmas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('word_category', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('lemmas', schema=None) as batch_op:
        batch_op.drop_column('word_category')
