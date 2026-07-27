"""Add immutable token-level review presentation evidence.

Revision ID: e5a7b9c1d3f5
Revises: d4f6a8b0c2e4
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa


revision = "e5a7b9c1d3f5"
down_revision = "d4f6a8b0c2e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "word_review_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_review_id", sa.String(length=50), nullable=False),
        sa.Column("review_log_id", sa.Integer(), nullable=True),
        sa.Column("sentence_word_id", sa.Integer(), nullable=False),
        sa.Column("sentence_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("lemma_id", sa.Integer(), nullable=False),
        sa.Column("canonical_lemma_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("review_mode", sa.String(length=20), nullable=False),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("surface_form", sa.Text(), nullable=False),
        sa.Column("rendered_front_form", sa.Text(), nullable=False),
        sa.Column("default_show_tashkeel", sa.Boolean(), nullable=False),
        sa.Column("front_initial_tashkeel_visible", sa.Boolean(), nullable=False),
        sa.Column("front_ever_tashkeel_visible", sa.Boolean(), nullable=False),
        sa.Column("front_tashkeel_visible_at_answer", sa.Boolean(), nullable=False),
        sa.Column("front_toggle_count", sa.Integer(), nullable=False),
        sa.Column("answer_revealed", sa.Boolean(), nullable=False),
        sa.Column("back_tashkeel_visible_at_rating", sa.Boolean(), nullable=True),
        sa.Column("back_toggle_count", sa.Integer(), nullable=False),
        sa.Column("failure_causes_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["canonical_lemma_id"],
            ["lemmas.lemma_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["lemma_id"],
            ["lemmas.lemma_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["review_log_id"],
            ["review_log.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sentence_id"],
            ["sentences.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sentence_word_id"],
            ["sentence_words.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "client_review_id",
            "sentence_word_id",
            name="uq_word_review_evidence_client_token",
        ),
    )
    op.create_index(
        op.f("ix_word_review_evidence_client_review_id"),
        "word_review_evidence",
        ["client_review_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_word_review_evidence_review_log_id"),
        "word_review_evidence",
        ["review_log_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_word_review_evidence_sentence_word_id"),
        "word_review_evidence",
        ["sentence_word_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_word_review_evidence_sentence_id"),
        "word_review_evidence",
        ["sentence_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_word_review_evidence_lemma_id"),
        "word_review_evidence",
        ["lemma_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_word_review_evidence_canonical_lemma_id"),
        "word_review_evidence",
        ["canonical_lemma_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_word_review_evidence_created_at"),
        "word_review_evidence",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_word_review_evidence_created_at"),
        table_name="word_review_evidence",
    )
    op.drop_index(
        op.f("ix_word_review_evidence_canonical_lemma_id"),
        table_name="word_review_evidence",
    )
    op.drop_index(
        op.f("ix_word_review_evidence_lemma_id"),
        table_name="word_review_evidence",
    )
    op.drop_index(
        op.f("ix_word_review_evidence_sentence_id"),
        table_name="word_review_evidence",
    )
    op.drop_index(
        op.f("ix_word_review_evidence_sentence_word_id"),
        table_name="word_review_evidence",
    )
    op.drop_index(
        op.f("ix_word_review_evidence_review_log_id"),
        table_name="word_review_evidence",
    )
    op.drop_index(
        op.f("ix_word_review_evidence_client_review_id"),
        table_name="word_review_evidence",
    )
    op.drop_table("word_review_evidence")
