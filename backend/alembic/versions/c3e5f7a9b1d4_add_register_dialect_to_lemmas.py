"""Add register and dialect columns to lemmas.

Two nullable columns recording the register (neutral|literary|colloquial|
vulgar|clinical) and dialect (msa|gulf|egyptian|levantine|mixed) of words
imported from external text via the Bookifier glossary path (OOV/dialectal
vocabulary). NULL for the standard MSA curriculum — purely additive, no
behavior change for existing rows.

Revision ID: c3e5f7a9b1d4
Revises: b2d4f6a8c0e2
Create Date: 2026-06-13
"""

from alembic import op
import sqlalchemy as sa


revision = "c3e5f7a9b1d4"
down_revision = "b2d4f6a8c0e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("lemmas", schema=None) as batch_op:
        batch_op.add_column(sa.Column("register", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("dialect", sa.String(20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("lemmas", schema=None) as batch_op:
        batch_op.drop_column("dialect")
        batch_op.drop_column("register")
