"""Add lemmas.word_card_json (derivation chain, form glosses, set phrases).

Revision ID: cb2d3e4f5a67
Revises: ba1c2d3e4f56
Create Date: 2026-11-25
"""

from alembic import op
import sqlalchemy as sa

revision = "cb2d3e4f5a67"
down_revision = "ba1c2d3e4f56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("lemmas") as batch:
        batch.add_column(sa.Column("word_card_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("lemmas") as batch:
        batch.drop_column("word_card_json")
