from datetime import datetime, timezone

from app.models import Lemma, QuranicVerse, QuranicVerseWord
from app.services.quran_service import (
    lemmatize_quran_verses,
    select_verse_cards,
)


def _faqad_lemmas(db_session, *, include_qad: bool):
    now = datetime.now(timezone.utc)
    loss = Lemma(
        lemma_ar="فَقْد",
        lemma_ar_bare="فقد",
        gloss_en="loss",
        pos="noun",
        gates_completed_at=now,
    )
    db_session.add(loss)
    qad = None
    if include_qad:
        qad = Lemma(
            lemma_ar="قَدْ",
            lemma_ar_bare="قد",
            gloss_en="already",
            pos="particle",
            gates_completed_at=now,
        )
        db_session.add(qad)
    db_session.flush()
    return qad, loss


def test_quran_lemmatizer_preserves_exact_faqad_identity(
    db_session,
    monkeypatch,
):
    qad, loss = _faqad_lemmas(db_session, include_qad=True)
    verse = QuranicVerse(
        surah=1,
        ayah=1,
        arabic_text="فَقَدْ",
        english_translation="already",
    )
    db_session.add(verse)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.morphology.find_best_db_match",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("exact alias must resolve before morphology")
        ),
    )

    assert lemmatize_quran_verses(db_session, limit=1) == 1

    word = db_session.query(QuranicVerseWord).one()
    assert word.surface_form == "فَقَدْ"
    assert word.lemma_id == qad.lemma_id
    assert word.lemma_id != loss.lemma_id
    assert word.is_function_word is True


def test_quran_card_unresolved_alias_hides_stale_homograph_metadata(
    db_session,
    monkeypatch,
):
    _, loss = _faqad_lemmas(db_session, include_qad=False)
    now = datetime.utcnow()
    verse = QuranicVerse(
        surah=1,
        ayah=1,
        arabic_text="فَقَدْ",
        english_translation="already",
        srs_level=1,
        next_due=now,
        lemmatized_at=now,
    )
    db_session.add(verse)
    db_session.flush()
    db_session.add(QuranicVerseWord(
        verse_id=verse.id,
        position=0,
        surface_form="فَقَدْ",
        lemma_id=loss.lemma_id,
        is_function_word=True,
    ))
    db_session.commit()

    monkeypatch.setattr(
        "app.services.quran_service.lemmatize_quran_verses",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "app.services.quran_service._fill_glosses_llm",
        lambda *_args, **_kwargs: None,
    )

    cards = select_verse_cards(db_session, max_new=0, max_total=1)

    assert len(cards) == 1
    word = cards[0]["words"][0]
    assert word["surface_form"] == "فَقَدْ"
    assert word["lemma_id"] is None
    assert word["lemma_ar"] is None
    assert word["pos"] is None
    assert word["root"] is None
    assert word["root_meaning"] is None
    assert word["is_function_word"] is False
