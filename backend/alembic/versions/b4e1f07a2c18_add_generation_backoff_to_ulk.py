"""Add generation backoff tracking to UserLemmaKnowledge.

Tracks per-lemma consecutive generation failures so the cron + warm-cache
loops can skip words that chronically fail to produce valid sentences.
After 3 consecutive 0-result attempts, a 7-day backoff is set and the
word is excluded from `words_needing` / gap computations until the
timestamp expires or a later attempt succeeds.

Revision ID: b4e1f07a2c18
Revises: a8c2d3e4f501
Create Date: 2026-04-17 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4e1f07a2c18"
down_revision: Union[str, Sequence[str], None] = "a8c2d3e4f501"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user_lemma_knowledge") as batch:
        batch.add_column(sa.Column(
            "generation_failed_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ))
        batch.add_column(sa.Column(
            "generation_backoff_until",
            sa.DateTime(),
            nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("user_lemma_knowledge") as batch:
        batch.drop_column("generation_backoff_until")
        batch.drop_column("generation_failed_count")
