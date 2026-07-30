"""Exact running-text aliases must never become contextless citation rows."""

from datetime import datetime, timezone

from app.models import Lemma, UserLemmaKnowledge
from app.services.sentence_validator import build_lemma_lookup
from scripts import import_avp_a1
from scripts import import_duolingo
from scripts import import_wiktionary
from scripts.import_michel_thomas import _resolve_contextless_import_word
from scripts.import_scaffold_lemmas import import_scaffold_words


def _seed_people_destination(db_session) -> Lemma:
    """Seed the unique gated destination for أُنَاسٌ → نَاسٌ."""
    destination = Lemma(
        lemma_ar="نَاسٌ",
        lemma_ar_bare="ناس",
        gloss_en="people",
        pos="noun",
        source="seed",
        gates_completed_at=datetime.now(timezone.utc),
    )
    db_session.add(destination)
    db_session.commit()
    return destination


def _alias_entries() -> list[dict[str, str]]:
    """Return one resolved and one deliberately unresolved exact alias."""
    return [
        {
            "arabic": "أُنَاسٌ",
            "bare": "أناس",
            "gloss": "people",
            "pos": "noun",
        },
        {
            "arabic": "فَقَدْ",
            "bare": "فقد",
            "gloss": "so; already",
            "pos": "particle",
        },
    ]


def test_avp_import_skips_resolved_and_unresolved_exact_aliases(
    db_session,
    monkeypatch,
):
    destination = _seed_people_destination(db_session)
    entries = [
        {"arabic": row["arabic"], "english": row["gloss"]}
        for row in _alias_entries()
    ]
    monkeypatch.setattr(
        import_avp_a1,
        "fetch_vocab_data",
        lambda: {"NOUNS": entries},
    )

    result = import_avp_a1.run_import(db_session)

    assert result["imported"] == 0
    assert result["skipped_existing"] == 2
    assert db_session.query(Lemma).all() == [destination]


def test_duolingo_import_skips_aliases_without_creating_learner_rows(
    db_session,
    monkeypatch,
):
    destination = _seed_people_destination(db_session)
    lexemes = [
        {
            "text": row["arabic"],
            "translations": [row["gloss"]],
            "audioURL": None,
        }
        for row in _alias_entries()
    ]
    monkeypatch.setattr(import_duolingo, "load_lexemes", lambda: lexemes)

    result = import_duolingo.run_import(db_session)

    assert result["imported"] == 0
    assert result["skipped_existing"] == 2
    assert db_session.query(Lemma).all() == [destination]
    assert db_session.query(UserLemmaKnowledge).count() == 0


def test_wiktionary_import_filters_resolved_and_unresolved_exact_aliases(
    db_session,
):
    destination = _seed_people_destination(db_session)
    candidates = [
        {
            **row,
            "root": None,
        }
        for row in _alias_entries()
    ]

    result = import_wiktionary.run_import(
        db_session,
        candidates,
        limit=len(candidates),
    )

    assert result["imported"] == 0
    assert result["skipped_existing"] == 2
    assert db_session.query(Lemma).all() == [destination]


def test_michel_thomas_contextless_resolution_preserves_exact_surface(
    db_session,
):
    destination = _seed_people_destination(db_session)
    lookup = build_lemma_lookup(db_session.query(Lemma).all())

    assert _resolve_contextless_import_word(
        "أُنَاسٌ",
        "أناس",
        lookup,
    ) == (True, destination.lemma_id)
    assert _resolve_contextless_import_word(
        "فَقَدْ",
        "فقد",
        lookup,
    ) == (True, None)
    assert _resolve_contextless_import_word(
        "مَوْمُو",
        "مومو",
        lookup,
    ) == (False, None)


def test_scaffold_import_skips_resolved_and_unresolved_exact_aliases(
    db_session,
):
    destination = _seed_people_destination(db_session)
    rows = [
        (row["arabic"], row["gloss"], row["pos"])
        for row in _alias_entries()
    ]

    result = import_scaffold_words(db_session, rows)

    assert result == {"imported": 0, "resumed": 0, "skipped": 2}
    assert db_session.query(Lemma).all() == [destination]
