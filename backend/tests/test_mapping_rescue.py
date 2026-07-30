"""Tests for app.services.mapping_rescue."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import (
    FrequencyCoreEntry,
    Lemma,
    Sentence,
    SentenceWord,
)
from app.services import mapping_rescue
from app.services.sentence_eligibility import (
    CORPUS_BLOCKED_SENTINEL,
    CORPUS_CLAIM_SENTINEL,
    CORPUS_QUALITY_REJECTED_SENTINEL,
    MAPPING_VERIFICATION_HARDENED_AT,
)


STALE = datetime(2026, 3, 1)  # before the active mapping verification cutoff


def _lemma(db, ar, gloss="x", bare=None, pos=None) -> Lemma:
    lem = Lemma(
        lemma_ar=ar,
        lemma_ar_bare=bare or ar,
        gloss_en=gloss,
        pos=pos,
        gates_completed_at=datetime(2026, 2, 1),
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

        def compatible(inputs, lemma_map, **_kwargs):
            return fn(inputs, lemma_map)

        monkeypatch.setattr(
            mapping_rescue,
            "batch_verify_sentences",
            compatible,
        )
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


def test_same_bare_wrong_sense_stays_unfixable(db_session, patched_verifier):
    """Reverify must not stamp a wrong same-bare homograph as an overcall."""
    wrong = _lemma(
        db_session,
        "شَالَ",
        "to rise, to become elevated",
        bare="شال",
        pos="verb",
    )
    sent = _stale_sentence(db_session, [wrong.lemma_id], target_id=wrong.lemma_id)
    db_session.commit()

    def same_bare_wrong_sense(inputs, _lemma_map):
        return [{"disambiguation": [], "issues": [
            {
                "position": 0,
                "correct_lemma_ar": "شَال",
                "correct_gloss": "shawl, scarf",
                "correct_pos": "noun",
                "explanation": "same bare, different sense",
            }
        ]} for _ in inputs]
    patched_verifier(same_bare_wrong_sense)

    stats = mapping_rescue.rescue_sentences_for_lemmas([wrong.lemma_id])

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.mappings_verified_at == STALE
    assert stats.sentences_unfixable == 1
    assert stats.sentences_rescued == 0


def test_compatible_same_lemma_overcall_still_stamps(db_session, patched_verifier):
    """A verifier restating the current compatible lemma remains a harmless overcall."""
    current = _lemma(db_session, "جَلَبَ", "to bring", bare="جلب", pos="verb")
    sent = _stale_sentence(db_session, [current.lemma_id], target_id=current.lemma_id)
    db_session.commit()

    def compatible_same_lemma(inputs, _lemma_map):
        return [{"disambiguation": [], "issues": [
            {
                "position": 0,
                "correct_lemma_ar": "جَلَبَ",
                "correct_gloss": "to bring",
                "correct_pos": "verb",
                "explanation": "same lemma",
            }
        ]} for _ in inputs]
    patched_verifier(compatible_same_lemma)

    stats = mapping_rescue.rescue_sentences_for_lemmas([current.lemma_id])

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.mappings_verified_at > STALE
    assert stats.sentences_rescued == 1


def test_exact_alias_conflict_is_not_calibrated_as_overcall(
    db_session,
    patched_verifier,
):
    """A contradictory verifier proposal cannot reopen exact identity."""
    people = _lemma(
        db_session,
        "نَاسٌ",
        "people",
        bare="ناس",
        pos="noun",
    )
    forget = _lemma(
        db_session,
        "نَسِيَ",
        "to forget",
        bare="نسي",
        pos="verb",
    )
    sent = _stale_sentence(
        db_session,
        [people.lemma_id],
        target_id=people.lemma_id,
    )
    word = (
        db_session.query(SentenceWord)
        .filter_by(sentence_id=sent.id)
        .one()
    )
    word.surface_form = "أُنَاسٌ"
    db_session.commit()

    def contradictory_alias(inputs, _lemma_map):
        return [
            {
                "disambiguation": [],
                "issues": [
                    {
                        "position": 0,
                        "correct_lemma_ar": "نَسِيَ",
                        "correct_gloss": "to forget",
                        "correct_pos": "verb",
                        "explanation": "contradicts exact surface",
                    }
                ],
            }
            for _ in inputs
        ]

    patched_verifier(contradictory_alias)

    stats = mapping_rescue.rescue_sentences_for_lemmas([people.lemma_id])

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    refreshed_word = (
        db_session.query(SentenceWord)
        .filter_by(sentence_id=sent.id)
        .one()
    )
    assert refreshed.mappings_verified_at == STALE
    assert refreshed_word.lemma_id == people.lemma_id
    assert refreshed_word.lemma_id != forget.lemma_id
    assert stats.sentences_unfixable == 1
    assert stats.sentences_rescued == 0


def test_exact_alias_never_creates_frequency_proposal(
    db_session,
    patched_verifier,
):
    """A governed surface cannot enter mapping-rescue lemma creation."""
    people = _lemma(
        db_session,
        "نَاسٌ",
        "people",
        bare="ناس",
        pos="noun",
    )
    fce = _fce(
        db_session,
        "جديد",
        lemma_id=None,
        gloss="new",
        pos="adj",
        rank=401,
    )
    sent = _stale_sentence(
        db_session,
        [people.lemma_id],
        target_id=people.lemma_id,
    )
    word = (
        db_session.query(SentenceWord)
        .filter_by(sentence_id=sent.id)
        .one()
    )
    word.surface_form = "أُنَاسٌ"
    db_session.commit()

    def missing_alias_proposal(inputs, _lemma_map):
        return [
            {
                "disambiguation": [],
                "issues": [
                    {
                        "position": 0,
                        "correct_lemma_ar": "جَدِيد",
                        "correct_gloss": "new",
                        "correct_pos": "adj",
                        "explanation": "contradicts exact surface",
                    }
                ],
            }
            for _ in inputs
        ]

    patched_verifier(missing_alias_proposal)
    count_before = db_session.query(Lemma).count()
    with patch("app.services.lemma_quality.run_quality_gates") as gates:
        stats = mapping_rescue.rescue_sentences_for_lemmas(
            [people.lemma_id]
        )

    gates.assert_not_called()
    db_session.expire_all()
    assert db_session.query(Lemma).count() == count_before
    assert db_session.query(FrequencyCoreEntry).get(fce.id).lemma_id is None
    assert db_session.query(Sentence).get(sent.id).mappings_verified_at == STALE
    assert (
        db_session.query(SentenceWord)
        .filter_by(sentence_id=sent.id)
        .one()
        .lemma_id
        == people.lemma_id
    )
    assert stats.proposals_created_lemma == 0
    assert stats.sentences_unfixable == 1


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
    assert refreshed.target_lemma_id == right.lemma_id
    assert refreshed.mappings_verified_at > STALE
    assert stats.sentences_rescued == 1
    assert stats.sentences_corrected == 1
    assert stats.targets_repaired == 1


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

    # Stub run_quality_gates so we don't actually run enrichment / LLM, while
    # preserving its load-bearing persisted gate stamp.
    def stamp_quality_gates(db, lemma_ids, **_kwargs):
        db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).update(
            {Lemma.gates_completed_at: datetime(2026, 2, 2)},
            synchronize_session=False,
        )
        db.commit()
        return {
            "finalize": {},
            "variants": 0,
            "enriched": False,
            "stamped": len(lemma_ids),
        }

    with patch(
        "app.services.lemma_quality.run_quality_gates",
        side_effect=stamp_quality_gates,
    ):
        stats = mapping_rescue.rescue_sentences_for_lemmas([wrong.lemma_id])

    db_session.expire_all()
    # New lemma created and linked to FCE
    new_lem = (
        db_session.query(Lemma)
        .filter(Lemma.lemma_ar_bare == "جديد")
        .one()
    )
    assert new_lem.gates_completed_at is not None
    refreshed_fce = db_session.query(FrequencyCoreEntry).get(fce.id)
    assert refreshed_fce.lemma_id == new_lem.lemma_id

    # SentenceWord remapped to the new lemma
    sw = db_session.query(SentenceWord).filter_by(sentence_id=sent.id).first()
    assert sw.lemma_id == new_lem.lemma_id

    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.mappings_verified_at > STALE
    assert stats.proposals_created_lemma == 1


def test_failed_quality_gate_never_exposes_or_duplicates_proposal(
    db_session,
    patched_verifier,
):
    """A committed FCE claim remains ungated and unusable until Step G2 heals it."""
    wrong = _lemma(db_session, "كِتاب", "book")
    fce = _fce(
        db_session,
        "جديد",
        lemma_id=None,
        gloss="new",
        pos="adj",
        rank=400,
    )
    sent = _stale_sentence(
        db_session, [wrong.lemma_id], target_id=wrong.lemma_id
    )
    db_session.commit()

    def proposal(inputs, _lemma_map):
        return [{
            "disambiguation": [],
            "issues": [{
                "position": 0,
                "correct_lemma_ar": "جَدِيد",
                "correct_gloss": "new",
                "correct_pos": "adj",
                "explanation": "should be جديد",
            }],
        } for _ in inputs]

    def fail_after_claim_commit(db, _lemma_ids, **_kwargs):
        db.commit()
        raise RuntimeError("quality pipeline interrupted")

    patched_verifier(proposal)
    with patch(
        "app.services.lemma_quality.run_quality_gates",
        side_effect=fail_after_claim_commit,
    ):
        first = mapping_rescue.rescue_sentences_for_lemmas([wrong.lemma_id])

    db_session.expire_all()
    claimed_fce = db_session.query(FrequencyCoreEntry).get(fce.id)
    assert claimed_fce.lemma_id is not None
    claimed_lemma = db_session.query(Lemma).get(claimed_fce.lemma_id)
    assert claimed_lemma.gates_completed_at is None
    word = (
        db_session.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sent.id)
        .one()
    )
    assert word.lemma_id == wrong.lemma_id
    assert db_session.query(Sentence).get(sent.id).mappings_verified_at == STALE
    assert first.proposals_created_lemma == 0
    assert first.sentences_unfixable == 1
    lemma_count = db_session.query(Lemma).count()

    # A later rescue sees the ungated link as an in-progress/stranded claim:
    # it neither reuses it nor creates a duplicate.
    with patch(
        "app.services.lemma_quality.run_quality_gates"
    ) as quality_mock:
        second = mapping_rescue.rescue_sentences_for_lemmas([wrong.lemma_id])

    db_session.expire_all()
    assert db_session.query(Lemma).count() == lemma_count
    assert db_session.query(FrequencyCoreEntry).get(
        fce.id
    ).lemma_id == claimed_lemma.lemma_id
    assert second.sentences_rescued == 0
    quality_mock.assert_not_called()


def test_frequency_core_claim_cas_rejects_stale_second_writer(db_session):
    """Two SQLite readers cannot both convert the same NULL FCE link."""
    fce = _fce(db_session, "مقترح", lemma_id=None, rank=401)
    fce_id = fce.id
    db_session.commit()

    Session = sessionmaker(bind=db_session.bind)
    first = Session()
    second = Session()
    try:
        stale_first = first.get(FrequencyCoreEntry, fce_id)
        stale_second = second.get(FrequencyCoreEntry, fce_id)
        assert stale_first.lemma_id is None
        assert stale_second.lemma_id is None

        winner = _lemma(first, "مُقْتَرَحٌ أَوَّل", "first proposal")
        assert mapping_rescue._claim_frequency_core_entry(
            first, stale_first.id, winner.lemma_id
        )
        winner_id = winner.lemma_id
        first.commit()

        loser = _lemma(second, "مُقْتَرَحٌ ثَانٍ", "second proposal")
        loser_id = loser.lemma_id
        assert not mapping_rescue._claim_frequency_core_entry(
            second, stale_second.id, loser_id
        )
        second.rollback()

        db_session.expire_all()
        assert db_session.get(FrequencyCoreEntry, fce_id).lemma_id == winner_id
        assert db_session.get(Lemma, winner_id) is not None
        assert db_session.get(Lemma, loser_id) is None
    finally:
        first.close()
        second.close()


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


def test_lazy_rescue_skips_only_invalid_verifier_row(
    db_session,
    patched_verifier,
):
    lem = _lemma(db_session, "كِتاب", "book")
    first = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    second = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    db_session.commit()

    def mixed_rows(inputs, _lemma_map):
        assert len(inputs) == 2
        return [
            {
                "disambiguation": [],
                "issues": [],
                "invalid_reason": "undeclared_disambiguation",
                "invalid_positions": [0],
            },
            {"disambiguation": [], "issues": []},
        ]

    patched_verifier(mixed_rows)
    stats = mapping_rescue.rescue_sentences_for_lemmas([lem.lemma_id])

    db_session.expire_all()
    assert db_session.get(Sentence, first.id).mappings_verified_at == STALE
    assert db_session.get(Sentence, second.id).mappings_verified_at > STALE
    assert stats.sentences_attempted == 2
    assert stats.sentences_rescued == 1


def test_reverify_skips_only_invalid_verifier_row(
    db_session,
    patched_verifier,
):
    lem = _lemma(db_session, "كِتاب", "book")
    first = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    second = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    db_session.commit()

    def mixed_rows(inputs, _lemma_map):
        assert len(inputs) == 2
        return [
            {
                "disambiguation": [],
                "issues": [],
                "invalid_reason": "undeclared_disambiguation",
                "invalid_positions": [0],
            },
            {"disambiguation": [], "issues": []},
        ]

    patched_verifier(mixed_rows)
    stats = mapping_rescue.reverify_all_active_sentences(
        sentence_ids=[first.id, second.id],
        batch_size=2,
    )

    db_session.expire_all()
    assert db_session.get(Sentence, first.id).mappings_verified_at == STALE
    assert db_session.get(Sentence, second.id).mappings_verified_at > STALE
    assert stats.sentences_attempted == 2
    assert stats.sentences_passed == 1
    assert stats.llm_failures == 1


def test_reverify_triage_mkdir_failure_is_best_effort(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.config.settings.log_dir",
        tmp_path / "uncreatable",
    )

    def fail_mkdir(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("pathlib.Path.mkdir", fail_mkdir)

    # Logging is diagnostic only and must never abort a completed sweep row.
    mapping_rescue._log_reverify_triage(
        sentence_id=1,
        arabic_text="جُمْلَةٌ",
        english_text="a sentence",
        failed_positions=[0],
        word_rows=[
            mapping_rescue.TokenMapping(
                position=0,
                surface_form="جُمْلَةٌ",
                lemma_id=1,
                is_target=False,
                is_function_word=False,
            )
        ],
        issues=[],
    )


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


def test_clean_lazy_rescue_cannot_stamp_absent_primary_target(
    db_session,
    patched_verifier,
):
    """A clean mapping verdict is insufficient when the stored target is absent."""
    mapped = _lemma(db_session, "قَدَم", "foot")
    absent = _lemma(db_session, "قَدِمَ", "to arrive")
    sent = _stale_sentence(
        db_session, [mapped.lemma_id], target_id=absent.lemma_id
    )
    db_session.commit()
    patched_verifier(_no_issues)

    stats = mapping_rescue.rescue_sentences_for_lemmas([mapped.lemma_id])

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.mappings_verified_at == STALE
    assert refreshed.target_lemma_id == absent.lemma_id
    assert stats.sentences_rescued == 0
    assert stats.sentences_unfixable == 1
    assert stats.sentences_target_invalid == 1


def test_clean_reverify_hides_absent_primary_target(
    db_session,
    patched_verifier,
):
    """The active sweep clears trust instead of blessing stale target drift."""
    mapped = _lemma(db_session, "قَدَم", "foot")
    absent = _lemma(db_session, "قَدِمَ", "to arrive")
    sent = _stale_sentence(
        db_session, [mapped.lemma_id], target_id=absent.lemma_id
    )
    db_session.commit()
    patched_verifier(_no_issues)

    stats = mapping_rescue.reverify_all_active_sentences(
        sentence_ids=[sent.id],
    )

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.mappings_verified_at is None
    assert refreshed.target_lemma_id == absent.lemma_id
    assert stats.sentences_passed == 0
    assert stats.sentences_unfixable == 1
    assert stats.sentences_target_invalid == 1


def test_lazy_rescue_never_reopens_durable_corpus_dispositions(
    db_session,
    patched_verifier,
):
    """Old sentinels are not all equivalent: only the transient claim retries."""
    lem = _lemma(db_session, "كِتاب", "book")
    blocked = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    blocked.mappings_verified_at = CORPUS_BLOCKED_SENTINEL
    rejected = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    rejected.mappings_verified_at = CORPUS_QUALITY_REJECTED_SENTINEL
    claim = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    claim.mappings_verified_at = CORPUS_CLAIM_SENTINEL
    db_session.commit()
    patched_verifier(_no_issues)

    stats = mapping_rescue.rescue_sentences_for_lemmas([lem.lemma_id])

    db_session.expire_all()
    assert stats.sentences_attempted == 1
    assert (
        db_session.query(Sentence).get(blocked.id).mappings_verified_at
        == CORPUS_BLOCKED_SENTINEL
    )
    assert (
        db_session.query(Sentence).get(rejected.id).mappings_verified_at
        == CORPUS_QUALITY_REJECTED_SENTINEL
    )
    assert (
        db_session.query(Sentence).get(claim.id).mappings_verified_at
        > MAPPING_VERIFICATION_HARDENED_AT
    )


def test_reverify_sentences_before_only_checks_explicit_pre_cutoff_stamps(
    db_session,
    patched_verifier,
):
    """Explicit reverify targets selected rows older than the hardening cutoff."""
    old_lem = _lemma(db_session, "قَدِيم", "old")
    fresh_lem = _lemma(db_session, "جَدِيد", "new")
    legacy = _stale_sentence(db_session, [old_lem.lemma_id], target_id=old_lem.lemma_id)
    legacy.mappings_verified_at = datetime(2026, 5, 13, 21, 30)
    fresh = _stale_sentence(db_session, [fresh_lem.lemma_id], target_id=fresh_lem.lemma_id)
    fresh.mappings_verified_at = datetime(2026, 5, 18, 8, 0)
    db_session.commit()

    seen_batches: list[list[str]] = []

    def verifier(inputs, _lemma_map):
        seen_batches.append([item["arabic"] for item in inputs])
        return _no_issues(inputs, _lemma_map)

    patched_verifier(verifier)

    stats = mapping_rescue.reverify_sentences_before(
        [legacy.id, fresh.id],
        cutoff=MAPPING_VERIFICATION_HARDENED_AT,
        batch_size=5,
    )

    db_session.expire_all()
    refreshed_legacy = db_session.query(Sentence).get(legacy.id)
    refreshed_fresh = db_session.query(Sentence).get(fresh.id)
    assert stats.sentences_attempted == 1
    assert seen_batches == [["جملة اختبار"]]
    assert refreshed_legacy.mappings_verified_at >= MAPPING_VERIFICATION_HARDENED_AT
    assert refreshed_fresh.mappings_verified_at == datetime(2026, 5, 18, 8, 0)


def test_reverify_overcall_counts_flagged_pass_not_correction(
    db_session,
    patched_verifier,
):
    """A compatible same-lemma verifier overcall is not a database correction."""
    current = _lemma(
        db_session, "جَلَبَ", "to bring", bare="جلب", pos="verb"
    )
    sent = _stale_sentence(
        db_session, [current.lemma_id], target_id=current.lemma_id
    )
    db_session.commit()

    def compatible_overcall(inputs, _lemma_map):
        return [{
            "disambiguation": [],
            "issues": [{
                "position": 0,
                "correct_lemma_ar": "جَلَبَ",
                "correct_gloss": "to bring",
                "correct_pos": "verb",
                "explanation": "same lemma",
            }],
        } for _ in inputs]

    patched_verifier(compatible_overcall)
    stats = mapping_rescue.reverify_all_active_sentences(
        sentence_ids=[sent.id],
    )

    assert stats.sentences_flagged == 1
    assert stats.sentences_passed == 1
    assert stats.sentences_corrected == 0


def test_explicit_reverify_excludes_durable_corpus_dispositions(
    db_session,
    patched_verifier,
):
    """A broad caller-provided ID list cannot bypass corpus retry curation."""
    lem = _lemma(db_session, "كِتاب", "book")
    blocked = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    blocked.mappings_verified_at = CORPUS_BLOCKED_SENTINEL
    rejected = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    rejected.mappings_verified_at = CORPUS_QUALITY_REJECTED_SENTINEL
    claim = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    claim.mappings_verified_at = CORPUS_CLAIM_SENTINEL
    db_session.commit()
    patched_verifier(_no_issues)

    stats = mapping_rescue.reverify_sentences_before(
        [blocked.id, rejected.id, claim.id],
        cutoff=MAPPING_VERIFICATION_HARDENED_AT,
        batch_size=5,
    )

    db_session.expire_all()
    assert stats.sentences_attempted == 1
    assert (
        db_session.query(Sentence).get(blocked.id).mappings_verified_at
        == CORPUS_BLOCKED_SENTINEL
    )
    assert (
        db_session.query(Sentence).get(rejected.id).mappings_verified_at
        == CORPUS_QUALITY_REJECTED_SENTINEL
    )
    assert (
        db_session.query(Sentence).get(claim.id).mappings_verified_at
        >= MAPPING_VERIFICATION_HARDENED_AT
    )


def test_reverify_explicit_ids_excludes_inactive_durable_rows(
    db_session,
    patched_verifier,
):
    """An exact caller list cannot revive inactive Jan-2/Jan-3 corpus rows."""
    lem = _lemma(db_session, "كِتاب", "book")
    blocked = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    blocked.is_active = False
    blocked.mappings_verified_at = CORPUS_BLOCKED_SENTINEL
    rejected = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    rejected.is_active = False
    rejected.mappings_verified_at = CORPUS_QUALITY_REJECTED_SENTINEL
    db_session.commit()
    calls = {"count": 0}

    def verifier(inputs, lemma_map):
        calls["count"] += 1
        return _no_issues(inputs, lemma_map)

    patched_verifier(verifier)
    stats = mapping_rescue.reverify_all_active_sentences(
        sentence_ids=[blocked.id, rejected.id],
    )

    db_session.expire_all()
    assert calls["count"] == 0
    assert stats.sentences_attempted == 0
    assert db_session.query(Sentence).get(
        blocked.id
    ).mappings_verified_at == CORPUS_BLOCKED_SENTINEL
    assert db_session.query(Sentence).get(
        rejected.id
    ).mappings_verified_at == CORPUS_QUALITY_REJECTED_SENTINEL


def test_reverify_cas_preserves_concurrent_durable_disposition(
    db_session,
    patched_verifier,
):
    """A lifecycle change during the LLM call wins over a stale clean result."""
    lem = _lemma(db_session, "كِتاب", "book")
    sent = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    db_session.commit()

    def concurrent_block(inputs, lemma_map):
        other = mapping_rescue.SessionLocal()
        try:
            row = other.query(Sentence).get(sent.id)
            row.is_active = False
            row.mappings_verified_at = CORPUS_BLOCKED_SENTINEL
            other.commit()
        finally:
            other.close()
        return _no_issues(inputs, lemma_map)

    patched_verifier(concurrent_block)
    stats = mapping_rescue.reverify_all_active_sentences(
        sentence_ids=[sent.id],
    )

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.is_active is False
    assert refreshed.mappings_verified_at == CORPUS_BLOCKED_SENTINEL
    assert stats.sentences_passed == 0
    assert stats.sentences_skipped_changed == 1


def test_concurrent_disposition_also_blocks_proposal_prepass(
    db_session,
    patched_verifier,
):
    """A lost snapshot cannot create an otherwise FCE-approved lemma."""
    wrong = _lemma(db_session, "كِتاب", "book")
    fce = _fce(
        db_session,
        "جديد",
        lemma_id=None,
        gloss="new",
        pos="adj",
        rank=400,
    )
    sent = _stale_sentence(
        db_session, [wrong.lemma_id], target_id=wrong.lemma_id
    )
    db_session.commit()
    lemma_count_before = db_session.query(Lemma).count()

    def concurrent_block_with_proposal(inputs, _lemma_map):
        other = mapping_rescue.SessionLocal()
        try:
            row = other.query(Sentence).get(sent.id)
            row.is_active = False
            row.mappings_verified_at = CORPUS_BLOCKED_SENTINEL
            other.commit()
        finally:
            other.close()
        return [{
            "disambiguation": [],
            "issues": [{
                "position": 0,
                "correct_lemma_ar": "جَدِيد",
                "correct_gloss": "new",
                "correct_pos": "adj",
                "explanation": "should be جديد",
            }],
        } for _ in inputs]

    patched_verifier(concurrent_block_with_proposal)
    with patch(
        "app.services.lemma_quality.run_quality_gates"
    ) as quality_mock:
        stats = mapping_rescue.reverify_all_active_sentences(
            sentence_ids=[sent.id],
        )

    db_session.expire_all()
    assert stats.sentences_skipped_changed == 1
    assert db_session.query(Lemma).count() == lemma_count_before
    assert db_session.query(FrequencyCoreEntry).get(fce.id).lemma_id is None
    quality_mock.assert_not_called()


def test_lazy_rescue_cas_preserves_concurrent_durable_disposition(
    db_session,
    patched_verifier,
):
    """Lazy rescue also refuses to write through a changed corpus claim."""
    lem = _lemma(db_session, "كِتاب", "book")
    sent = _stale_sentence(
        db_session, [lem.lemma_id], target_id=lem.lemma_id
    )
    db_session.commit()

    def concurrent_reject(inputs, lemma_map):
        other = mapping_rescue.SessionLocal()
        try:
            row = other.query(Sentence).get(sent.id)
            row.is_active = False
            row.mappings_verified_at = CORPUS_QUALITY_REJECTED_SENTINEL
            other.commit()
        finally:
            other.close()
        return _no_issues(inputs, lemma_map)

    patched_verifier(concurrent_reject)
    stats = mapping_rescue.rescue_sentences_for_lemmas([lem.lemma_id])

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    assert refreshed.is_active is False
    assert refreshed.mappings_verified_at == CORPUS_QUALITY_REJECTED_SENTINEL
    assert stats.sentences_rescued == 0
    assert stats.sentences_skipped_changed == 1


def test_reverify_dry_run_has_no_proposal_or_logging_side_effects(
    db_session,
    patched_verifier,
):
    """Dry-run may call the verifier but cannot write through proposal helpers."""
    wrong = _lemma(db_session, "كِتاب", "book")
    fce = _fce(
        db_session,
        "جديد",
        lemma_id=None,
        gloss="new",
        pos="adj",
        rank=400,
    )
    sent = _stale_sentence(
        db_session, [wrong.lemma_id], target_id=wrong.lemma_id
    )
    db_session.commit()

    def proposal(inputs, _lemma_map):
        return [{
            "disambiguation": [],
            "issues": [{
                "position": 0,
                "correct_lemma_ar": "جَدِيد",
                "correct_gloss": "new",
                "correct_pos": "adj",
                "explanation": "should be جديد",
            }],
        } for _ in inputs]

    patched_verifier(proposal)
    lemma_count_before = db_session.query(Lemma).count()
    with (
        patch.object(mapping_rescue, "_log_proposal_suggestion") as log_mock,
        patch(
            "app.services.lemma_quality.run_quality_gates"
        ) as quality_mock,
    ):
        stats = mapping_rescue.reverify_all_active_sentences(
            sentence_ids=[sent.id],
            dry_run=True,
        )

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    word = (
        db_session.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sent.id)
        .one()
    )
    assert stats.sentences_flagged == 1
    assert stats.sentences_corrected == 0
    assert db_session.query(Lemma).count() == lemma_count_before
    assert db_session.query(FrequencyCoreEntry).get(fce.id).lemma_id is None
    assert refreshed.mappings_verified_at == STALE
    assert word.lemma_id == wrong.lemma_id
    log_mock.assert_not_called()
    quality_mock.assert_not_called()


def test_secondary_multi_target_correction_keeps_primary_target(
    db_session,
    patched_verifier,
):
    """Correcting another target word must not retarget the primary sentence."""
    primary = _lemma(db_session, "بَيت", "house", bare="بيت", pos="noun")
    wrong_secondary = _lemma(
        db_session, "عَلِيّ", "Ali", bare="علي", pos="noun"
    )
    right_secondary = _lemma(
        db_session, "عَلَى", "on", bare="على", pos="preposition"
    )
    sent = _stale_sentence(
        db_session,
        [primary.lemma_id, wrong_secondary.lemma_id],
        target_id=primary.lemma_id,
    )
    secondary_word = (
        db_session.query(SentenceWord)
        .filter(
            SentenceWord.sentence_id == sent.id,
            SentenceWord.position == 1,
        )
        .one()
    )
    secondary_word.is_target_word = True
    db_session.commit()

    def correction(inputs, _lemma_map):
        return [{
            "disambiguation": [],
            "issues": [{
                "position": 1,
                "correct_lemma_ar": "عَلَى",
                "correct_gloss": "on",
                "correct_pos": "preposition",
                "explanation": "wrong homograph",
            }],
        } for _ in inputs]

    patched_verifier(correction)
    stats = mapping_rescue.reverify_all_active_sentences(
        sentence_ids=[sent.id],
    )

    db_session.expire_all()
    refreshed = db_session.query(Sentence).get(sent.id)
    refreshed_secondary = (
        db_session.query(SentenceWord)
        .filter(
            SentenceWord.sentence_id == sent.id,
            SentenceWord.position == 1,
        )
        .one()
    )
    assert refreshed.target_lemma_id == primary.lemma_id
    assert refreshed_secondary.lemma_id == right_secondary.lemma_id
    assert stats.sentences_flagged == 1
    assert stats.sentences_corrected == 1
    assert stats.targets_repaired == 0
