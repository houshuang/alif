"""Progress for dialect listening clips."""
from alembic import op
import sqlalchemy as sa

revision = "e1a3c5d7f9b2"
down_revision = "d0f2b4c6e8a1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "clip_progress",
        sa.Column("clip_id", sa.String(40), primary_key=True),
        sa.Column("stage", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("pass_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_passed_at", sa.DateTime(), nullable=True),
        sa.Column("next_check_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("clip_progress")
