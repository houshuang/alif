"""Add root enrichment_json column and pattern_info table.

Revision ID: z6g7h8i9j012
Revises: y5f6g7h8i901
Create Date: 2026-02-25
"""

from alembic import op
import sqlalchemy as sa

revision = "z6g7h8i9j012"
down_revision = "y5f6g7h8i901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("roots") as batch_op:
        batch_op.add_column(
            sa.Column("enrichment_json", sa.JSON(), nullable=True)
        )

    op.create_table(
        "pattern_info",
        sa.Column("wazn", sa.String(30), primary_key=True),
        sa.Column("wazn_meaning", sa.Text(), nullable=True),
        sa.Column("enrichment_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("pattern_info")
    with op.batch_alter_table("roots") as batch_op:
        batch_op.drop_column("enrichment_json")
