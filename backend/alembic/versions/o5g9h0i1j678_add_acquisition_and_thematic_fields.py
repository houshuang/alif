"""Add acquisition, thematic, and etymology fields

Revision ID: o5g9h0i1j678
Revises: 66513200edc1
Create Date: 2026-02-12
"""
from alembic import op
import sqlalchemy as sa

revision = "o5g9h0i1j678"
down_revision = "66513200edc1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # UserLemmaKnowledge — acquisition fields
    op.add_column("user_lemma_knowledge", sa.Column("acquisition_box", sa.Integer(), nullable=True))
    op.add_column("user_lemma_knowledge", sa.Column("acquisition_next_due", sa.DateTime(), nullable=True))
    op.add_column("user_lemma_knowledge", sa.Column("acquisition_started_at", sa.DateTime(), nullable=True))
    op.add_column("user_lemma_knowledge", sa.Column("graduated_at", sa.DateTime(), nullable=True))
    op.add_column("user_lemma_knowledge", sa.Column("leech_suspended_at", sa.DateTime(), nullable=True))

    # Lemma — thematic + etymology
    op.add_column("lemmas", sa.Column("thematic_domain", sa.String(30), nullable=True))
    op.add_column("lemmas", sa.Column("etymology_json", sa.JSON(), nullable=True))

    # ReviewLog — acquisition flag
    op.add_column("review_log", sa.Column("is_acquisition", sa.Boolean(), server_default="0"))

    # Index for acquisition due queries
    op.create_index("ix_ulk_acquisition_due", "user_lemma_knowledge", ["acquisition_box", "acquisition_next_due"])


def downgrade() -> None:
    op.drop_index("ix_ulk_acquisition_due", table_name="user_lemma_knowledge")
    op.drop_column("review_log", "is_acquisition")
    op.drop_column("lemmas", "etymology_json")
    op.drop_column("lemmas", "thematic_domain")
    op.drop_column("user_lemma_knowledge", "leech_suspended_at")
    op.drop_column("user_lemma_knowledge", "graduated_at")
    op.drop_column("user_lemma_knowledge", "acquisition_started_at")
    op.drop_column("user_lemma_knowledge", "acquisition_next_due")
    op.drop_column("user_lemma_knowledge", "acquisition_box")
