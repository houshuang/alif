"""add_content_flags_and_activity_log

Revision ID: m3e7f8a9b456
Revises: l2d6e7f8g345
Create Date: 2026-02-11 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'm3e7f8a9b456'
down_revision: Union[str, Sequence[str], None] = 'l2d6e7f8g345'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "content_flags",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("content_type", sa.String(30), nullable=False, index=True),
        sa.Column("lemma_id", sa.Integer, sa.ForeignKey("lemmas.lemma_id"), nullable=True),
        sa.Column("sentence_id", sa.Integer, sa.ForeignKey("sentences.id"), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", index=True),
        sa.Column("original_value", sa.Text, nullable=True),
        sa.Column("corrected_value", sa.Text, nullable=True),
        sa.Column("resolution_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "activity_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(50), nullable=False, index=True),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("detail_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("activity_log")
    op.drop_table("content_flags")
