"""Add wazn and wazn_meaning to lemmas.

Revision ID: x4e5f6g7h890
Revises: w3d4e5f6g789
Create Date: 2026-02-21
"""

from alembic import op
import sqlalchemy as sa

revision = "x4e5f6g7h890"
down_revision = "w3d4e5f6g789"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("lemmas") as batch_op:
        batch_op.add_column(
            sa.Column("wazn", sa.String(30), nullable=True)
        )
        batch_op.add_column(
            sa.Column("wazn_meaning", sa.Text(), nullable=True)
        )
        batch_op.create_index("ix_lemmas_wazn", ["wazn"])


def downgrade() -> None:
    with op.batch_alter_table("lemmas") as batch_op:
        batch_op.drop_index("ix_lemmas_wazn")
        batch_op.drop_column("wazn_meaning")
        batch_op.drop_column("wazn")
