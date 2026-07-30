from datetime import datetime, timezone
from unittest.mock import patch

from app.models import Lemma, Sentence, SentenceWord
from scripts.fix_null_lemma_ids import remap_unmapped_sentence_words


def _seed_exact_alias_inventory(db, *, include_destinations: bool):
    gated_at = datetime.now(timezone.utc)
    forget = Lemma(
        lemma_ar="نَسِيَ",
        lemma_ar_bare="نسي",
        gloss_en="to forget",
        pos="verb",
        forms_json={"active_participle": "نَاسٍ"},
        gates_completed_at=gated_at,
    )
    loss = Lemma(
        lemma_ar="فَقْد",
        lemma_ar_bare="فقد",
        gloss_en="loss",
        pos="noun",
        gates_completed_at=gated_at,
    )
    rows = {"forget": forget, "loss": loss}
    if include_destinations:
        rows.update(
            people=Lemma(
                lemma_ar="نَاسٌ",
                lemma_ar_bare="ناس",
                gloss_en="people",
                pos="noun",
                gates_completed_at=gated_at,
            ),
            particle=Lemma(
                lemma_ar="قَدْ",
                lemma_ar_bare="قد",
                gloss_en="already",
                pos="particle",
                gates_completed_at=gated_at,
            ),
        )
    db.add_all(rows.values())
    db.flush()
    return rows


def _seed_active_unmapped_sentence(db):
    sentence = Sentence(
        arabic_text="أُنَاسٌ فَقَدْ.",
        english_translation="People, therefore.",
        source="book",
        is_active=True,
    )
    db.add(sentence)
    db.flush()
    words = [
        SentenceWord(
            sentence_id=sentence.id,
            position=0,
            surface_form="أُنَاسٌ",
            lemma_id=None,
        ),
        SentenceWord(
            sentence_id=sentence.id,
            position=1,
            surface_form="فَقَدْ.",
            lemma_id=None,
        ),
    ]
    db.add_all(words)
    db.flush()
    return words


def test_null_healer_resolves_exact_aliases_from_full_surface(db_session):
    rows = _seed_exact_alias_inventory(
        db_session,
        include_destinations=True,
    )
    words = _seed_active_unmapped_sentence(db_session)

    stats = remap_unmapped_sentence_words(db_session)

    assert [word.lemma_id for word in words] == [
        rows["people"].lemma_id,
        rows["particle"].lemma_id,
    ]
    assert stats == {
        "deleted_non_word": 0,
        "fixed_by_lookup": 2,
        "fixed_by_proper_name": 0,
        "still_unmapped": 0,
        "sentences_touched": 1,
    }


def test_null_healer_keeps_unresolved_aliases_out_of_name_creation(db_session):
    _seed_exact_alias_inventory(
        db_session,
        include_destinations=False,
    )
    words = _seed_active_unmapped_sentence(db_session)
    before_lemmas = db_session.query(Lemma).count()

    with patch(
        "scripts.fix_null_lemma_ids.detect_proper_names",
        return_value={"اناس", "فقد"},
    ), patch(
        "scripts.fix_null_lemma_ids.get_or_create_proper_name_lemma",
        side_effect=AssertionError(
            "unresolved exact alias reached proper-name creation"
        ),
    ):
        stats = remap_unmapped_sentence_words(db_session)

    assert all(word.lemma_id is None for word in words)
    assert db_session.query(Lemma).count() == before_lemmas
    assert stats == {
        "deleted_non_word": 0,
        "fixed_by_lookup": 0,
        "fixed_by_proper_name": 0,
        "still_unmapped": 2,
        "sentences_touched": 1,
    }
