"""Add learner_settings table for topical learning cycles

Revision ID: p6h0i1j2k789
Revises: o5g9h0i1j678
Create Date: 2026-02-12
"""
from alembic import op
import sqlalchemy as sa

revision = "p6h0i1j2k789"
down_revision = "o5g9h0i1j678"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learner_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("active_topic", sa.String(30), nullable=True),
        sa.Column("topic_started_at", sa.DateTime(), nullable=True),
        sa.Column("words_introduced_in_topic", sa.Integer(), server_default="0"),
        sa.Column("topic_history_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("learner_settings")
