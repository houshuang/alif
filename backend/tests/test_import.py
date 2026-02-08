import json

from app.models import Root, Lemma, UserLemmaKnowledge
from scripts.import_duolingo import (
    strip_diacritics,
    is_multi_word,
    load_lexemes,
    run_import,
)


def test_strip_diacritics():
    assert strip_diacritics("كَلْب") == "كلب"
    assert strip_diacritics("مُحَمَّد") == "محمد"
    assert strip_diacritics("بَيْت") == "بيت"


def test_is_multi_word():
    assert is_multi_word("هٰذا الْكَلْب") is True
    assert is_multi_word("كَلْب") is False
    assert is_multi_word("لَيْسَ عِنْدِك") is True


def test_load_lexemes():
    lexemes = load_lexemes()
    assert len(lexemes) == 302


def test_run_import(db_session):
    result = run_import(db_session)

    assert result["imported"] > 0
    assert result["skipped_names"] > 0
    assert result["skipped_phrases"] > 0

    # Check some words were imported
    lemma_count = db_session.query(Lemma).count()
    assert lemma_count == result["imported"]

    # All imported words should have knowledge records
    knowledge_count = db_session.query(UserLemmaKnowledge).count()
    assert knowledge_count == result["imported"]

    # All should be in 'learning' state
    learning = (
        db_session.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state == "learning")
        .count()
    )
    assert learning == result["imported"]

    # Check a specific word was imported
    dog = db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "كلب").first()
    assert dog is not None
    assert dog.gloss_en == "dog"
    assert dog.source == "duolingo"

    # Check names were skipped
    mohammad = db_session.query(Lemma).filter(Lemma.lemma_ar == "مُحَمَّد").first()
    assert mohammad is None

    # Check phrases were skipped
    phrase = db_session.query(Lemma).filter(
        Lemma.lemma_ar == "لَيْسَ عِنْدِك"
    ).first()
    assert phrase is None


def test_import_idempotent(db_session):
    result1 = run_import(db_session)
    result2 = run_import(db_session)

    # Second import should add 0 new words
    assert result2["imported"] == 0

    # Total should match first import
    total = db_session.query(Lemma).count()
    assert total == result1["imported"]
