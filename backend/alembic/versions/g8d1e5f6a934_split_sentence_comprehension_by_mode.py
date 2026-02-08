"""split sentence comprehension by mode

Revision ID: g8d1e5f6a934
Revises: b6cfc27cd12d
Create Date: 2026-02-08 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g8d1e5f6a934"
down_revision: Union[str, None] = "b6cfc27cd12d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sentences", sa.Column("last_reading_comprehension", sa.String(20), nullable=True))
    op.add_column("sentences", sa.Column("last_reading_shown_at", sa.DateTime(), nullable=True))
    op.add_column("sentences", sa.Column("last_listening_comprehension", sa.String(20), nullable=True))
    op.add_column("sentences", sa.Column("last_listening_shown_at", sa.DateTime(), nullable=True))

    # Backfill: existing data is all reading mode
    op.execute("UPDATE sentences SET last_reading_comprehension = last_comprehension WHERE last_comprehension IS NOT NULL")
    op.execute("UPDATE sentences SET last_reading_shown_at = last_shown_at WHERE last_shown_at IS NOT NULL")

    with op.batch_alter_table("sentences") as batch_op:
        batch_op.drop_column("last_comprehension")
        batch_op.drop_column("last_shown_at")


def downgrade() -> None:
    op.add_column("sentences", sa.Column("last_comprehension", sa.String(20), nullable=True))
    op.add_column("sentences", sa.Column("last_shown_at", sa.DateTime(), nullable=True))

    op.execute("UPDATE sentences SET last_comprehension = last_reading_comprehension WHERE last_reading_comprehension IS NOT NULL")
    op.execute("UPDATE sentences SET last_shown_at = last_reading_shown_at WHERE last_reading_shown_at IS NOT NULL")

    with op.batch_alter_table("sentences") as batch_op:
        batch_op.drop_column("last_reading_comprehension")
        batch_op.drop_column("last_reading_shown_at")
        batch_op.drop_column("last_listening_comprehension")
        batch_op.drop_column("last_listening_shown_at")
