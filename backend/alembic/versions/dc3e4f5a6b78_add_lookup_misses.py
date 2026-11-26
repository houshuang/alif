"""Add lookup_misses (reading-tap reasons) and word_add_contexts.phrase_json.

Revision ID: dc3e4f5a6b78
Revises: cb2d3e4f5a67
Create Date: 2026-11-26
"""

from alembic import op
import sqlalchemy as sa

revision = "dc3e4f5a6b78"
down_revision = "cb2d3e4f5a67"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lookup_misses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("lemma_id", sa.Integer(), nullable=False, index=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("surface", sa.Text(), nullable=True),
        sa.Column("sentence_ar", sa.Text(), nullable=True),
        sa.Column("snap_id", sa.Integer(), nullable=True),
        sa.Column("causes_json", sa.JSON(), nullable=False),
        sa.Column("knowledge_state", sa.String(20), nullable=True),
        sa.Column("opens_miss", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    with op.batch_alter_table("word_add_contexts") as batch:
        batch.add_column(sa.Column("phrase_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("word_add_contexts") as batch:
        batch.drop_column("phrase_json")
    op.drop_table("lookup_misses")
