"""Add frequency core entries.

Revision ID: ab9c0d1e2f34
Revises: aa7h8i9j0k12
Create Date: 2026-05-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ab9c0d1e2f34"
down_revision: Union[str, Sequence[str], None] = "aa7h8i9j0k12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "frequency_core_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("core_rank", sa.Integer(), nullable=False),
        sa.Column("lemma_id", sa.Integer(), sa.ForeignKey("lemmas.lemma_id"), nullable=True),
        sa.Column("lemma_key", sa.Text(), nullable=False),
        sa.Column("display_form", sa.Text(), nullable=False),
        sa.Column("gloss_en", sa.Text(), nullable=True),
        sa.Column("pos", sa.String(length=20), nullable=True),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("camel_rank", sa.Integer(), nullable=True),
        sa.Column("camel_count", sa.Integer(), nullable=True),
        sa.Column("buckwalter_rank", sa.Integer(), nullable=True),
        sa.Column("artenten_rank", sa.Integer(), nullable=True),
        sa.Column("kelly_rank", sa.Integer(), nullable=True),
        sa.Column("kelly_cefr", sa.String(length=2), nullable=True),
        sa.Column("hindawi_rank", sa.Integer(), nullable=True),
        sa.Column("news_rank", sa.Integer(), nullable=True),
        sa.Column("islamic_rank", sa.Integer(), nullable=True),
        sa.Column("broad_source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence_tier", sa.String(length=20), nullable=False, server_default="low"),
        sa.Column("gap_status", sa.String(length=30), nullable=True),
        sa.Column("source_flags_json", sa.JSON(), nullable=True),
        sa.Column("excluded_reason", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_frequency_core_entries_core_rank", "frequency_core_entries", ["core_rank"], unique=True)
    op.create_index("ix_frequency_core_entries_lemma_id", "frequency_core_entries", ["lemma_id"])
    op.create_index("ix_frequency_core_entries_lemma_key", "frequency_core_entries", ["lemma_key"])


def downgrade() -> None:
    op.drop_index("ix_frequency_core_entries_lemma_key", table_name="frequency_core_entries")
    op.drop_index("ix_frequency_core_entries_lemma_id", table_name="frequency_core_entries")
    op.drop_index("ix_frequency_core_entries_core_rank", table_name="frequency_core_entries")
    op.drop_table("frequency_core_entries")
