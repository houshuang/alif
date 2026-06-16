"""Deficit-refill (R4) — the classify-then-act logic that decides which FSRS-due,
zero-reviewable-sentence words to regenerate. LLM generation itself is not
exercised here; this guards the selection and classification."""
from datetime import datetime, timedelta, timezone

from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from scripts.refill_due_deficit import (
    classify,
    compute_due_deficit,
    _is_inert,
    _looks_like_artifact,
)

NOW = datetime.now(timezone.utc)
PAST = (NOW - timedelta(days=1)).isoformat()
FUTURE = (NOW + timedelta(days=5)).isoformat()


def _lemma(db, lemma_id, ar, gloss="g", bare=None, category=None):
    db.add(Lemma(
        lemma_id=lemma_id, lemma_ar=ar, lemma_ar_bare=bare or ar, gloss_en=gloss,
        word_category=category, gates_completed_at=NOW,
    ))
    db.flush()


def _ulk(db, lemma_id, state="known", due=PAST, backoff_until=None):
    db.add(UserLemmaKnowledge(
        lemma_id=lemma_id, knowledge_state=state, source="study",
        fsrs_card_json={"due": due}, generation_backoff_until=backoff_until,
    ))
    db.flush()


def _sentence(db, sid, lemma_ids, target_id):
    db.add(Sentence(
        id=sid, arabic_text=f"s{sid}", english_translation="t", is_active=True,
        mappings_verified_at=NOW, target_lemma_id=target_id, times_shown=1,
    ))
    db.flush()
    for i, lid in enumerate(lemma_ids):
        db.add(SentenceWord(sentence_id=sid, position=i, surface_form=f"w{lid}",
                            lemma_id=lid, is_target_word=(lid == target_id)))
    db.flush()


def test_compute_due_deficit_finds_due_uncovered_only(db_session):
    # 1: due, no sentence       -> deficit
    # 2: due, has sentence      -> covered (not deficit)
    # 3: not due, no sentence   -> not deficit (not due)
    # 4: due but acquiring      -> not in DUE_STATES, ignored
    for lid in (1, 2, 3):
        _lemma(db_session, lid, f"w{lid}")
    _lemma(db_session, 4, "w4")
    _ulk(db_session, 1, due=PAST)
    _ulk(db_session, 2, due=PAST)
    _ulk(db_session, 3, due=FUTURE)
    _ulk(db_session, 4, state="acquiring", due=PAST)
    _sentence(db_session, 100, [2], 2)
    db_session.commit()

    deficit = compute_due_deficit(db_session)
    assert deficit == [1]


def test_collateral_coverage_counts_as_covered(db_session):
    # Word 2 is only collateral scaffold in a sentence targeting word 1 — still
    # covered (the review engine credits collateral), so not in deficit.
    _lemma(db_session, 1, "w1")
    _lemma(db_session, 2, "w2")
    _ulk(db_session, 1, due=FUTURE)   # target not due
    _ulk(db_session, 2, due=PAST)     # collateral, due
    _sentence(db_session, 100, [1, 2], 1)
    db_session.commit()

    assert compute_due_deficit(db_session) == []


def test_classify_splits_inert_backoff_artifact_generatable(db_session):
    _lemma(db_session, 1, "مِقَصّ", gloss="scissors")                       # generatable
    _lemma(db_session, 2, "أُوسْلو", gloss="Oslo", category="proper_name")   # inert
    _lemma(db_session, 3, "نَدْرُسُ", gloss="we study")                      # artifact (still generatable)
    _lemma(db_session, 4, "صِرْب", gloss="Serbs")                            # backed off
    for lid in (1, 2, 3, 4):
        _ulk(db_session, lid, due=PAST,
             backoff_until=(NOW + timedelta(days=3)) if lid == 4 else None)
    db_session.commit()

    buckets = classify(db_session, [1, 2, 3, 4])
    assert buckets["inert"] == [2]
    assert buckets["backed_off"] == [4]
    assert buckets["artifacts"] == [3]
    # artifact is still attempted; inert + backed-off are not
    assert set(buckets["generatable"]) == {1, 3}


def test_is_inert_and_artifact_heuristics(db_session):
    pn = Lemma(lemma_id=10, lemma_ar="روما", lemma_ar_bare="روما", word_category="proper_name")
    real = Lemma(lemma_id=11, lemma_ar="مِقَصّ", lemma_ar_bare="مقص", gloss_en="scissors")
    conj = Lemma(lemma_id=12, lemma_ar="يَكْتُبُونَ", lemma_ar_bare="يكتبون", gloss_en="they write")
    shadda = Lemma(lemma_id=13, lemma_ar="ثَّامِن", lemma_ar_bare="ثامن", gloss_en="eighth")
    assert _is_inert(pn) is True
    assert _is_inert(real) is False
    assert _looks_like_artifact(conj) is True
    assert _looks_like_artifact(shadda) is True
    assert _looks_like_artifact(real) is False
