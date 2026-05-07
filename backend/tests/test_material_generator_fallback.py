from unittest.mock import patch

from app.models import Lemma, UserLemmaKnowledge
from app.services import material_generator


def test_self_correct_batch_falls_back_to_legacy_generation(db_session):
    """Claude-tool self-correct is CLI-only; quota failures must use API-backed legacy generation."""
    db_session.add_all([
        Lemma(
            lemma_id=1,
            lemma_ar="كِتَابٌ",
            lemma_ar_bare="كتاب",
            gloss_en="book",
            pos="noun",
        ),
        Lemma(
            lemma_id=2,
            lemma_ar="بَيْتٌ",
            lemma_ar_bare="بيت",
            gloss_en="house",
            pos="noun",
        ),
        UserLemmaKnowledge(lemma_id=1, knowledge_state="acquiring"),
        UserLemmaKnowledge(lemma_id=2, knowledge_state="known", fsrs_card_json={"stability": 10}),
    ])
    db_session.commit()

    with (
        patch("app.services.material_generator._generate_via_self_correct") as self_correct,
        patch("app.services.material_generator._generate_via_legacy_batch") as legacy,
    ):
        self_correct.side_effect = RuntimeError(
            "claude exited 1: You're out of extra usage · resets later"
        )
        legacy.return_value = []

        result = material_generator.batch_generate_material([1], count_per_word=1)

    assert result["generated"] == 0
    assert result["words_failed"] == [1]
    self_correct.assert_called_once()
    legacy.assert_called_once()
