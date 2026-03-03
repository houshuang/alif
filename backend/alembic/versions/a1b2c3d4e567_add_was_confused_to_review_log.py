"""Add was_confused column to review_log.

Revision ID: a1b2c3d4e567
Revises: z6g7h8i9j012
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e567"
down_revision = "z6g7h8i9j012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_log",
        sa.Column("was_confused", sa.Boolean(), server_default="0", nullable=True),
    )
    # Backfill: mark existing rating=2 reviews as confused
    op.execute("UPDATE review_log SET was_confused = 1 WHERE rating = 2")


def downgrade() -> None:
    op.drop_column("review_log", "was_confused")
