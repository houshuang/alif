"""Pre-read batches."""
from alembic import op
import sqlalchemy as sa

revision = "a7c9e1f3b5d2"
down_revision = "f2b4d6e8a0c3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "preread_sets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="uploading"),
        sa.Column("pages_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_preread_sets_created_at", "preread_sets", ["created_at"])


def downgrade():
    op.drop_index("ix_preread_sets_created_at", table_name="preread_sets")
    op.drop_table("preread_sets")
