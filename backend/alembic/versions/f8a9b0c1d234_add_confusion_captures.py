"""Add confusion_captures table for user-reported word confusion data.

Revision ID: f8a9b0c1d234
Revises: e7f8a9b0c123
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa


revision = "f8a9b0c1d234"
down_revision = "e7f8a9b0c123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "confusion_captures",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "failed_lemma_id",
            sa.Integer,
            sa.ForeignKey("lemmas.lemma_id"),
            nullable=False,
        ),
        sa.Column(
            "sentence_id",
            sa.Integer,
            sa.ForeignKey("sentences.id"),
            nullable=True,
        ),
        sa.Column("session_id", sa.String(50), nullable=True),
        sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("captured_at", sa.DateTime, nullable=True),
        sa.Column("capture_method", sa.String(20), nullable=False),
        sa.Column(
            "confused_with_lemma_id",
            sa.Integer,
            sa.ForeignKey("lemmas.lemma_id"),
            nullable=True,
        ),
        sa.Column("confused_with_text", sa.Text, nullable=True),
        sa.Column("candidates_shown_json", sa.JSON, nullable=True),
        sa.Column(
            "resolved_lemma_id",
            sa.Integer,
            sa.ForeignKey("lemmas.lemma_id"),
            nullable=True,
        ),
        sa.Column("resolution_method", sa.String(20), nullable=True),
        sa.Column("resolution_confidence", sa.Float, nullable=True),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
    )
    op.create_index(
        "ix_confusion_captures_failed_lemma_id",
        "confusion_captures",
        ["failed_lemma_id"],
    )
    op.create_index(
        "ix_confusion_captures_sentence_id",
        "confusion_captures",
        ["sentence_id"],
    )
    op.create_index(
        "ix_confusion_captures_session_id",
        "confusion_captures",
        ["session_id"],
    )
    op.create_index(
        "ix_confusion_captures_captured_at",
        "confusion_captures",
        ["captured_at"],
    )
    op.create_index(
        "ix_confusion_captures_confused_with_lemma_id",
        "confusion_captures",
        ["confused_with_lemma_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_confusion_captures_confused_with_lemma_id", "confusion_captures")
    op.drop_index("ix_confusion_captures_captured_at", "confusion_captures")
    op.drop_index("ix_confusion_captures_session_id", "confusion_captures")
    op.drop_index("ix_confusion_captures_sentence_id", "confusion_captures")
    op.drop_index("ix_confusion_captures_failed_lemma_id", "confusion_captures")
    op.drop_table("confusion_captures")
