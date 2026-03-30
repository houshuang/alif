"""add quranic verses tables

Revision ID: 83b0dd62728d
Revises: c261385e96be
Create Date: 2026-03-30 09:48:15.880598

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '83b0dd62728d'
down_revision: Union[str, Sequence[str], None] = 'c261385e96be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('quranic_verses',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('surah', sa.Integer(), nullable=False),
        sa.Column('ayah', sa.Integer(), nullable=False),
        sa.Column('surah_name_ar', sa.Text(), nullable=True),
        sa.Column('surah_name_en', sa.Text(), nullable=True),
        sa.Column('arabic_text', sa.Text(), nullable=False),
        sa.Column('english_translation', sa.Text(), nullable=False),
        sa.Column('transliteration', sa.Text(), nullable=True),
        sa.Column('next_due', sa.DateTime(), nullable=True),
        sa.Column('srs_level', sa.Integer(), nullable=True),
        sa.Column('last_rating', sa.String(length=20), nullable=True),
        sa.Column('last_reviewed', sa.DateTime(), nullable=True),
        sa.Column('times_reviewed', sa.Integer(), nullable=True),
        sa.Column('introduced_at', sa.DateTime(), nullable=True),
        sa.Column('lemmatized_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('surah', 'ayah', name='uq_surah_ayah')
    )
    with op.batch_alter_table('quranic_verses', schema=None) as batch_op:
        batch_op.create_index('ix_quranic_verses_next_due', ['next_due'], unique=False)
        batch_op.create_index('ix_quranic_verses_surah', ['surah'], unique=False)

    op.create_table('quranic_verse_words',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('verse_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('surface_form', sa.Text(), nullable=False),
        sa.Column('lemma_id', sa.Integer(), nullable=True),
        sa.Column('is_function_word', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['lemma_id'], ['lemmas.lemma_id']),
        sa.ForeignKeyConstraint(['verse_id'], ['quranic_verses.id']),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('quranic_verse_words', schema=None) as batch_op:
        batch_op.create_index('ix_quranic_verse_words_verse_id', ['verse_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('quranic_verse_words', schema=None) as batch_op:
        batch_op.drop_index('ix_quranic_verse_words_verse_id')
    op.drop_table('quranic_verse_words')

    with op.batch_alter_table('quranic_verses', schema=None) as batch_op:
        batch_op.drop_index('ix_quranic_verses_surah')
        batch_op.drop_index('ix_quranic_verses_next_due')
    op.drop_table('quranic_verses')
