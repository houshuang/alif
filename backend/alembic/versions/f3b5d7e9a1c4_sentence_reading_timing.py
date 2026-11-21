"""Persist versioned sentence reading timing without reinterpreting legacy times."""
from alembic import op
import sqlalchemy as sa
revision = "f3b5d7e9a1c4"
down_revision = "c8e1a3f5b7d9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sentence_review_log", sa.Column("reading_timing_json", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("sentence_review_log") as batch:
        batch.drop_column("reading_timing_json")
