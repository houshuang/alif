"""Snap page history."""
from alembic import op
import sqlalchemy as sa

revision = "f2b4d6e8a0c3"
down_revision = "e1a3c5d7f9b2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "snap_pages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("arabic_text", sa.Text(), nullable=False),
        sa.Column("translation_en", sa.Text(), nullable=False, server_default=""),
        sa.Column("sentences_json", sa.JSON(), nullable=True),
        sa.Column("words_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_snap_pages_created_at", "snap_pages", ["created_at"])


def downgrade():
    op.drop_index("ix_snap_pages_created_at", table_name="snap_pages")
    op.drop_table("snap_pages")
