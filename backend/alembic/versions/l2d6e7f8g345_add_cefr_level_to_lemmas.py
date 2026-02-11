"""add_cefr_level_to_lemmas

Revision ID: l2d6e7f8g345
Revises: k1c5d6e7f234
Create Date: 2026-02-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'l2d6e7f8g345'
down_revision: Union[str, Sequence[str], None] = 'k1c5d6e7f234'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lemmas", sa.Column("cefr_level", sa.String(2), nullable=True))


def downgrade() -> None:
    op.drop_column("lemmas", "cefr_level")
