"""Tests for per-lemma generation backoff.

Chronically-failing words get a 7-day skip after 3 consecutive zero-result
generation attempts, so the 3h cron stops wasting LLM calls on them.
"""
from datetime import datetime, timedelta

from app.models import Lemma, UserLemmaKnowledge
from app.services.material_generator import (
    GENERATION_BACKOFF_DURATION,
    GENERATION_BACKOFF_THRESHOLD,
    lemmas_on_backoff,
    record_generation_result,
)


def _make_lemma_with_knowledge(db, lemma_ar: str, state: str = "known") -> Lemma:
    lemma = Lemma(lemma_ar=lemma_ar, lemma_ar_bare=lemma_ar, gloss_en="x")
    db.add(lemma)
    db.flush()
    ulk = UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state=state)
    db.add(ulk)
    db.commit()
    return lemma


def test_single_failure_increments_but_does_not_back_off(db_session):
    lemma = _make_lemma_with_knowledge(db_session, "مَبْسُوطَة")

    record_generation_result(db_session, lemma.lemma_id, 0)
    db_session.expire_all()

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.generation_failed_count == 1
    assert ulk.generation_backoff_until is None


def test_third_failure_sets_backoff(db_session):
    lemma = _make_lemma_with_knowledge(db_session, "طَيِّب")

    for _ in range(GENERATION_BACKOFF_THRESHOLD):
        record_generation_result(db_session, lemma.lemma_id, 0)
    db_session.expire_all()

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.generation_failed_count == GENERATION_BACKOFF_THRESHOLD
    assert ulk.generation_backoff_until is not None
    expected_floor = datetime.utcnow() + GENERATION_BACKOFF_DURATION - timedelta(minutes=1)
    assert ulk.generation_backoff_until >= expected_floor


def test_success_resets_failed_count_and_clears_backoff(db_session):
    lemma = _make_lemma_with_knowledge(db_session, "عَجِيب")

    # Drive into backoff
    for _ in range(GENERATION_BACKOFF_THRESHOLD):
        record_generation_result(db_session, lemma.lemma_id, 0)
    # A later successful run should reset both fields.
    record_generation_result(db_session, lemma.lemma_id, 2)
    db_session.expire_all()

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.generation_failed_count == 0
    assert ulk.generation_backoff_until is None


def test_record_ignores_lemma_without_knowledge(db_session):
    lemma = Lemma(lemma_ar="خَفِيف", lemma_ar_bare="خفيف", gloss_en="light")
    db_session.add(lemma)
    db_session.commit()

    # No ULK exists — should silently no-op, not raise.
    record_generation_result(db_session, lemma.lemma_id, 0)
    assert (
        db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
        is None
    )


def test_lemmas_on_backoff_filters_expired_entries(db_session):
    active_lemma = _make_lemma_with_knowledge(db_session, "صَعْب")
    expired_lemma = _make_lemma_with_knowledge(db_session, "سَهْل")
    untouched_lemma = _make_lemma_with_knowledge(db_session, "حُلْو")

    active_ulk = (
        db_session.query(UserLemmaKnowledge)
        .filter_by(lemma_id=active_lemma.lemma_id)
        .first()
    )
    active_ulk.generation_backoff_until = datetime.utcnow() + timedelta(days=3)

    expired_ulk = (
        db_session.query(UserLemmaKnowledge)
        .filter_by(lemma_id=expired_lemma.lemma_id)
        .first()
    )
    expired_ulk.generation_backoff_until = datetime.utcnow() - timedelta(hours=1)
    db_session.commit()

    result = lemmas_on_backoff(
        db_session,
        [active_lemma.lemma_id, expired_lemma.lemma_id, untouched_lemma.lemma_id],
    )
    assert result == {active_lemma.lemma_id}


def test_lemmas_on_backoff_empty_input(db_session):
    assert lemmas_on_backoff(db_session, []) == set()


# ── Backoff-aware multi-target group augmentation ──
# Backed-off lemmas piggy-back on healthy cohorts via multi-target collateral,
# capped at 1 per group of ≤4 healthy lemmas. Original concern from #37 was
# chronic failures crowding out viable lemmas; the cap keeps groups majority-
# healthy. A successful generation auto-resets the counter via the existing
# record_generation_result path.

def _w(lemma_id, root_id=None):
    return {"lemma_id": lemma_id, "lemma_ar": f"w{lemma_id}", "root_id": root_id}


def test_augment_skips_full_groups():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from update_material import _augment_groups_with_recovery

    full_group = [_w(1, 100), _w(2, 200), _w(3, 300), _w(4, 400)]
    recovery = [_w(99, 999)]
    out = _augment_groups_with_recovery([full_group], recovery)
    assert out == [full_group]  # unchanged — no slot available


def test_augment_adds_one_to_undersized_group():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from update_material import _augment_groups_with_recovery

    group = [_w(1, 100), _w(2, 200)]
    recovery = [_w(99, 999), _w(100, 998)]
    out = _augment_groups_with_recovery([group], recovery)
    assert len(out) == 1
    assert len(out[0]) == 3  # 1 recovery added
    assert out[0][:2] == group  # recovery appended, healthy intact


def test_augment_skips_recovery_with_root_collision():
    import sys
    import random as _random
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from update_material import _augment_groups_with_recovery

    _random.seed(0)
    group = [_w(1, 100), _w(2, 200)]
    # Both recovery candidates collide with the group's roots.
    recovery = [_w(99, 100), _w(100, 200)]
    out = _augment_groups_with_recovery([group], recovery)
    # No non-colliding option → group left unchanged.
    assert out[0] == group


def test_augment_distributes_one_per_group():
    import sys
    import random as _random
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from update_material import _augment_groups_with_recovery

    _random.seed(0)
    groups = [
        [_w(1, 100), _w(2, 200)],
        [_w(3, 300), _w(4, 400)],
    ]
    recovery = [_w(99, 901), _w(100, 902), _w(101, 903)]
    out = _augment_groups_with_recovery(groups, recovery)
    # Both groups had room; each gets exactly 1 recovery word.
    assert len(out[0]) == 3
    assert len(out[1]) == 3
    # Recovery lemmas present in result
    recovery_ids = {99, 100, 101}
    found = {w["lemma_id"] for g in out for w in g if w["lemma_id"] in recovery_ids}
    assert len(found) == 2  # one per group, third recovery word unused
