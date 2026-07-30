"""Regression tests for exact-identity handling in story-word backfill."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.sentence_validator import build_lemma_lookup
from scripts import backfill_story_words as backfill


def _collision_lookup(*, include_destinations: bool):
    now = datetime.now(timezone.utc)
    lemmas = [
        SimpleNamespace(
            lemma_id=3711,
            lemma_ar="نَسِيَ",
            lemma_ar_bare="نسي",
            gloss_en="to forget",
            pos="verb",
            forms_json={
                "active_participle": "نَاسٍ",
                "imperative": "اِنْسَ",
            },
            gates_completed_at=now,
        ),
        SimpleNamespace(
            lemma_id=2189,
            lemma_ar="فَقْد",
            lemma_ar_bare="فقد",
            gloss_en="loss",
            pos="noun",
            forms_json=None,
            gates_completed_at=now,
        ),
    ]
    if include_destinations:
        lemmas.extend([
            SimpleNamespace(
                lemma_id=270,
                lemma_ar="نَاسٌ",
                lemma_ar_bare="ناس",
                gloss_en="people",
                pos="noun",
                forms_json=None,
                gates_completed_at=now,
            ),
            SimpleNamespace(
                lemma_id=2054,
                lemma_ar="قَدْ",
                lemma_ar_bare="قد",
                gloss_en="indeed",
                pos="particle",
                forms_json=None,
                gates_completed_at=now,
            ),
        ])
    return build_lemma_lookup(lemmas)


def test_backfill_resolves_exact_aliases_before_bare_or_camel(monkeypatch):
    lookup = _collision_lookup(include_destinations=True)
    assert lookup["انس"] == 3711
    assert lookup["فقد"] == 2189

    def fail(*_args, **_kwargs):
        raise AssertionError("exact alias reached a lossy backfill fallback")

    monkeypatch.setattr(backfill, "lookup_lemma_id", fail)
    monkeypatch.setattr(backfill, "find_best_db_match", fail)

    people = backfill._resolve_story_word_from_existing_inventory(
        "أُنَاسٌ",
        lookup,
        set(lookup),
    )
    particle = backfill._resolve_story_word_from_existing_inventory(
        "فَقَدْ",
        lookup,
        set(lookup),
    )

    assert people == (270, False, True)
    assert particle == (2054, True, True)


def test_backfill_leaves_unresolved_exact_aliases_closed(monkeypatch):
    lookup = _collision_lookup(include_destinations=False)

    def fail(*_args, **_kwargs):
        raise AssertionError("unresolved exact alias reached lookup or CAMeL")

    monkeypatch.setattr(backfill, "lookup_lemma_id", fail)
    monkeypatch.setattr(backfill, "find_best_db_match", fail)

    people = backfill._resolve_story_word_from_existing_inventory(
        "أُنَاسٌ",
        lookup,
        set(lookup),
    )
    particle = backfill._resolve_story_word_from_existing_inventory(
        "فَقَدْ",
        lookup,
        set(lookup),
    )

    assert people == (None, None, True)
    assert particle == (None, None, True)
