"""add forms_json to lemmas

Revision ID: d5a9e3c4f612
Revises: c4f8a2b3d501
Create Date: 2026-02-08 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d5a9e3c4f612"
down_revision: Union[str, None] = "c4f8a2b3d501"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lemmas", sa.Column("forms_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("lemmas", "forms_json")
