"""Leech auto-management — detect and handle chronically failing words.

A word is a leech if: recent accuracy < 50% (sliding window of last N reviews,
where N = LEECH_WINDOW_SIZE) AND total reviews >= LEECH_MIN_REVIEWS.

Leeches get graduated cooldowns based on leech_count:
  1st suspension: 3 days
  2nd suspension: 7 days
  3rd+ suspension: 14 days

On reintroduction: stats are preserved for overall tracking, but leech
detection uses a sliding window so words can escape with improved performance.

2026-02-14: Graduated cooldown, preserved stats, memory hook integration.
2026-03-15: Switched to sliding window for leech detection to fix escape trap.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.interaction_logger import log_interaction

logger = logging.getLogger(__name__)

LEECH_MIN_REVIEWS = 5
LEECH_MAX_ACCURACY = 0.50
LEECH_WINDOW_SIZE = 8  # sliding window: use last N reviews for accuracy

# Graduated cooldowns based on leech_count (how many times suspended before)
REINTRO_DELAYS = {
    0: timedelta(days=3),   # first suspension: 3 days
    1: timedelta(days=7),   # second: 7 days
    2: timedelta(days=14),  # third+: 14 days
}


def _get_reintro_delay(leech_count: int) -> timedelta:
    """Return the reintroduction delay based on how many times this word has been leeched."""
    if leech_count <= 0:
        return REINTRO_DELAYS[0]
    elif leech_count == 1:
        return REINTRO_DELAYS[1]
    else:
        return REINTRO_DELAYS[2]


def check_and_manage_leeches(db: Session) -> list[int]:
    """Check all active words for leech status and auto-suspend leeches.

    Returns list of lemma_ids that were suspended.
    """
    candidates = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "lapsed", "acquiring"]),
            UserLemmaKnowledge.times_seen >= LEECH_MIN_REVIEWS,
        )
        .all()
    )

    suspended = []
    for ulk in candidates:
        acc = _recent_accuracy(db, ulk.lemma_id)
        if acc is None:
            continue
        accuracy = acc
        if accuracy < LEECH_MAX_ACCURACY:
            ulk.knowledge_state = "suspended"
            ulk.leech_suspended_at = datetime.now(timezone.utc)
            ulk.leech_count = (ulk.leech_count or 0) + 1
            ulk.acquisition_box = None
            ulk.acquisition_next_due = None
            suspended.append(ulk.lemma_id)

            cooldown = _get_reintro_delay(ulk.leech_count - 1)
            log_interaction(
                event="leech_suspended",
                lemma_id=ulk.lemma_id,
                times_seen=ulk.times_seen,
                times_correct=ulk.times_correct,
                accuracy=round(accuracy, 3),
                leech_count=ulk.leech_count,
                reintro_days=cooldown.days,
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


def check_leech_reintroductions(db: Session) -> list[int]:
    """Check for leeches ready for reintroduction based on graduated cooldown.

    Cooldown: 3d (1st), 7d (2nd), 14d (3rd+) based on leech_count.
    Stats are preserved — word must genuinely improve to escape leech status.
    Fresh sentences generated and memory hooks ensured.
    """
    from app.services.acquisition_service import start_acquisition

    now = datetime.now(timezone.utc)

    # Fetch all suspended leeches (those with leech_suspended_at set)
    suspended_leeches = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state == "suspended",
            UserLemmaKnowledge.leech_suspended_at.isnot(None),
        )
        .all()
    )

    reintroduced = []
    for ulk in suspended_leeches:
        # Calculate per-word cooldown based on leech_count
        lc = (ulk.leech_count or 1) - 1  # count before this suspension
        delay = _get_reintro_delay(lc)
        suspended_at = ulk.leech_suspended_at
        if suspended_at.tzinfo is None:
            suspended_at = suspended_at.replace(tzinfo=timezone.utc)
        if suspended_at + delay > now:
            continue  # not ready yet

        # Preserve stats — don't zero times_seen/times_correct
        # The word must genuinely improve since leech detection uses cumulative accuracy
        ulk.leech_suspended_at = None
        start_acquisition(db, ulk.lemma_id, source="leech_reintro")
        reintroduced.append(ulk.lemma_id)

        log_interaction(
            event="leech_reintroduced",
            lemma_id=ulk.lemma_id,
            leech_count=ulk.leech_count,
            times_seen=ulk.times_seen,
            times_correct=ulk.times_correct,
        )

    if reintroduced:
        db.commit()

        # Generate fresh sentences and ensure memory hooks (background, best-effort)
        for lid in reintroduced:
            try:
                from app.services.material_generator import generate_material_for_word
                generate_material_for_word(lid, needed=2)
            except Exception:
                logger.warning(f"Failed to generate material for reintroduced leech {lid}")

            try:
                from app.services.memory_hooks import generate_memory_hooks
                lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
                if lemma and not lemma.memory_hooks_json:
                    generate_memory_hooks(lid)
            except Exception:
                logger.warning(f"Failed to generate memory hooks for reintroduced leech {lid}")

        log_activity(
            db,
            event_type="leech_reintroduced",
            summary=f"Reintroduced {len(reintroduced)} leech words to acquisition",
            detail={"lemma_ids": reintroduced, "stats_preserved": True},
        )

    return reintroduced


def _recent_accuracy(db: Session, lemma_id: int, window: int = LEECH_WINDOW_SIZE) -> float | None:
    """Compute accuracy over the last `window` reviews. Returns None if < LEECH_MIN_REVIEWS."""
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
    """Check if a word meets leech criteria using sliding window accuracy.

    Uses last LEECH_WINDOW_SIZE reviews instead of cumulative stats so that
    words can escape leech status by improving recent performance.
    """
    if (ulk.times_seen or 0) < LEECH_MIN_REVIEWS:
        return False
    if db is not None:
        acc = _recent_accuracy(db, ulk.lemma_id)
        if acc is not None:
            return acc < LEECH_MAX_ACCURACY
    # Fallback to cumulative if no db session provided
    accuracy = (ulk.times_correct or 0) / (ulk.times_seen or 1)
    return accuracy < LEECH_MAX_ACCURACY


def check_single_word_leech(db: Session, lemma_id: int) -> bool:
    """Check if a specific word just became a leech after a review.

    Call this after each review submission. Returns True if word was suspended.
    """
    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if not ulk or ulk.knowledge_state == "suspended":
        return False

    if is_leech(ulk, db=db):
        acc = _recent_accuracy(db, lemma_id) or 0
        ulk.knowledge_state = "suspended"
        ulk.leech_suspended_at = datetime.now(timezone.utc)
        ulk.leech_count = (ulk.leech_count or 0) + 1
        ulk.acquisition_box = None
        ulk.acquisition_next_due = None

        cooldown = _get_reintro_delay(ulk.leech_count - 1)
        log_interaction(
            event="leech_suspended",
            lemma_id=lemma_id,
            times_seen=ulk.times_seen,
            times_correct=ulk.times_correct,
            recent_accuracy=round(acc, 3),
            leech_count=ulk.leech_count,
            reintro_days=cooldown.days,
        )
        return True

    return False
