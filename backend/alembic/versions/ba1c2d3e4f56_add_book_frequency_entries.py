"""Add book_frequency_entries.

Revision ID: ba1c2d3e4f56
Revises: a2d4f6c8b019
Create Date: 2026-11-25
"""

from alembic import op
import sqlalchemy as sa

revision = "ba1c2d3e4f56"
down_revision = "a2d4f6c8b019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "book_frequency_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("lemma_id", sa.Integer(), sa.ForeignKey("lemmas.lemma_id"), nullable=True),
        sa.Column("lemma_key", sa.Text(), nullable=False),
        sa.Column("pos", sa.String(20), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_book_frequency_entries_rank", "book_frequency_entries", ["rank"], unique=True)
    op.create_index("ix_book_frequency_entries_lemma_id", "book_frequency_entries", ["lemma_id"])
    op.create_index("ix_book_frequency_entries_lemma_key", "book_frequency_entries", ["lemma_key"])


def downgrade() -> None:
    op.drop_table("book_frequency_entries")
