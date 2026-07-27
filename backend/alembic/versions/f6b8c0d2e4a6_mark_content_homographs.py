"""Mark audited lexical homographs of function words.

Revision ID: f6b8c0d2e4a6
Revises: e5a7b9c1d3f5
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa


revision = "f6b8c0d2e4a6"
down_revision = "e5a7b9c1d3f5"
branch_labels = None
depends_on = None


# Production IDs are guarded by normalized bare form and a distinctive gloss
# fragment. A guard mismatch is deliberately a no-op, never a broad rewrite.
_CONTENT_HOMOGRAPHS = (
    (76, "ام", "mother"),
    (554, "بان", "separate"),
    (615, "مني", "semen"),
    (663, "اذن", "ear"),
    (943, "ان", "time"),
    (976, "مثل", "resemble"),
    (1107, "اما", "meow"),
    (1121, "لهم", "greedy"),
)


def upgrade() -> None:
    op.add_column(
        "lemmas",
        sa.Column("function_word_override", sa.Boolean(), nullable=True),
    )
    connection = op.get_bind()
    for lemma_id, bare, gloss_fragment in _CONTENT_HOMOGRAPHS:
        connection.execute(
            sa.text(
                """
            UPDATE lemmas
               SET function_word_override = 0
             WHERE lemma_id = :lemma_id
               AND lemma_ar_bare = :bare
               AND lower(coalesce(gloss_en, '')) LIKE :gloss
               AND function_word_override IS NULL
            """
            ),
            {
                "lemma_id": lemma_id,
                "bare": bare,
                "gloss": f"%{gloss_fragment}%",
            },
        )


def downgrade() -> None:
    op.drop_column("lemmas", "function_word_override")
