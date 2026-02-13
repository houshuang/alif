"""Add memory_hooks_json to lemmas

Revision ID: r8j3k4l5m901
Revises: q7i2j3k4l890
Create Date: 2026-02-13
"""
from alembic import op
import sqlalchemy as sa

revision = "r8j3k4l5m901"
down_revision = "q7i2j3k4l890"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("lemmas", sa.Column("memory_hooks_json", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("lemmas", "memory_hooks_json")
