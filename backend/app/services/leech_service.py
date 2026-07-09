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

from app.models import FrequencyCoreEntry, Lemma, ReviewLog, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.frequency_lanes import is_low_priority_lemma
from app.services.interaction_logger import log_interaction

logger = logging.getLogger(__name__)

LEECH_MIN_REVIEWS = 5
LEECH_MAX_ACCURACY = 0.50
LEECH_WINDOW_SIZE = 8  # sliding window: use last N reviews for accuracy
LEECH_REINTRO_DAILY_CAP = 8
LEECH_REINTRO_BOX1_ADMISSION_LIMIT = 20

# Graduated cooldowns based on leech_count (how many times suspended before)
REINTRO_DELAYS = {
    0: timedelta(days=3),   # first suspension: 3 days
    1: timedelta(days=7),   # second: 7 days
    2: timedelta(days=14),  # third+: 14 days
}
LOW_PRIORITY_LEECH_DELAY_MULTIPLIER = 4
LOW_PRIORITY_LEECH_MAX_DELAY = timedelta(days=60)


def _get_reintro_delay(
    leech_count: int, lemma: Lemma | None = None, core_rank: int | None = None
) -> timedelta:
    """Return the reintroduction delay based on how many times this word has been leeched."""
    if leech_count <= 0:
        delay = REINTRO_DELAYS[0]
    elif leech_count == 1:
        delay = REINTRO_DELAYS[1]
    else:
        delay = REINTRO_DELAYS[2]
    if lemma is not None and is_low_priority_lemma(lemma, core_rank=core_rank):
        return min(delay * LOW_PRIORITY_LEECH_DELAY_MULTIPLIER, LOW_PRIORITY_LEECH_MAX_DELAY)
    return delay


def _get_lemma(db: Session, lemma_id: int) -> Lemma | None:
    return db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()


def _get_core_rank(db: Session, lemma_id: int) -> int | None:
    """Authoritative merged frequency rank for a lemma (None if not in the core)."""
    row = (
        db.query(FrequencyCoreEntry.core_rank)
        .filter(FrequencyCoreEntry.lemma_id == lemma_id)
        .first()
    )
    return row[0] if row else None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_active_reintro_episode(ulk: UserLemmaKnowledge) -> bool:
    """Return whether leech detection should use episode-local evidence.

    New episodes are explicit. Two narrow fallbacks keep rows that were already
    mid-reintroduction when the nullable episode column shipped from falling
    straight back into the historical-window trap:

    * legacy ``source='leech_reintro'`` rows;
    * rows with leech history that are currently acquiring, or that graduated
      from their most recent acquisition start. An original acquisition cannot
      acquire a positive ``leech_count`` and remain acquiring: suspension first
      changes its state, and only reintroduction starts acquisition again.

    This is runtime classification only; it does not rewrite historical episode
    metadata or change true-new intake accounting.
    """
    if ulk.acquisition_episode_kind == "leech_reintro":
        return True
    if ulk.acquisition_episode_kind == "new":
        return False
    if ulk.source == "leech_reintro":
        return True
    if not (ulk.leech_count or 0) or ulk.acquisition_started_at is None:
        return False
    if ulk.knowledge_state == "acquiring":
        return True
    started = _as_utc(ulk.acquisition_started_at)
    graduated = _as_utc(ulk.graduated_at)
    return bool(started and graduated and graduated >= started)


