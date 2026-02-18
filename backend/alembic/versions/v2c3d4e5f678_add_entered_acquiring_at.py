"""Add entered_acquiring_at to user_lemma_knowledge.

Revision ID: v2c3d4e5f678
Revises: b1b45317288d
Create Date: 2026-02-18
"""

from alembic import op
import sqlalchemy as sa

revision = "v2c3d4e5f678"
down_revision = "b1b45317288d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_lemma_knowledge") as batch_op:
        batch_op.add_column(
            sa.Column("entered_acquiring_at", sa.DateTime(), nullable=True)
        )
    # Backfill: use earliest review_log entry per lemma for existing acquiring words
    op.execute("""
        UPDATE user_lemma_knowledge
        SET entered_acquiring_at = (
            SELECT MIN(reviewed_at) FROM review_log
            WHERE review_log.lemma_id = user_lemma_knowledge.lemma_id
        )
        WHERE knowledge_state = 'acquiring'
        AND entered_acquiring_at IS NULL
    """)


def downgrade() -> None:
    with op.batch_alter_table("user_lemma_knowledge") as batch_op:
        batch_op.drop_column("entered_acquiring_at")
