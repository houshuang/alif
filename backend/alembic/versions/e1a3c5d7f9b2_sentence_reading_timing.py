"""Persist versioned sentence reading timing without reinterpreting legacy times."""
from alembic import op
import sqlalchemy as sa
revision = "e1a3c5d7f9b2"
down_revision = "d0f2b4c6e8a1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sentence_review_log", sa.Column("reading_timing_json", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("sentence_review_log") as batch:
        batch.drop_column("reading_timing_json")
