"""Add decomposition_note JSON column to lemmas.

Revision ID: aa7h8i9j0k12
Revises: z6g7h8i9j012
Create Date: 2026-04-24

Audit metadata for lemmas flagged during the lemma-decomposition audit
(Phase 2 Step 4b). Stores mle_misanalysis flags + reasons for orphans whose
CAMeL MLE decomposition proved wrong.
"""

from alembic import op
import sqlalchemy as sa

revision = "aa7h8i9j0k12"
down_revision = "z6g7h8i9j012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("lemmas") as batch_op:
        batch_op.add_column(
            sa.Column("decomposition_note", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("lemmas") as batch_op:
        batch_op.drop_column("decomposition_note")
