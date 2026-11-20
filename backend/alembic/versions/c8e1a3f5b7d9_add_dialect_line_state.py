"""Listening state for Dialect Lines."""
from alembic import op
import sqlalchemy as sa

revision = "c8e1a3f5b7d9"
down_revision = "d4f6b8c0e2a5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dialect_line_state",
        sa.Column("line_id", sa.String(64), primary_key=True),
        sa.Column("hard", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("plays", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("arabic_reveals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("english_reveals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("dialect_line_state")
