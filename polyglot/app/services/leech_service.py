"""Leech auto-management — detect and suspend chronically failing words.

A word becomes a leech when ``times_seen >= LEECH_MIN_REVIEWS`` and recent
accuracy (sliding window of the last ``LEECH_WINDOW_SIZE`` reviews) drops
below ``LEECH_MAX_ACCURACY``. The sliding window matters: cumulative
accuracy creates an "escape trap" where a word that's improved still can't
shake leech status. The window lets recent good runs evict bad history.

Graduated cooldowns on reintroduction:
    1st suspension → 3 days
    2nd suspension → 7 days
    3rd+           → 14 days

Stats are preserved across leech cycles — the word must genuinely improve
recent performance to escape, not just be reintroduced and treated as new.

Ported from Alif's `leech_service`. Key differences:

- Polyglot has no ``material_generator``/``memory_hooks`` yet, so reintroduction
  doesn't pre-generate sentences or mnemonics. When polyglot adds sentence
  generation, wire it here the same way Alif does.
- ``is_low_priority_lemma`` (Alif's frequency-lane helper) is replaced by a
  rank threshold check on ``Lemma.frequency_rank`` — simpler and
  language-agnostic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.interaction_logger import log_interaction

logger = logging.getLogger(__name__)

LEECH_MIN_REVIEWS = 5
LEECH_MAX_ACCURACY = 0.50
LEECH_WINDOW_SIZE = 8  # sliding window: last N reviews

REINTRO_DELAYS = {
    0: timedelta(days=3),   # first suspension
    1: timedelta(days=7),   # second
    2: timedelta(days=14),  # third+
}

# A lemma counts as "low priority" (longer leech cooldowns) when its
# frequency rank is beyond this threshold. NULL frequency_rank → not low
# priority (we don't penalize lemmas we never frequency-ranked).
LOW_PRIORITY_FREQUENCY_RANK_THRESHOLD = 5000
LOW_PRIORITY_LEECH_DELAY_MULTIPLIER = 4
LOW_PRIORITY_LEECH_MAX_DELAY = timedelta(days=60)


def _is_low_priority(lemma: Lemma | None) -> bool:
    if lemma is None:
        return False
    rank = lemma.frequency_rank
    return rank is not None and rank > LOW_PRIORITY_FREQUENCY_RANK_THRESHOLD


def _get_reintro_delay(leech_count: int, lemma: Lemma | None = None) -> timedelta:
    """Cooldown until a suspended leech becomes eligible for reintroduction."""
    if leech_count <= 0:
        delay = REINTRO_DELAYS[0]
    elif leech_count == 1:
        delay = REINTRO_DELAYS[1]
    else:
        delay = REINTRO_DELAYS[2]
    if _is_low_priority(lemma):
        return min(delay * LOW_PRIORITY_LEECH_DELAY_MULTIPLIER, LOW_PRIORITY_LEECH_MAX_DELAY)
    return delay


def _get_lemma(db: Session, lemma_id: int) -> Lemma | None:
    return db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()


def _recent_accuracy(
    db: Session, lemma_id: int, window: int = LEECH_WINDOW_SIZE
) -> float | None:
    """Accuracy over the last ``window`` reviews. ``None`` if the window is too small."""
    recent = (
        db.query(ReviewLog.rating)
        .filter(ReviewLog.lemma_id == lemma_id)
        .order_by(ReviewLog.reviewed_at.desc())
        .limit(window)
        .all()
    )
    if len(recent) < LEECH_MIN_REVIEWS:
        return None
    correct = sum(1 for (r,) in recent if r >= 3)
    return correct / len(recent)


def is_leech(ulk: UserLemmaKnowledge, db: Session | None = None) -> bool:
    """Does this ULK currently meet the leech criteria?"""
    if (ulk.times_seen or 0) < LEECH_MIN_REVIEWS:
        return False
    if db is not None:
        acc = _recent_accuracy(db, ulk.lemma_id)
        if acc is not None:
            return acc < LEECH_MAX_ACCURACY
    # Fallback to cumulative accuracy when no DB session is available
    accuracy = (ulk.times_correct or 0) / (ulk.times_seen or 1)
    return accuracy < LEECH_MAX_ACCURACY


def check_and_manage_leeches(db: Session) -> list[int]:
    """Sweep all active words and auto-suspend any that meet leech criteria.

    Returns the lemma_ids suspended. Safe to run on a cron tick.
    """
    candidates = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state.in_(
                ["learning", "known", "lapsed", "acquiring"]
            ),
            UserLemmaKnowledge.times_seen >= LEECH_MIN_REVIEWS,
        )
        .all()
    )

    suspended: list[int] = []
    for ulk in candidates:
        acc = _recent_accuracy(db, ulk.lemma_id)
        if acc is None:
            continue
        if acc < LEECH_MAX_ACCURACY:
            lemma = _get_lemma(db, ulk.lemma_id)
            ulk.knowledge_state = "suspended"
            ulk.leech_suspended_at = datetime.now(timezone.utc)
            ulk.leech_count = (ulk.leech_count or 0) + 1
            ulk.acquisition_box = None
            ulk.acquisition_next_due = None
            suspended.append(ulk.lemma_id)

            cooldown = _get_reintro_delay(ulk.leech_count - 1, lemma)
            log_interaction(
                event="leech_suspended",
                lemma_id=ulk.lemma_id,
                times_seen=ulk.times_seen,
                times_correct=ulk.times_correct,
                accuracy=round(acc, 3),
                leech_count=ulk.leech_count,
                reintro_days=cooldown.days,
                low_priority=_is_low_priority(lemma),
            )

    if suspended:
        db.commit()
        log_activity(
            db,
            event_type="leech_suspended",
            summary=f"Auto-suspended {len(suspended)} leech words",
            detail={"lemma_ids": suspended},
        )

    return suspended


def check_single_word_leech(db: Session, lemma_id: int) -> bool:
    """Per-review check called after a submit. True if just suspended."""
    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if not ulk or ulk.knowledge_state == "suspended":
        return False

    if is_leech(ulk, db=db):
        acc = _recent_accuracy(db, lemma_id) or 0
        lemma = _get_lemma(db, lemma_id)
        ulk.knowledge_state = "suspended"
        ulk.leech_suspended_at = datetime.now(timezone.utc)
        ulk.leech_count = (ulk.leech_count or 0) + 1
        ulk.acquisition_box = None
        ulk.acquisition_next_due = None
        db.commit()

        cooldown = _get_reintro_delay(ulk.leech_count - 1, lemma)
        log_interaction(
            event="leech_suspended",
            lemma_id=lemma_id,
            times_seen=ulk.times_seen,
            times_correct=ulk.times_correct,
            recent_accuracy=round(acc, 3),
            leech_count=ulk.leech_count,
            reintro_days=cooldown.days,
            low_priority=_is_low_priority(lemma),
        )
        return True

    return False


def check_leech_reintroductions(db: Session) -> list[int]:
    """Reintroduce suspended leeches whose cooldown has elapsed.

    Stats are preserved — the word must genuinely improve to escape leech
    status on the next pass. ``start_acquisition`` is called with
    ``source='leech_reintro'`` to bypass the daily intro cap.
    """
    from app.services.acquisition_service import start_acquisition

    now = datetime.now(timezone.utc)

    suspended_leeches = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state == "suspended",
            UserLemmaKnowledge.leech_suspended_at.isnot(None),
        )
        .all()
    )

    reintroduced: list[int] = []
    for ulk in suspended_leeches:
        # leech_count is incremented when we suspend; subtract 1 to get the
        # count BEFORE this suspension, which is the delay tier.
        lc = (ulk.leech_count or 1) - 1
        lemma = _get_lemma(db, ulk.lemma_id)
        delay = _get_reintro_delay(lc, lemma)
        suspended_at = ulk.leech_suspended_at
        if suspended_at.tzinfo is None:
            suspended_at = suspended_at.replace(tzinfo=timezone.utc)
        if suspended_at + delay > now:
            continue

        ulk.leech_suspended_at = None
        start_acquisition(db, ulk.lemma_id, source="leech_reintro")
        reintroduced.append(ulk.lemma_id)

        log_interaction(
            event="leech_reintroduced",
            lemma_id=ulk.lemma_id,
            leech_count=ulk.leech_count,
            times_seen=ulk.times_seen,
            times_correct=ulk.times_correct,
            low_priority=_is_low_priority(lemma),
        )

    if reintroduced:
        db.commit()
        log_activity(
            db,
            event_type="leech_reintroduced",
            summary=f"Reintroduced {len(reintroduced)} leech words to acquisition",
            detail={"lemma_ids": reintroduced, "stats_preserved": True},
        )

    return reintroduced
