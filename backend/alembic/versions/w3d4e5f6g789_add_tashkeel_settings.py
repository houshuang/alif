"""Add tashkeel_mode and tashkeel_stability_threshold to learner_settings.

Revision ID: w3d4e5f6g789
Revises: v2c3d4e5f678
Create Date: 2026-02-20
"""

from alembic import op
import sqlalchemy as sa

revision = "w3d4e5f6g789"
down_revision = "v2c3d4e5f678"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("learner_settings") as batch_op:
        batch_op.add_column(
            sa.Column("tashkeel_mode", sa.String(10), server_default="always", nullable=True)
        )
        batch_op.add_column(
            sa.Column("tashkeel_stability_threshold", sa.Float(), server_default="30.0", nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("learner_settings") as batch_op:
        batch_op.drop_column("tashkeel_stability_threshold")
        batch_op.drop_column("tashkeel_mode")
