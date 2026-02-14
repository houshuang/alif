"""Add leech_count to user_lemma_knowledge for graduated cooldown.

Revision ID: t0l5m6n7o123
Revises: s9k4l5m6n012
Create Date: 2026-02-14
"""

from alembic import op
import sqlalchemy as sa

revision = "t0l5m6n7o123"
down_revision = "s9k4l5m6n012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_lemma_knowledge") as batch_op:
        batch_op.add_column(
            sa.Column("leech_count", sa.Integer(), server_default="0", nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("user_lemma_knowledge") as batch_op:
        batch_op.drop_column("leech_count")
