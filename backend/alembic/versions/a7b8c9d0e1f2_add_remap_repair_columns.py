"""Add repair columns to lemma_remap_verdicts (the word a "neither" link is).

Revision ID: a7b8c9d0e1f2
Revises: fe5a6b7c8d90
Create Date: 2026-11-26
"""

from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "fe5a6b7c8d90"
branch_labels = None
depends_on = None

_COLS = (
    ("repair_lemma_ar", sa.Text()),
    ("repair_pos", sa.Text()),
    ("repair_gloss", sa.Text()),
    ("repair_lemma_id", sa.Integer()),
    ("repair_checked_at", sa.DateTime()),
)


def upgrade() -> None:
    with op.batch_alter_table("lemma_remap_verdicts") as batch:
        for name, type_ in _COLS:
            batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("lemma_remap_verdicts") as batch:
        for name, _ in _COLS:
            batch.drop_column(name)
