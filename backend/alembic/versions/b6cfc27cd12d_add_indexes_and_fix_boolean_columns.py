"""add_indexes_and_fix_boolean_columns

Revision ID: b6cfc27cd12d
Revises: f7c1a5b6d834
Create Date: 2026-02-08 21:49:24.102258

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6cfc27cd12d'
down_revision: Union[str, Sequence[str], None] = 'f7c1a5b6d834'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add indexes on hot columns and fix boolean column types."""
    with op.batch_alter_table('review_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_review_log_reviewed_at'), ['reviewed_at'], unique=False)

    with op.batch_alter_table('sentence_words', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sentence_words_sentence_id'), ['sentence_id'], unique=False)

    with op.batch_alter_table('sentences', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sentences_target_lemma_id'), ['target_lemma_id'], unique=False)

    with op.batch_alter_table('stories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_stories_status'), ['status'], unique=False)

    with op.batch_alter_table('story_words', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_story_words_story_id'), ['story_id'], unique=False)

    with op.batch_alter_table('user_lemma_knowledge', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_lemma_knowledge_knowledge_state'), ['knowledge_state'], unique=False)


def downgrade() -> None:
    """Remove indexes."""
    with op.batch_alter_table('user_lemma_knowledge', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_lemma_knowledge_knowledge_state'))

    with op.batch_alter_table('story_words', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_story_words_story_id'))

    with op.batch_alter_table('stories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_stories_status'))

    with op.batch_alter_table('sentences', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sentences_target_lemma_id'))

    with op.batch_alter_table('sentence_words', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sentence_words_sentence_id'))

    with op.batch_alter_table('review_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_review_log_reviewed_at'))
