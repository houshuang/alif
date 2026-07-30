import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.database import SessionLocal
from app.models import Sentence
from app.services.llm import (
    MOMO_PUBLISHED_ARABIC_REVIEW_CONTEXT,
    SentenceReviewResult,
)
from scripts import review_existing_sentences


def test_incomplete_quality_review_leaves_sentence_for_retry(db_session):
    sentence = Sentence(
        id=990_001,
        arabic_text="الْكِتَابُ جَدِيدٌ.",
        english_translation="The book is new.",
        source="corpus",
        kind="momo_book",
        is_active=True,
    )
    db_session.add(sentence)
    db_session.commit()
    review_inputs: list[dict] = []

    def unavailable(inputs):
        review_inputs.extend(inputs)
        return [
            SentenceReviewResult(
                natural=False,
                translation_correct=False,
                reason="quality review unavailable",
                review_completed=False,
            )
        ]

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
            side_effect=unavailable,
        ),
    ):
        review_existing_sentences.main()

    db_session.refresh(sentence)
    assert sentence.is_active is True
    assert sentence.quality_reviewed_at is None
    assert sentence.quality_natural is None
    assert sentence.quality_translation_correct is None
    assert review_inputs == [
        {
            "arabic": sentence.arabic_text,
            "english": sentence.english_translation,
            "review_context": MOMO_PUBLISHED_ARABIC_REVIEW_CONTEXT,
        }
    ]


_CAS_FIELDS = (
    ("arabic_text", "نَصٌّ مُعَدَّلٌ."),
    ("english_translation", "Concurrently revised."),
    ("source", "book"),
    ("kind", "other_book"),
    ("is_active", False),
    (
        "quality_reviewed_at",
        datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
    ),
    ("quality_natural", True),
    ("quality_translation_correct", True),
    ("quality_reason", "concurrent reviewer"),
)


def _cas_state(sentence: Sentence) -> dict:
    reviewed_at = sentence.quality_reviewed_at
    if reviewed_at is not None and reviewed_at.tzinfo is not None:
        reviewed_at = reviewed_at.astimezone(timezone.utc).replace(tzinfo=None)
    return {
        "arabic_text": sentence.arabic_text,
        "english_translation": sentence.english_translation,
        "source": sentence.source,
        "kind": sentence.kind,
        "is_active": bool(sentence.is_active),
        "quality_reviewed_at": reviewed_at,
        "quality_natural": sentence.quality_natural,
        "quality_translation_correct": (
            sentence.quality_translation_correct
        ),
        "quality_reason": sentence.quality_reason,
    }


@pytest.mark.parametrize(("changed_field", "changed_value"), _CAS_FIELDS)
def test_completed_stale_verdict_preserves_each_concurrent_mutation(
    db_session,
    changed_field,
    changed_value,
    capsys,
):
    sentence = Sentence(
        id=990_002,
        arabic_text="الْكِتَابُ جَدِيدٌ.",
        english_translation="The book is new.",
        source="corpus",
        kind="momo_book",
        is_active=True,
    )
    db_session.add(sentence)
    db_session.commit()
    concurrent_state: dict = {}

    def mutate_then_reject(_inputs):
        concurrent = SessionLocal()
        try:
            row = concurrent.get(Sentence, sentence.id)
            setattr(row, changed_field, changed_value)
            concurrent.commit()
            concurrent.refresh(row)
            concurrent_state.update(_cas_state(row))
        finally:
            concurrent.close()
        return [
            SentenceReviewResult(
                natural=False,
                translation_correct=False,
                reason="stale rejection",
            )
        ]

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
            side_effect=mutate_then_reject,
        ),
    ):
        review_existing_sentences.main()

    db_session.expire_all()
    stored = db_session.get(Sentence, sentence.id)
    assert _cas_state(stored) == concurrent_state
    output = capsys.readouterr().out
    assert f"RETRY id={sentence.id}: sentence changed" in output
    assert "Reviewed: 0, Failed: 0, Retired: 0, Retry: 1" in output


def test_unchanged_completed_rejection_stamps_and_retires(
    db_session,
    capsys,
):
    sentence = Sentence(
        id=990_003,
        arabic_text="هَذِهِ جُمْلَةٌ غَيْرُ طَبِيعِيَّةٍ.",
        english_translation="This is an unnatural sentence.",
        source="llm",
        kind="generated",
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
                    translation_correct=True,
                    reason="awkward fragment",
                )
            ],
        ),
    ):
        review_existing_sentences.main()

    db_session.expire_all()
    stored = db_session.get(Sentence, sentence.id)
    assert stored.is_active is False
    assert stored.quality_reviewed_at is not None
    assert stored.quality_natural is False
    assert stored.quality_translation_correct is True
    assert stored.quality_reason == "awkward fragment"
    output = capsys.readouterr().out
    assert "Reviewed: 1, Failed: 1, Retired: 1, Retry: 0" in output
