"""Pair-level confusion lessons and practice evidence; no FSRS backfill."""
from alembic import op
import sqlalchemy as sa
revision = "a2d4f6c8b019"
down_revision = "f3b5d7e9a1c4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("confusion_pairs", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lemma_a_id", sa.Integer(), sa.ForeignKey("lemmas.lemma_id"), nullable=False),
        sa.Column("lemma_b_id", sa.Integer(), sa.ForeignKey("lemmas.lemma_id"), nullable=False),
        sa.Column("lesson_json", sa.JSON()), sa.Column("generation_state", sa.String(20), nullable=False),
        sa.Column("generation_started_at", sa.DateTime()), sa.Column("generation_error", sa.Text()),
        sa.UniqueConstraint("lemma_a_id", "lemma_b_id", name="uq_confusion_pair_members"))
    op.create_table("confusion_links", sa.Column("capture_id", sa.Integer(), sa.ForeignKey("confusion_captures.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("pair_id", sa.Integer(), sa.ForeignKey("confusion_pairs.id")),
        sa.Column("state", sa.String(20), nullable=False), sa.Column("candidates_json", sa.JSON()),
        sa.Column("resolution_method", sa.String(20)), sa.Column("resolved_at", sa.DateTime()))
    op.create_table("confusion_attempts", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_attempt_id", sa.String(80), unique=True, nullable=False),
        sa.Column("pair_id", sa.Integer(), sa.ForeignKey("confusion_pairs.id"), nullable=False),
        sa.Column("session_id", sa.String(80), nullable=False), sa.Column("question_index", sa.Integer(), nullable=False),
        sa.Column("chosen_lemma_id", sa.Integer(), sa.ForeignKey("lemmas.lemma_id")),
        sa.Column("target_lemma_id", sa.Integer(), sa.ForeignKey("lemmas.lemma_id"), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False), sa.Column("assisted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    for table, fields in [("confusion_pairs", ["lemma_a_id", "lemma_b_id"]), ("confusion_links", ["pair_id"]), ("confusion_attempts", ["pair_id", "created_at"])]:
        for field in fields: op.create_index(f"ix_{table}_{field}", table, [field])


def downgrade():
    op.drop_table("confusion_attempts")
    op.drop_table("confusion_links")
    op.drop_table("confusion_pairs")
