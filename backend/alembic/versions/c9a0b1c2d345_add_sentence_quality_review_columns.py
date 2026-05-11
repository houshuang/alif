"""Add sentence quality review metadata.

Revision ID: c9a0b1c2d345
Revises: ab9c0d1e2f34
Create Date: 2026-05-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9a0b1c2d345"
down_revision: Union[str, Sequence[str], None] = "ab9c0d1e2f34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sentences") as batch_op:
        batch_op.add_column(sa.Column("quality_reviewed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("quality_natural", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("quality_translation_correct", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("quality_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sentences") as batch_op:
        batch_op.drop_column("quality_reason")
        batch_op.drop_column("quality_translation_correct")
        batch_op.drop_column("quality_natural")
        batch_op.drop_column("quality_reviewed_at")
