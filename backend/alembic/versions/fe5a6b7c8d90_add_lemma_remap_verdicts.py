"""Add lemma_remap_verdicts (judged moves of stored word links to another lemma).

Revision ID: fe5a6b7c8d90
Revises: ed4f5a6b7c89
Create Date: 2026-11-26
"""

from alembic import op
import sqlalchemy as sa

revision = "fe5a6b7c8d90"
down_revision = "ed4f5a6b7c89"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lemma_remap_verdicts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("old_lemma_id", sa.Integer(), nullable=False),
        sa.Column("new_lemma_id", sa.Integer(), nullable=False),
        sa.Column("surface_key", sa.Text(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("judged_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("old_lemma_id", "new_lemma_id", "surface_key", name="uq_lemma_remap_verdict"),
    )


def downgrade() -> None:
    op.drop_table("lemma_remap_verdicts")
