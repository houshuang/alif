"""Repair Jawad common-noun category.

Revision ID: e7f8a9b0c123
Revises: d2f6a7b8c901
Create Date: 2026-05-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7f8a9b0c123"
down_revision: Union[str, Sequence[str], None] = "d2f6a7b8c901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JAWAD_BARE = "جواد"
JAWAD_ROOT = "ج.و.د"


def upgrade() -> None:
    conn = op.get_bind()

    root_id = conn.execute(
        sa.text("SELECT root_id FROM roots WHERE root = :root"),
        {"root": JAWAD_ROOT},
    ).scalar()
    if root_id is None:
        conn.execute(
            sa.text(
                "INSERT INTO roots (root, core_meaning_en) "
                "VALUES (:root, :core_meaning_en)"
            ),
            {
                "root": JAWAD_ROOT,
                "core_meaning_en": "quality, goodness; generosity",
            },
        )
        root_id = conn.execute(
            sa.text("SELECT root_id FROM roots WHERE root = :root"),
            {"root": JAWAD_ROOT},
        ).scalar()

    conn.execute(
        sa.text(
            """
            UPDATE lemmas
            SET pos = 'noun',
                word_category = NULL,
                root_id = :root_id
            WHERE lemma_ar_bare = :bare
              AND pos = 'noun_prop'
              AND word_category = 'proper_name'
              AND (
                    lower(coalesce(gloss_en, '')) LIKE '%horse%'
                 OR lower(coalesce(gloss_en, '')) LIKE '%steed%'
              )
            """
        ),
        {"bare": JAWAD_BARE, "root_id": root_id},
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE lemmas
            SET pos = 'noun_prop',
                word_category = 'proper_name'
            WHERE lemma_ar_bare = :bare
              AND pos = 'noun'
              AND word_category IS NULL
              AND (
                    lower(coalesce(gloss_en, '')) LIKE '%horse%'
                 OR lower(coalesce(gloss_en, '')) LIKE '%steed%'
              )
            """
        ),
        {"bare": JAWAD_BARE},
    )
