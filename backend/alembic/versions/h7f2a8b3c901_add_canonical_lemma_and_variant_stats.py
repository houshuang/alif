"""add canonical_lemma_id and variant_stats_json

Revision ID: h7f2a8b3c901
Revises: a1b2c3d4e5f6
Create Date: 2026-02-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "h7f2a8b3c901"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lemmas", sa.Column("canonical_lemma_id", sa.Integer(), nullable=True))
    op.add_column("user_lemma_knowledge", sa.Column("variant_stats_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_lemma_knowledge", "variant_stats_json")
    op.drop_column("lemmas", "canonical_lemma_id")
