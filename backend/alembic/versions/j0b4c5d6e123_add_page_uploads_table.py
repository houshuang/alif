"""add page_uploads table for textbook OCR scanning

Revision ID: j0b4c5d6e123
Revises: i9a3b4c5d012
Create Date: 2026-02-09 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "j0b4c5d6e123"
down_revision: str | None = "i9a3b4c5d012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "page_uploads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.String(50), nullable=False, index=True),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False, index=True),
        sa.Column("extracted_words_json", sa.JSON(), nullable=True),
        sa.Column("new_words", sa.Integer(), server_default="0"),
        sa.Column("existing_words", sa.Integer(), server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("page_uploads")
