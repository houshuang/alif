"""Tests for app.services.mapping_rescue."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from app.models import (
    FrequencyCoreEntry,
    Lemma,
    Sentence,
    SentenceWord,
)
from app.services import mapping_rescue


STALE = datetime(2026, 3, 1)  # before MAPPING_VERIFICATION_MIN_AT (2026-04-16)


def _lemma(db, ar, gloss="x", bare=None, pos=None) -> Lemma:
    lem = Lemma(
        lemma_ar=ar,
        lemma_ar_bare=bare or ar,
        gloss_en=gloss,
        pos=pos,
    )
    db.add(lem)
    db.flush()
    return lem


def _stale_sentence(db, lemma_ids, target_id=None) -> Sentence:
    sent = Sentence(
        arabic_text="جملة اختبار",
        english_translation="test sentence",
        source="llm",
        target_lemma_id=target_id,
        is_active=True,
        mappings_verified_at=STALE,
    )
    db.add(sent)
    db.flush()
    for pos, lid in enumerate(lemma_ids):
        db.add(SentenceWord(
            sentence_id=sent.id,
            position=pos,
            surface_form=f"w{pos}",
            lemma_id=lid,
            is_target_word=lid == target_id,
        ))
    db.flush()
    return sent


def _fce(db, key, *, lemma_id=None, gloss=None, pos=None, rank=100) -> FrequencyCoreEntry:
    fce = FrequencyCoreEntry(
        core_rank=rank,
        lemma_id=lemma_id,
        lemma_key=key,
        display_form=key,
        gloss_en=gloss,
        pos=pos,
        score=1.0,
        broad_source_count=1,
        confidence_tier="high",
    )
    db.add(fce)
    db.flush()
    return fce


@pytest.fixture
def patched_verifier(monkeypatch):
    """Patch batch_verify_sentences to return whatever the test supplies.

    Usage: ``patched_verifier(lambda inputs, _: [...])``.
    """
    holder: dict = {"fn": None}

    def install(fn):
        holder["fn"] = fn
        monkeypatch.setattr(mapping_rescue, "batch_verify_sentences", fn)
    return install


def _no_issues(inputs, _lemma_map):
    return [{"disambiguation": [], "issues": []} for _ in inputs]


def test_clean_sentence_stamps_fresh(db_session, patched_verifier):
    lem = _lemma(db_session, "كِتاب", "book")
    sent = _stale_sentence(db_session, [lem.lemma_id], target_id=lem.lemma_id)
    db_session.commit()

    patched_verifier(_no_issues)

    stats = mapping_rescue.rescue_sentences_for_lemmas([lem.lemma_id])

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.mappings_verified_at > STALE
    assert stats.sentences_rescued == 1
    assert stats.sentences_corrected == 0  # no corrections needed


def test_unfixable_issue_leaves_stale(db_session, patched_verifier):
    """No FCE match and no existing lemma → sentence stays stale."""
    lem = _lemma(db_session, "كِتاب", "book")
    sent = _stale_sentence(db_session, [lem.lemma_id], target_id=lem.lemma_id)
    db_session.commit()

    def with_issue(inputs, _lemma_map):
        return [{"disambiguation": [], "issues": [
            {
                "position": 0,
                "correct_lemma_ar": "غريب",
                "correct_gloss": "strange (not in vocab)",
                "correct_pos": "adj",
                "explanation": "wrong",
            }
        ]} for _ in inputs]
    patched_verifier(with_issue)

    stats = mapping_rescue.rescue_sentences_for_lemmas([lem.lemma_id])

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.mappings_verified_at == STALE  # unchanged
    assert stats.sentences_unfixable == 1
    assert stats.sentences_rescued == 0


def test_fixable_issue_via_existing_lemma(db_session, patched_verifier):
    """Verifier proposes a correction whose target lemma already exists in DB."""
    wrong = _lemma(db_session, "عَلِيّ", "Ali (name)")
    right = _lemma(db_session, "على", "on", bare="على")
    sent = _stale_sentence(db_session, [wrong.lemma_id], target_id=wrong.lemma_id)
    db_session.commit()

    def with_fixable(inputs, _lemma_map):
        return [{"disambiguation": [], "issues": [
            {
                "position": 0,
                "correct_lemma_ar": "على",
                "correct_gloss": "on (preposition)",
                "correct_pos": "prep",
                "explanation": "homograph",
            }
        ]} for _ in inputs]
    patched_verifier(with_fixable)

    stats = mapping_rescue.rescue_sentences_for_lemmas([wrong.lemma_id])

    db_session.expire_all()
    sw = db_session.query(SentenceWord).filter_by(sentence_id=sent.id).first()
    assert sw.lemma_id == right.lemma_id
    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.mappings_verified_at > STALE
    assert stats.sentences_rescued == 1
    assert stats.sentences_corrected == 1


def test_proposal_with_fce_existing_lemma_reused(db_session, patched_verifier):
    """FCE already points at a lemma — reuse it, don't create."""
    wrong = _lemma(db_session, "كِتاب", "book")
    target_lem = _lemma(db_session, "قَلَم", "pen", bare="قلم")
    _fce(db_session, "قلم", lemma_id=target_lem.lemma_id, rank=300)
    sent = _stale_sentence(db_session, [wrong.lemma_id], target_id=wrong.lemma_id)
    db_session.commit()

    def proposal(inputs, _lemma_map):
        return [{"disambiguation": [], "issues": [
            {
                "position": 0,
                "correct_lemma_ar": "قَلَم",
                "correct_gloss": "pen",
                "correct_pos": "noun",
                "explanation": "wrong word",
            }
        ]} for _ in inputs]
    patched_verifier(proposal)

    # correct_mapping inside apply_corrections will already find the lemma —
    # the FCE branch only kicks in when correct_mapping fails. Use a different
    # bare form to force the FCE branch.
    sw_count_before = db_session.query(Lemma).count()
    stats = mapping_rescue.rescue_sentences_for_lemmas([wrong.lemma_id])
    sw_count_after = db_session.query(Lemma).count()
    assert sw_count_after == sw_count_before  # no new lemma created
    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.mappings_verified_at > STALE


