"""add_sentence_is_active

Revision ID: k1c5d6e7f234
Revises: j0b4c5d6e123
Create Date: 2026-02-11 06:30:21.211961

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'k1c5d6e7f234'
down_revision: Union[str, Sequence[str], None] = 'j0b4c5d6e123'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sentences", sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False))


def downgrade() -> None:
    op.drop_column("sentences", "is_active")
