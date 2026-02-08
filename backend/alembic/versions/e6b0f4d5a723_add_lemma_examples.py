"""add example_ar and example_en to lemmas

Revision ID: e6b0f4d5a723
Revises: d5a9e3c4f612
Create Date: 2026-02-08 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e6b0f4d5a723"
down_revision: Union[str, None] = "d5a9e3c4f612"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lemmas", sa.Column("example_ar", sa.Text(), nullable=True))
    op.add_column("lemmas", sa.Column("example_en", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("lemmas", "example_en")
    op.drop_column("lemmas", "example_ar")
