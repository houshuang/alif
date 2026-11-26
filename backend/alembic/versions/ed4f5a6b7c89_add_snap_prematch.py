"""Add snap_pages.prematch_json (every page word matched and sense-checked).

Revision ID: ed4f5a6b7c89
Revises: dc3e4f5a6b78
Create Date: 2026-11-26
"""

from alembic import op
import sqlalchemy as sa

revision = "ed4f5a6b7c89"
down_revision = "dc3e4f5a6b78"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("snap_pages") as batch:
        batch.add_column(sa.Column("prematch_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("snap_pages") as batch:
        batch.drop_column("prematch_json")
