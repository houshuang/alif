"""add_variant_decisions_table

Revision ID: n4f8a9b0c567
Revises: m3e7f8a9b456
Create Date: 2026-02-11 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'n4f8a9b0c567'
down_revision: Union[str, Sequence[str], None] = 'm3e7f8a9b456'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "variant_decisions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("word_bare", sa.Text, nullable=False, index=True),
        sa.Column("base_bare", sa.Text, nullable=False, index=True),
        sa.Column("is_variant", sa.Boolean, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("decided_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("variant_decisions")
