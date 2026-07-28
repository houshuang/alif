import sys
from unittest.mock import patch

from app.models import Sentence
from app.services.llm import SentenceReviewResult
from scripts import review_existing_sentences


def test_incomplete_quality_review_leaves_sentence_for_retry(db_session):
    sentence = Sentence(
        id=990_001,
        arabic_text="الْكِتَابُ جَدِيدٌ.",
        english_translation="The book is new.",
        source="corpus",
        is_active=True,
    )
    db_session.add(sentence)
    db_session.commit()

    with (
        patch.object(
            sys,
            "argv",
            [
                "review_existing_sentences.py",
                "--ids",
                str(sentence.id),
            ],
        ),
        patch.object(
            review_existing_sentences,
            "review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=False,
                    translation_correct=False,
                    reason="quality review unavailable",
                    review_completed=False,
                )
            ],
        ),
    ):
        review_existing_sentences.main()

    db_session.refresh(sentence)
    assert sentence.is_active is True
    assert sentence.quality_reviewed_at is None
    assert sentence.quality_natural is None
    assert sentence.quality_translation_correct is None
