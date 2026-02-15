"""Add story_id to sentences and page_count to stories for book import.

Revision ID: u1v2w3x4y567
Revises: t0l5m6n7o123
Create Date: 2026-02-14
"""

from alembic import op
import sqlalchemy as sa

revision = "u1v2w3x4y567"
down_revision = "t0l5m6n7o123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sentences") as batch_op:
        batch_op.add_column(
            sa.Column("story_id", sa.Integer(), sa.ForeignKey("stories.id"), nullable=True)
        )
        batch_op.create_index("ix_sentences_story_id", ["story_id"])

    with op.batch_alter_table("stories") as batch_op:
        batch_op.add_column(
            sa.Column("page_count", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("stories") as batch_op:
        batch_op.drop_column("page_count")

    with op.batch_alter_table("sentences") as batch_op:
        batch_op.drop_index("ix_sentences_story_id")
        batch_op.drop_column("story_id")