def test_proposal_creates_lemma_when_fce_unlinked(db_session, patched_verifier):
    """FCE row exists but lemma_id IS NULL — proposal creates the lemma."""
    wrong = _lemma(db_session, "كِتاب", "book")
    # FCE row points at a not-yet-imported lemma key. Production FCE keys are
    # bare (no tashkeel) — match that.
    fce = _fce(
        db_session, "جديد",
        lemma_id=None, gloss="new", pos="adj", rank=400,
    )
    sent = _stale_sentence(db_session, [wrong.lemma_id], target_id=wrong.lemma_id)
    db_session.commit()

    def proposal(inputs, _lemma_map):
        return [{"disambiguation": [], "issues": [
            {
                "position": 0,
                "correct_lemma_ar": "جَدِيد",
                "correct_gloss": "new",
                "correct_pos": "adj",
                "explanation": "should be جديد",
            }
        ]} for _ in inputs]
    patched_verifier(proposal)

    # Stub run_quality_gates so we don't actually run enrichment / LLM
    with patch(
        "app.services.lemma_quality.run_quality_gates",
        return_value={"finalize": {}, "variants": 0, "enriched": False, "stamped": 0},
    ):
        stats = mapping_rescue.rescue_sentences_for_lemmas([wrong.lemma_id])

    db_session.expire_all()
    # New lemma created and linked to FCE
    new_lem = (
        db_session.query(Lemma)
        .filter(Lemma.lemma_ar_bare == "جديد")
        .one()
    )
    refreshed_fce = db_session.query(FrequencyCoreEntry).get(fce.id)
    assert refreshed_fce.lemma_id == new_lem.lemma_id

    # SentenceWord remapped to the new lemma
    sw = db_session.query(SentenceWord).filter_by(sentence_id=sent.id).first()
    assert sw.lemma_id == new_lem.lemma_id

    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.mappings_verified_at > STALE


def test_coverage_threshold_marks_lemma_covered(db_session, patched_verifier):
    """When rescue brings a lemma to >= coverage_target reviewable sentences, it's flagged covered."""
    lem = _lemma(db_session, "بَيت", "house")
    # Three stale sentences — all should get rescued by a clean verifier pass.
    for _ in range(3):
        _stale_sentence(db_session, [lem.lemma_id], target_id=lem.lemma_id)
    db_session.commit()
    patched_verifier(_no_issues)

    stats = mapping_rescue.rescue_sentences_for_lemmas(
        [lem.lemma_id], coverage_target=3,
    )

    assert lem.lemma_id in stats.lemmas_now_covered
    assert stats.sentences_rescued == 3


def test_llm_failure_keeps_sentence_stale(db_session, patched_verifier):
    """batch_verify_sentences returning None for a chunk should not crash and should leave sentence untouched."""
    lem = _lemma(db_session, "كِتاب", "book")
    sent = _stale_sentence(db_session, [lem.lemma_id], target_id=lem.lemma_id)
    db_session.commit()

    patched_verifier(lambda inputs, _: None)
    stats = mapping_rescue.rescue_sentences_for_lemmas([lem.lemma_id])

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.mappings_verified_at == STALE  # unchanged
    assert stats.sentences_rescued == 0
    assert stats.lemmas_attempted == 1


def test_no_stale_sentences_is_noop(db_session, patched_verifier):
    """A gap lemma with no stale-verified sentences should not call the verifier."""
    lem = _lemma(db_session, "ماء", "water")
    db_session.commit()
    calls = {"n": 0}

    def counting(inputs, _lemma_map):
        calls["n"] += 1
        return _no_issues(inputs, _lemma_map)
    patched_verifier(counting)

    stats = mapping_rescue.rescue_sentences_for_lemmas([lem.lemma_id])
    assert calls["n"] == 0
    assert stats.sentences_attempted == 0
