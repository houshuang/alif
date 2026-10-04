"""Explicit per-text reading targets (novel sprint)."""
from alembic import op
import sqlalchemy as sa

revision = "d0f2b4c6e8a1"
down_revision = "c9e1a3b5d7f0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reading_targets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("lemma_id", sa.Integer(), sa.ForeignKey("lemmas.lemma_id"), nullable=False),
        sa.Column("program", sa.String(60), nullable=False),
        sa.Column("chapter", sa.Integer(), nullable=True),
        sa.Column("text_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("lemma_id", "program", name="uq_reading_target_lemma_program"),
    )
    op.create_index("ix_reading_targets_lemma_id", "reading_targets", ["lemma_id"])
    op.create_index("ix_reading_targets_program", "reading_targets", ["program"])


def downgrade():
    op.drop_index("ix_reading_targets_program", table_name="reading_targets")
    op.drop_index("ix_reading_targets_lemma_id", table_name="reading_targets")
    op.drop_table("reading_targets")
