"""add indices for session end performance

Revision ID: 7aefcb0433a9
Revises: 044cfcef0a96
Create Date: 2026-03-07 19:37:38.672130

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '7aefcb0433a9'
down_revision: Union[str, Sequence[str], None] = '044cfcef0a96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add indices on columns used by session-end card queries."""
    with op.batch_alter_table('review_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_review_log_lemma_id'), ['lemma_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_log_session_id'), ['session_id'], unique=False)

    with op.batch_alter_table('sentence_review_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sentence_review_log_reviewed_at'), ['reviewed_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_sentence_review_log_session_id'), ['session_id'], unique=False)

    with op.batch_alter_table('user_lemma_knowledge', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_lemma_knowledge_graduated_at'), ['graduated_at'], unique=False)


def downgrade() -> None:
    """Remove session-end performance indices."""
    with op.batch_alter_table('user_lemma_knowledge', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_lemma_knowledge_graduated_at'))

    with op.batch_alter_table('sentence_review_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sentence_review_log_session_id'))
        batch_op.drop_index(batch_op.f('ix_sentence_review_log_reviewed_at'))

    with op.batch_alter_table('review_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_review_log_session_id'))
        batch_op.drop_index(batch_op.f('ix_review_log_lemma_id'))
