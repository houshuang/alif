"""normalize_lemma_ar_bare_alef

Normalize alef variants (أإآٱ → ا) in lemmas.lemma_ar_bare to prevent
lookup mismatches in correct_mapping(). Affects ~179 rows (9% of lemmas).

Revision ID: 3c8d28cc6e9b
Revises: 67321144f50b
Create Date: 2026-04-09 21:54:47.446252

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c8d28cc6e9b'
down_revision: Union[str, Sequence[str], None] = '67321144f50b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Normalize alef variants in lemma_ar_bare: أإآٱ → ا
    # Chain REPLACE calls for each variant
    op.execute(
        """
        UPDATE lemmas
        SET lemma_ar_bare = REPLACE(
            REPLACE(
                REPLACE(
                    REPLACE(lemma_ar_bare, 'أ', 'ا'),
                'إ', 'ا'),
            'آ', 'ا'),
        'ٱ', 'ا')
        WHERE lemma_ar_bare LIKE '%أ%'
           OR lemma_ar_bare LIKE '%إ%'
           OR lemma_ar_bare LIKE '%آ%'
           OR lemma_ar_bare LIKE '%ٱ%'
        """
    )


def downgrade() -> None:
    # Data-only migration — cannot reconstruct original alef forms
    pass
