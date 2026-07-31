"""Complete token presentation evidence for inert sentence words.

Revision ID: a7c9e1f3b5d7
Revises: f6b8c0d2e4a6
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa


revision = "a7c9e1f3b5d7"
down_revision = "f6b8c0d2e4a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("word_review_evidence") as batch_op:
        batch_op.add_column(sa.Column(
            "is_schedulable_content",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ))
        batch_op.add_column(sa.Column(
            "is_function_word",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ))
        batch_op.add_column(sa.Column(
            "is_proper_name",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ))
        batch_op.add_column(sa.Column(
            "rating_source",
            sa.String(length=30),
            nullable=False,
            server_default="sentence_comprehension",
        ))


def downgrade() -> None:
    with op.batch_alter_table("word_review_evidence") as batch_op:
        batch_op.drop_column("rating_source")
        batch_op.drop_column("is_proper_name")
        batch_op.drop_column("is_function_word")
        batch_op.drop_column("is_schedulable_content")
