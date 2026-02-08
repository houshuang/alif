"""add last_comprehension to sentences

Revision ID: f7c1a5b6d834
Revises: e6b0f4d5a723
Create Date: 2026-02-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f7c1a5b6d834"
down_revision: Union[str, None] = "e6b0f4d5a723"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sentences", sa.Column("last_comprehension", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("sentences", "last_comprehension")
