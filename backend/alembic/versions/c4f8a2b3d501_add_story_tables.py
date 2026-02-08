"""add story tables

Revision ID: c4f8a2b3d501
Revises: b3e7d9a1f402
Create Date: 2026-02-08 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4f8a2b3d501"
down_revision: Union[str, None] = "b3e7d9a1f402"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title_ar", sa.Text(), nullable=True),
        sa.Column("title_en", sa.Text(), nullable=True),
        sa.Column("body_ar", sa.Text(), nullable=False),
        sa.Column("body_en", sa.Text(), nullable=True),
        sa.Column("transliteration", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("total_words", sa.Integer(), server_default="0"),
        sa.Column("known_count", sa.Integer(), server_default="0"),
        sa.Column("unknown_count", sa.Integer(), server_default="0"),
        sa.Column("readiness_pct", sa.Float(), server_default="0.0"),
        sa.Column("difficulty_level", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "story_words",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("stories.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("surface_form", sa.Text(), nullable=False),
        sa.Column("lemma_id", sa.Integer(), sa.ForeignKey("lemmas.lemma_id"), nullable=True),
        sa.Column("sentence_index", sa.Integer(), server_default="0"),
        sa.Column("gloss_en", sa.Text(), nullable=True),
        sa.Column("is_known_at_creation", sa.Integer(), server_default="0"),
        sa.Column("is_function_word", sa.Integer(), server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("story_words")
    op.drop_table("stories")
