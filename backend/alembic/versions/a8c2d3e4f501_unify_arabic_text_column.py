"""Unify arabic_text column — drop arabic_diacritized.

Backfills arabic_text with the diacritized form where they diverge
(book/corpus/michel_thomas rows had arabic_text stripped of diacritics),
then drops the now-redundant arabic_diacritized column. After this
migration, `sentences.arabic_text` always holds the fully diacritized
form; callers that need plain text call `strip_diacritics()` at query
time (dedup and validators already do this).

Revision ID: a8c2d3e4f501
Revises: 3c8d28cc6e9b
Create Date: 2026-04-17 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8c2d3e4f501"
down_revision: Union[str, Sequence[str], None] = "3c8d28cc6e9b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE sentences
           SET arabic_text = arabic_diacritized
         WHERE arabic_diacritized IS NOT NULL
           AND arabic_diacritized != ''
           AND arabic_diacritized != arabic_text
        """
    )
    with op.batch_alter_table("sentences") as batch:
        batch.drop_column("arabic_diacritized")


def downgrade() -> None:
    with op.batch_alter_table("sentences") as batch:
        batch.add_column(sa.Column("arabic_diacritized", sa.Text(), nullable=True))
    op.execute("UPDATE sentences SET arabic_diacritized = arabic_text")
