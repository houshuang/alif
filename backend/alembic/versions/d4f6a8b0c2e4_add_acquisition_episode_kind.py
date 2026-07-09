"""Add acquisition episode kind to user lemma knowledge.

``source`` records curriculum provenance, while this nullable column records
why the current acquisition episode started.  Existing rows deliberately stay
NULL; application queries retain a legacy fallback for historical
``source='leech_reintro'`` rows.

Revision ID: d4f6a8b0c2e4
Revises: c3e5f7a9b1d4
Create Date: 2026-07-09
"""

from alembic import op
import sqlalchemy as sa


revision = "d4f6a8b0c2e4"
down_revision = "c3e5f7a9b1d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_lemma_knowledge", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("acquisition_episode_kind", sa.String(20), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("user_lemma_knowledge", schema=None) as batch_op:
        batch_op.drop_column("acquisition_episode_kind")
