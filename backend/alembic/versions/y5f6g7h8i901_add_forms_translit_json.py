"""Add forms_translit_json to lemmas.

Revision ID: y5f6g7h8i901
Revises: 3d88231674a2
Create Date: 2026-02-25
"""

from alembic import op
import sqlalchemy as sa

revision = "y5f6g7h8i901"
down_revision = "3d88231674a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("lemmas") as batch_op:
        batch_op.add_column(
            sa.Column("forms_translit_json", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("lemmas") as batch_op:
        batch_op.drop_column("forms_translit_json")
