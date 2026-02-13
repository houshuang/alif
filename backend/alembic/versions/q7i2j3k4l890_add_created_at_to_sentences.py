"""Add created_at to sentences

Revision ID: q7i2j3k4l890
Revises: p6h0i1j2k789
Create Date: 2026-02-13
"""
from alembic import op
import sqlalchemy as sa

revision = "q7i2j3k4l890"
down_revision = "p6h0i1j2k789"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sentences", sa.Column("created_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("sentences", "created_at")