def _reintroductions_started_today(db: Session, now: datetime) -> int:
    """Count explicit reintroduction episodes admitted during this UTC day."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.acquisition_episode_kind == "leech_reintro",
            UserLemmaKnowledge.acquisition_started_at >= today_start,
        )
        .count()
    )


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
        since = ulk.acquisition_started_at if _is_active_reintro_episode(ulk) else None
        acc = _recent_accuracy(db, ulk.lemma_id, since=since)
        if acc is None:
            continue
        accuracy = acc
        if accuracy < LEECH_MAX_ACCURACY:
            lemma = _get_lemma(db, ulk.lemma_id)
            core_rank = _get_core_rank(db, ulk.lemma_id)
            ulk.knowledge_state = "suspended"
            ulk.leech_suspended_at = datetime.now(timezone.utc)
            ulk.leech_count = (ulk.leech_count or 0) + 1
            ulk.acquisition_box = None
            ulk.acquisition_next_due = None
            suspended.append(ulk.lemma_id)

            cooldown = _get_reintro_delay(ulk.leech_count - 1, lemma, core_rank=core_rank)
            log_interaction(
                event="leech_suspended",
                lemma_id=ulk.lemma_id,
                times_seen=ulk.times_seen,
                times_correct=ulk.times_correct,
                accuracy=round(accuracy, 3),
                leech_count=ulk.leech_count,
                reintro_days=cooldown.days,
                low_priority=lemma is not None and is_low_priority_lemma(lemma, core_rank=core_rank),
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
    from app.services.acquisition_service import (
        ACQUISITION_EPISODE_LEECH_REINTRO,
        RECOVERY_BOX2_DUE_LIMIT,
        RECOVERY_FSRS_MAIN_DUE_LIMIT,
        _main_fsrs_due_count,
        _recovery_backlog_counts,
        start_acquisition,
    )

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

    eligible: list[
        tuple[
            int,
            int,
            datetime,
            int,
            UserLemmaKnowledge,
            Lemma | None,
            int | None,
        ]
    ] = []
    for ulk in suspended_leeches:
        # Calculate per-word cooldown based on leech_count
        lc = (ulk.leech_count or 1) - 1  # count before this suspension
        lemma = _get_lemma(db, ulk.lemma_id)
        core_rank = _get_core_rank(db, ulk.lemma_id)
        delay = _get_reintro_delay(lc, lemma, core_rank=core_rank)
        suspended_at = ulk.leech_suspended_at
        if suspended_at.tzinfo is None:
            suspended_at = suspended_at.replace(tzinfo=timezone.utc)
        if suspended_at + delay > now:
            continue  # not ready yet

        # Lower leech count first, then better frequency rank, then the oldest
        # eligible suspension. This favors tractable, useful words without
        # starving an equally ranked older treatment.
        effective_rank = core_rank or (lemma.frequency_rank if lemma else None) or 1_000_000_000
        eligible.append((
            ulk.leech_count or 0,
            effective_rank,
            suspended_at,
            ulk.lemma_id,
            ulk,
            lemma,
            core_rank,
        ))

    eligible.sort(key=lambda row: row[:4])
    if not eligible:
        return []

    box1_actionable, box2_due = _recovery_backlog_counts(db, now)
    main_fsrs_due = _main_fsrs_due_count(db, now)
    admission_reasons = []
    if box1_actionable >= LEECH_REINTRO_BOX1_ADMISSION_LIMIT:
        admission_reasons.append("box1_actionable")
    if box2_due >= RECOVERY_BOX2_DUE_LIMIT:
        admission_reasons.append("box2_due")
    if main_fsrs_due >= RECOVERY_FSRS_MAIN_DUE_LIMIT:
        admission_reasons.append("main_fsrs_due")

    admitted_today = _reintroductions_started_today(db, now)
    daily_capacity = max(0, LEECH_REINTRO_DAILY_CAP - admitted_today)
    # Reintroductions enter Box 1. Respect the debt ceiling as a capacity, not
    # just a pre-flight check, so 19 actionable words cannot admit eight more
    # and overshoot the intended limit in one batch.
    box1_capacity = max(0, LEECH_REINTRO_BOX1_ADMISSION_LIMIT - box1_actionable)
    remaining_capacity = min(daily_capacity, box1_capacity)
    selected = [] if admission_reasons else eligible[:remaining_capacity]
    deferred_count = len(eligible) - len(selected)
    deferred_reasons = list(admission_reasons)
    if not admission_reasons and deferred_count:
        if daily_capacity < len(eligible):
            deferred_reasons.append("daily_cap")
        if box1_capacity < len(eligible):
            deferred_reasons.append("box1_headroom")
    if deferred_count:
        log_interaction(
            event="leech_reintro_capacity_deferred",
            eligible=len(eligible),
            admitted_today=admitted_today,
            daily_cap=LEECH_REINTRO_DAILY_CAP,
            deferred=deferred_count,
            admission_reasons=deferred_reasons,
            box1_actionable=box1_actionable,
            box2_due=box2_due,
            main_fsrs_due=main_fsrs_due,
        )

    reintroduced = []
    for _, _, _, _, ulk, lemma, core_rank in selected:

        # Preserve stats — don't zero times_seen/times_correct
        # Detection now gives the treatment an episode-local evidence window.
        ulk.leech_suspended_at = None
        # Keep curriculum provenance (book/textbook/etc.) separate from why
        # this acquisition episode restarted.
        start_acquisition(
            db,
            ulk.lemma_id,
            source=ulk.source or "study",
            episode_kind=ACQUISITION_EPISODE_LEECH_REINTRO,
        )
        reintroduced.append(ulk.lemma_id)

        log_interaction(
            event="leech_reintroduced",
            lemma_id=ulk.lemma_id,
            leech_count=ulk.leech_count,
            times_seen=ulk.times_seen,
            times_correct=ulk.times_correct,
            low_priority=lemma is not None and is_low_priority_lemma(lemma, core_rank=core_rank),
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
            detail={
                "lemma_ids": reintroduced,
                "stats_preserved": True,
                "daily_cap": LEECH_REINTRO_DAILY_CAP,
                "deferred_count": deferred_count,
                "deferred_reasons": deferred_reasons,
            },
        )

    return reintroduced


def _recent_accuracy(
    db: Session,
    lemma_id: int,
    window: int = LEECH_WINDOW_SIZE,
    since: datetime | None = None,
) -> float | None:
    """Compute accuracy over the last `window` reviews. Returns None if < LEECH_MIN_REVIEWS."""
    query = db.query(ReviewLog.rating).filter(ReviewLog.lemma_id == lemma_id)
    if since is not None:
        query = query.filter(ReviewLog.reviewed_at >= since)
    recent = query.order_by(ReviewLog.reviewed_at.desc()).limit(window).all()
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
        since = ulk.acquisition_started_at if _is_active_reintro_episode(ulk) else None
        acc = _recent_accuracy(db, ulk.lemma_id, since=since)
        if acc is not None:
            return acc < LEECH_MAX_ACCURACY
        if since is not None:
            # Old reviews explain why the word entered treatment; they cannot
            # end that treatment before it has produced five fresh observations.
            return False
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
        since = ulk.acquisition_started_at if _is_active_reintro_episode(ulk) else None
        acc = _recent_accuracy(db, lemma_id, since=since) or 0
        lemma = _get_lemma(db, lemma_id)
        core_rank = _get_core_rank(db, lemma_id)
        ulk.knowledge_state = "suspended"
        ulk.leech_suspended_at = datetime.now(timezone.utc)
        ulk.leech_count = (ulk.leech_count or 0) + 1
        ulk.acquisition_box = None
        ulk.acquisition_next_due = None

        cooldown = _get_reintro_delay(ulk.leech_count - 1, lemma, core_rank=core_rank)
        log_interaction(
            event="leech_suspended",
            lemma_id=lemma_id,
            times_seen=ulk.times_seen,
            times_correct=ulk.times_correct,
            recent_accuracy=round(acc, 3),
            leech_count=ulk.leech_count,
            reintro_days=cooldown.days,
            low_priority=lemma is not None and is_low_priority_lemma(lemma, core_rank=core_rank),
        )
        return True

    return False
