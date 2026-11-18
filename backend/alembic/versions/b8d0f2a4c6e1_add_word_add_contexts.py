"""Word add contexts: the sentence each added word came from."""
from alembic import op
import sqlalchemy as sa

revision = "b8d0f2a4c6e1"
down_revision = "a7c9e1f3b5d2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "word_add_contexts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("lemma_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("surface", sa.Text(), nullable=True),
        sa.Column("sentence_ar", sa.Text(), nullable=False),
        sa.Column("snap_id", sa.Integer(), nullable=True),
        sa.Column("preread_set_id", sa.Integer(), nullable=True),
        sa.Column("gloss_at_add", sa.Text(), nullable=True),
        sa.Column("check_verdict", sa.String(20), nullable=True),
        sa.Column("check_json", sa.JSON(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_word_add_contexts_created_at", "word_add_contexts", ["created_at"])
    op.create_index("ix_word_add_contexts_lemma_id", "word_add_contexts", ["lemma_id"])


def downgrade():
    op.drop_index("ix_word_add_contexts_lemma_id", table_name="word_add_contexts")
    op.drop_index("ix_word_add_contexts_created_at", table_name="word_add_contexts")
    op.drop_table("word_add_contexts")
