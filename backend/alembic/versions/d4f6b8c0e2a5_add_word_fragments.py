"""Add word_fragments and word_retest_assignments.

Revision ID: d4f6b8c0e2a5
Revises: c3e5a7b9d1f4
Create Date: 2026-11-20
"""

from alembic import op
import sqlalchemy as sa

revision = "d4f6b8c0e2a5"
down_revision = "c3e5a7b9d1f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "word_fragments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("lemma_id", sa.Integer(), nullable=False),
        sa.Column("arabic_text", sa.Text(), nullable=False),
        sa.Column("english_translation", sa.Text(), nullable=False),
        sa.Column("transliteration", sa.Text(), nullable=True),
        sa.Column("target_surface", sa.Text(), nullable=False),
        sa.Column("times_shown", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_shown_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_word_fragments_lemma_id", "word_fragments", ["lemma_id"])
    op.create_index("ix_word_fragments_created_at", "word_fragments", ["created_at"])
    op.create_table(
        "word_retest_assignments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("lemma_id", sa.Integer(), nullable=False),
        sa.Column("missed_at", sa.DateTime(), nullable=False),
        sa.Column("miss_rating", sa.Integer(), nullable=True),
        sa.Column("arm", sa.String(20), nullable=False),
        sa.Column("delivered", sa.String(20), nullable=False),
        sa.Column("fragment_id", sa.Integer(), nullable=True),
        sa.Column("protocol", sa.String(40), nullable=False),
        sa.UniqueConstraint("lemma_id", "missed_at", name="uq_word_retest_assignment_miss"),
    )
    op.create_index("ix_word_retest_assignments_lemma_id", "word_retest_assignments", ["lemma_id"])
    op.create_index("ix_word_retest_assignments_created_at", "word_retest_assignments", ["created_at"])


def downgrade() -> None:
    op.drop_table("word_retest_assignments")
    op.drop_table("word_fragments")
