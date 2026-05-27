"""Add root_focus_id and kind to sentences for root-showcase generation.

Adds two nullable columns to support root-saturation sentences (one sentence
packs multiple derivations of the same Arabic root). `root_focus_id` lets
the selector and analytics tag/filter showcase sentences by which root they
target. `kind` distinguishes 'root_showcase' from default LLM/book/etc.

Revision ID: b2d4f6a8c0e2
Revises: f8a9b0c1d234
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa


revision = "b2d4f6a8c0e2"
down_revision = "f8a9b0c1d234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # FK constraint omitted at the SQLite level to match the codebase pattern
    # (217c2a674f92 etc.). SQLAlchemy enforces the ForeignKey via the model.
    with op.batch_alter_table("sentences", schema=None) as batch_op:
        batch_op.add_column(sa.Column("root_focus_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("kind", sa.String(30), nullable=True))
    op.create_index(
        "ix_sentences_root_focus_id",
        "sentences",
        ["root_focus_id"],
    )
    op.create_index(
        "ix_sentences_kind",
        "sentences",
        ["kind"],
    )


def downgrade() -> None:
    op.drop_index("ix_sentences_kind", "sentences")
    op.drop_index("ix_sentences_root_focus_id", "sentences")
    with op.batch_alter_table("sentences", schema=None) as batch_op:
        batch_op.drop_column("kind")
        batch_op.drop_column("root_focus_id")
