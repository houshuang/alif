"""expand grammar features and add grammar tracking columns

Revision ID: i9a3b4c5d012
Revises: h7f2a8b3c901
Create Date: 2026-02-09 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "i9a3b4c5d012"
down_revision: Union[str, None] = "h7f2a8b3c901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add form_change_type to grammar_features
    with op.batch_alter_table("grammar_features") as batch_op:
        batch_op.add_column(sa.Column("form_change_type", sa.String(20), nullable=True))

    # Add introduced_at and times_confused to user_grammar_exposure
    with op.batch_alter_table("user_grammar_exposure") as batch_op:
        batch_op.add_column(sa.Column("introduced_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("times_confused", sa.Integer(), server_default="0", nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_grammar_exposure") as batch_op:
        batch_op.drop_column("times_confused")
        batch_op.drop_column("introduced_at")

    with op.batch_alter_table("grammar_features") as batch_op:
        batch_op.drop_column("form_change_type")
