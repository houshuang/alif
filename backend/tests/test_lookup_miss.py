"""Snap-tap reasons (discover /lookup/miss) and their effect on open misses."""
from datetime import datetime, timedelta, timezone

from app.models import Lemma, LookupMiss, ReviewLog, UserLemmaKnowledge, WordAddContext
from app.routers import discover
from app.services.missed_word_recovery import open_misses
from app.services.word_card import gather_inputs


def _word(db, state: str) -> Lemma:
    lemma = Lemma(lemma_ar="عَيْن", lemma_ar_bare="عين", gloss_en="eye", pos="noun", source="test")
    db.add(lemma)
    db.flush()
    db.add(UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state=state))
    db.commit()
    return lemma


def _miss(db, lemma_id: int, causes: list[str], context: str = "طبيبة عيون") -> dict:
    return discover.lookup_miss(
        discover.LookupMissIn(lemma_id=lemma_id, surface="عيون", context=context, snap_id=7, causes=causes),
        db,
    )


def test_reason_on_a_reviewed_word_opens_a_miss_without_review_rows(db_session):
    lemma = _word(db_session, "known")

    out = _miss(db_session, lemma.lemma_id, ["unfamiliar_form"])

    assert out["opens_miss"] is True
    miss = open_misses(db_session, datetime.now(timezone.utc) + timedelta(seconds=1))[lemma.lemma_id]
    assert miss.rating == 1
    assert miss.misses_in_run == 0  # never counts toward the leech run
    assert db_session.query(ReviewLog).count() == 0


def test_changing_and_clearing_reasons_updates_one_row(db_session):
    lemma = _word(db_session, "known")
    _miss(db_session, lemma.lemma_id, ["retrieval_lapse"])
    _miss(db_session, lemma.lemma_id, ["mixed_up", "unfamiliar_form", "bogus"])

    rows = db_session.query(LookupMiss).all()
    assert len(rows) == 1 and rows[0].causes_json == ["mixed_up", "unfamiliar_form"]

    _miss(db_session, lemma.lemma_id, [])
    assert db_session.query(LookupMiss).count() == 0
    assert lemma.lemma_id not in open_misses(db_session, datetime.now(timezone.utc))


def test_reason_on_an_acquiring_word_is_evidence_only(db_session):
    lemma = _word(db_session, "acquiring")

    out = _miss(db_session, lemma.lemma_id, ["retrieval_lapse"])

    assert out["opens_miss"] is False
    assert db_session.query(LookupMiss).one().opens_miss is False
    assert lemma.lemma_id not in open_misses(db_session, datetime.now(timezone.utc) + timedelta(seconds=1))


def test_sentence_successes_after_a_lookup_miss_close_it(db_session):
    lemma = _word(db_session, "known")
    _miss(db_session, lemma.lemma_id, ["retrieval_lapse"])
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    for i in range(2):
        db_session.add(ReviewLog(
            lemma_id=lemma.lemma_id, rating=3, review_mode="reading",
            reviewed_at=(later + timedelta(minutes=i)).replace(tzinfo=None), is_acquisition=False,
        ))
    db_session.commit()

    assert lemma.lemma_id not in open_misses(db_session, later + timedelta(minutes=10))


def test_gloss_here_only_when_it_differs():
    assert discover._gloss_here({"gloss_en": "to see, perceive", "gloss_here": "was born"}) == "was born"
    assert discover._gloss_here({"gloss_en": "eye", "gloss_here": "Eye"}) is None
    assert discover._gloss_phrase({"phrase_ar": "أَبْصَرَ النُّورَ", "phrase_en": "was born"}) == {
        "ar": "أَبْصَرَ النُّورَ", "en": "was born"}
    assert discover._gloss_phrase({"phrase_ar": "x"}) is None


def test_phrase_met_in_reading_is_offered_to_the_word_card(db_session):
    lemma = _word(db_session, "acquiring")
    db_session.add(WordAddContext(
        lemma_id=lemma.lemma_id, source="snap", sentence_ar="أبصر النور",
        phrase_json={"ar": "أَبْصَرَ النُّورَ", "en": "was born"},
    ))
    db_session.commit()

    inputs = gather_inputs(db_session, [lemma.lemma_id], {lemma.lemma_id: [("عين الماء", 3)]})

    assert inputs[0]["book_phrases"] == ["أَبْصَرَ النُّورَ", "عين الماء"]


def test_a_tap_without_snap_or_sentence_still_keeps_one_row(db_session):
    lemma = _word(db_session, "known")
    for causes in (["retrieval_lapse"], ["unfamiliar_form"]):
        discover.lookup_miss(
            discover.LookupMissIn(lemma_id=lemma.lemma_id, surface="عيون", causes=causes), db_session
        )

    assert db_session.query(LookupMiss).count() == 1
