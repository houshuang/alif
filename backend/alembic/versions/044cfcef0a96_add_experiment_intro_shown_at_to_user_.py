"""add experiment_intro_shown_at to user_lemma_knowledge

Revision ID: 044cfcef0a96
Revises: 08b6c78e0212
Create Date: 2026-03-07 18:02:43.286991

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '044cfcef0a96'
down_revision: Union[str, Sequence[str], None] = '08b6c78e0212'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('user_lemma_knowledge', schema=None) as batch_op:
        batch_op.add_column(sa.Column('experiment_intro_shown_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('user_lemma_knowledge', schema=None) as batch_op:
        batch_op.drop_column('experiment_intro_shown_at')
