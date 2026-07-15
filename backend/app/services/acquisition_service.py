"""Acquisition system — Leitner 3-box for newly introduced words.

Words go through three acquisition boxes before graduating to FSRS:
  Box 1: 4-hour interval (within-session advancement allowed)
  Box 2: 1-day interval (must be due before advancing)
  Box 3: 3-day interval (must be due before graduating)

Box 1→2 is "encoding" — allowed within a single session for initial repetition,
except when the review is still inside the intro-card working-memory window.
Box 2→3 and 3→graduation enforce real inter-session spacing (sleep consolidation).

Graduation is tiered (2026-03-03):
  - First correct review (times_seen was 0, rating >= 3) → instant graduation
  - Elapsed-interval (Tier E): correct review after >= 3 days real gap → any box
  - Perfect accuracy (100%) + 3+ reviews → graduate from any box
  - High accuracy (≥80%) + 4+ reviews + box ≥ 2 → graduate
  - Standard: box >= 3 + times_seen >= 5 + accuracy >= 60% + 2 calendar days

2026-02-14: Added due-date gating for box 2+ and calendar-day graduation check.
2026-03-03: Added tiered graduation — first-correct instant grad + relaxed criteria.
2026-07-08: Added Tier E (elapsed-interval) — a long real retention interval is
            direct proof of consolidation the fixed Leitner intervals discarded.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import Lemma, ReviewLog, Root, UserLemmaKnowledge
from app.services.fsrs_service import create_new_card, parse_json_column, STATE_MAP
from app.services.interaction_logger import log_interaction

logger = logging.getLogger(__name__)

BOX_INTERVALS = {
    1: timedelta(hours=4),
    2: timedelta(days=1),
    3: timedelta(days=3),
}

GRADUATION_MIN_REVIEWS = 5
GRADUATION_MIN_ACCURACY = 0.60
GRADUATION_MIN_CALENDAR_DAYS = 2
ROOT_SIBLING_THRESHOLD = 2  # known root siblings needed for Easy graduation boost
# Tier-0 (first-correct) instant graduation must NOT fire when the intro card
# was shown moments ago — that's working memory, not learning. Require this
# much elapsed time since the intro card before allowing fast-grad.
FAST_GRAD_INTRO_GAP = timedelta(minutes=10)
# Correct reviews inside the intro-card gap count for exposure/accuracy, but
# should stay in Box 1 and come back soon instead of proving consolidation.
FAST_INTRO_RETRY_INTERVAL = timedelta(minutes=30)
# Tier E (elapsed-interval) graduation: a correct review after at least this much
# real elapsed time since the previous review demonstrates durable retention that
# already exceeds a fresh graduate's initial FSRS stability (S₀(Good) ≈ 2.3d) and
# the deepest Leitner rung (Box 3 = 3d). The fixed box intervals otherwise discard
# this signal; FSRS's whole premise is that surviving a long interval is high-value
# evidence, so a word recognized after this long graduates straight to FSRS.
ELAPSED_GRADUATION_MIN_INTERVAL = timedelta(days=3)


def _intro_shown_recently(ulk: UserLemmaKnowledge, now: datetime) -> bool:
    intro_shown = ulk.experiment_intro_shown_at
    if intro_shown is None:
        return False
    if intro_shown.tzinfo is None:
        intro_shown = intro_shown.replace(tzinfo=timezone.utc)
    gap = now - intro_shown
    return timedelta(0) <= gap < FAST_GRAD_INTRO_GAP


def _reviews_span_calendar_days(db: Session, lemma_id: int, min_days: int) -> bool:
    """Check if acquisition reviews for a word span at least N distinct UTC calendar days."""
    reviews = (
        db.query(ReviewLog.reviewed_at)
        .filter(
            ReviewLog.lemma_id == lemma_id,
            ReviewLog.is_acquisition == True,  # noqa: E712
        )
        .all()
    )
    dates = set()
    for (reviewed_at,) in reviews:
        if reviewed_at:
            dt = reviewed_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dates.add(dt.date())
    return len(dates) >= min_days


DAILY_INTRO_CAP = 30  # ceiling on new acquiring promotions per UTC day; see start_acquisition
ACQUISITION_EPISODE_NEW = "new"
ACQUISITION_EPISODE_LEECH_REINTRO = "leech_reintro"
_ACQUISITION_EPISODE_KINDS = {
    ACQUISITION_EPISODE_NEW,
    ACQUISITION_EPISODE_LEECH_REINTRO,
}

# Recovery-mode intro budget. These gates only bind when the acquisition
# pipeline is overloaded; normal low-debt days keep the hard 30/day ceiling.
#
# The earned FULL budget is the lever for fast vocabulary growth: a learner who
# does 100+ sentence reviews/day at >=85% word accuracy has demonstrated they can
# absorb new words, so they earn the full daily cap. The 2026-06-03 throttle
# simulation (research/analysis-2026-06-03-throttle-simulation.md) showed raising
# FULL 8->30 gives a high-accuracy learner +53% known growth at ~3pt comprehension
# cost, while low-accuracy learners (kept at the modest MID budget by the accuracy
# floors) see no growth benefit and only backlog — so the raise lives in FULL, NOT
# in the trigger or the accuracy floors, which are the safety gate.
RECOVERY_BOX1_UNREVIEWED_LIMIT = 5
RECOVERY_BOX2_DUE_LIMIT = 30
RECOVERY_FSRS_MAIN_DUE_LIMIT = 750
RECOVERY_MIN_SENTENCES_FOR_ANY_INTRO = 40
RECOVERY_MIN_SENTENCES_FOR_FULL_BUDGET = 100
RECOVERY_LOW_ACCURACY_FLOOR = 0.80
RECOVERY_GOOD_ACCURACY_FLOOR = 0.85
RECOVERY_MID_INTRO_BUDGET = 8
RECOVERY_FULL_INTRO_BUDGET = DAILY_INTRO_CAP  # 30 — full cap, earned by accuracy+volume
_FSRS_DUE_CACHE_TTL = timedelta(seconds=5)
_FSRS_DUE_CACHE_KEY = "alif_main_fsrs_due_count"


def true_new_acquisition_episode_filter():
    """SQL predicate for acquisition starts that consume new-word budget.

    New writes carry an explicit episode kind. Historical rows predate that
    column, so NULL episodes count as new unless their legacy ``source`` says
    they were a leech reintroduction. ``leech_count`` is intentionally not a
    fallback: the first suspension increments it while
    ``acquisition_started_at`` still identifies the original new episode.
    """
    return or_(
        UserLemmaKnowledge.acquisition_episode_kind == ACQUISITION_EPISODE_NEW,
        and_(
            UserLemmaKnowledge.acquisition_episode_kind.is_(None),
            or_(
                UserLemmaKnowledge.source.is_(None),
                UserLemmaKnowledge.source != ACQUISITION_EPISODE_LEECH_REINTRO,
            ),
        ),
    )


def _daily_intro_count(db: Session, today_start: datetime) -> int:
    """Count today's acquisitions that count toward the daily cap.

    Leech reintroduction episodes are excluded even when their meaningful
    source provenance (for example ``book``) is preserved.
    """
    return (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.acquisition_started_at >= today_start,
            true_new_acquisition_episode_filter(),
        )
        .count()
    )


def _recovery_backlog_counts(db: Session, now: datetime) -> tuple[int, int]:
    """Return (actionable/protected box-1 count, due box-2 count).

    Both counts measure practice debt that should block further intake. New,
    never-reviewed Box-1 words stay protected even before their first due time;
    previously-seen Box-1 words count only when actionable. Words the session
    pipeline can never serve are excluded — otherwise they pin recovery mode
    permanently (2026-06-10: 2 proper-name rows stuck since May 4 plus 4
    generation-backed-off words held the box-1 count over the trigger limit
    for weeks, capping daily intros at the earned-recovery budget):

    - inert word categories (proper_name/onomatopoeia) are filtered out of
      word selection and earn no review credit, so they can never advance;
    - words inside a generation backoff window have no sentence and cannot
      get one until the backoff expires (box-1 only: box-2 words were already
      served at least once, so their debt is real even while backed off).
    """
    inert_or_null_category = Lemma.word_category.is_(None) | Lemma.word_category.notin_(
        ["proper_name", "onomatopoeia"]
    )
    box1_actionable = (
        db.query(UserLemmaKnowledge)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_box == 1,
            # Never-reviewed words stay protected even before their first due
            # time. Previously-seen Box-1 words count once they are due; this is
            # where failed/reintroduced leeches otherwise disappeared from the
            # recovery trigger.
            (
                (UserLemmaKnowledge.times_seen == 0)
                | (UserLemmaKnowledge.times_seen.is_(None))
                | (UserLemmaKnowledge.acquisition_next_due <= now)
            ),
            inert_or_null_category,
            UserLemmaKnowledge.generation_backoff_until.is_(None)
            | (UserLemmaKnowledge.generation_backoff_until <= now),
        )
        .count()
    )
    box2_due = (
        db.query(UserLemmaKnowledge)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_box == 2,
            UserLemmaKnowledge.acquisition_next_due <= now,
            inert_or_null_category,
        )
        .count()
    )
    return box1_actionable, box2_due


def _main_fsrs_due_count(db: Session, now: datetime) -> int:
    """Count actionable main-lane FSRS debt for sustained-break recovery.

    The threshold intentionally uses the same lane classifier as session
    building, rather than raw API due counts that include suspended, inert, and
    slow-lane artifact rows. Production checkpoints put normal active-day debt
    at 343–439 (observed high 576), about two sparse days at 672, and a five-day
    break at 806. The 750 trigger therefore catches a real hiatus without
    turning ordinary maintenance into permanent recovery mode.
    """
    cached = db.info.get(_FSRS_DUE_CACHE_KEY)
    if cached:
        cached_at, cached_count = cached
        if timedelta(0) <= now - cached_at <= _FSRS_DUE_CACHE_TTL:
            return cached_count

    from app.services.frequency_lanes import due_lane_snapshot
    snapshot = due_lane_snapshot(db, now)
    due_ids = snapshot.main_due_ids & snapshot.fsrs_due_ids
    inert_ids: set[int] = set()
    overshadowed_variants: set[int] = set()
    if due_ids:
        from app.services.canonical_resolution import resolve_canonical_via_map

        lemma_rows = db.query(
            Lemma.lemma_id,
            Lemma.canonical_lemma_id,
            Lemma.word_category,
        ).all()
        canonical_by_id = {
            lemma_id: canonical_id
            for lemma_id, canonical_id, _category in lemma_rows
        }
        inert_ids = {
            lemma_id
            for lemma_id, _canonical_id, category in lemma_rows
            if lemma_id in due_ids and category in {"proper_name", "onomatopoeia"}
        }
        canonical_targets = {
            lemma_id: resolve_canonical_via_map(lemma_id, canonical_by_id)
            for lemma_id in due_ids
        }
        target_ids = {
            target_id
            for lemma_id, target_id in canonical_targets.items()
            if target_id != lemma_id
        }
        canonical_states = {
            lemma_id: state
            for lemma_id, state in db.query(
                UserLemmaKnowledge.lemma_id,
                UserLemmaKnowledge.knowledge_state,
            ).filter(UserLemmaKnowledge.lemma_id.in_(target_ids)).all()
        } if target_ids else {}
        overshadowed_variants = {
            lemma_id
            for lemma_id, target_id in canonical_targets.items()
            if target_id != lemma_id
            and canonical_states.get(target_id) in {"known", "learning"}
        }
    count = len(due_ids - inert_ids - overshadowed_variants)
    # One review request can promote several collateral/cold words. Those
    # promotions do not create FSRS debt, so reparsing every active card for
    # each start only adds latency. A short session-local cache collapses that
    # burst while expiring quickly for long-lived script sessions.
    db.info[_FSRS_DUE_CACHE_KEY] = (now, count)
    return count


def _primary_reading_reviews_today(db: Session, today_start: datetime) -> list[ReviewLog]:
    """Primary reading answers today — the earn-in unit for recovery intros.

    One primary reading ReviewLog corresponds to one answered reading card.
    SentenceReviewLog cannot be used as the unit: a single passage answer
    writes one child row per sentence, and collateral ReviewLogs inflate both
    volume and accuracy without testing the card's retrieval target.
    """
    return (
        db.query(ReviewLog)
        .filter(
            ReviewLog.reviewed_at >= today_start,
            ReviewLog.review_mode == "reading",
            ReviewLog.credit_type == "primary",
        )
        .all()
    )


def recovery_status(db: Session, now: datetime | None = None) -> dict:
    """Read-only snapshot of the recovery-gate state for the stats panel.

    Companion to _recovery_mode_intro_budget: the same counts and thresholds
    that gate intake, plus the earn-in progress numbers, so the learner can see
    how far the backlog is from re-opening intros and leech reintroduction.
    """
    from app.services.leech_service import LEECH_REINTRO_BOX1_ADMISSION_LIMIT

    if now is None:
        now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    box1_actionable, box2_due = _recovery_backlog_counts(db, now)
    main_fsrs_due = _main_fsrs_due_count(db, now)
    active = (
        box1_actionable >= RECOVERY_BOX1_UNREVIEWED_LIMIT
        or box2_due >= RECOVERY_BOX2_DUE_LIMIT
        or main_fsrs_due >= RECOVERY_FSRS_MAIN_DUE_LIMIT
    )
    intro_budget_today = _recovery_mode_intro_budget(db, now, today_start)
    intros_used_today = _daily_intro_count(db, today_start)

    primary = _primary_reading_reviews_today(db, today_start)
    reading_cards_today = len(primary)
    primary_accuracy_today = (
        round(sum(1 for r in primary if r.rating >= 3) / reading_cards_today * 100, 1)
        if reading_cards_today >= 10
        else None
    )

    return {
        "active": active,
        "box1_actionable": box1_actionable,
        "box1_trigger_limit": RECOVERY_BOX1_UNREVIEWED_LIMIT,
        "box1_reintro_admission_limit": LEECH_REINTRO_BOX1_ADMISSION_LIMIT,
        "box2_due": box2_due,
        "box2_limit": RECOVERY_BOX2_DUE_LIMIT,
        "main_fsrs_due": main_fsrs_due,
        "main_fsrs_limit": RECOVERY_FSRS_MAIN_DUE_LIMIT,
        "intro_budget_today": intro_budget_today,
        "intros_used_today": intros_used_today,
        "reading_cards_today": reading_cards_today,
        "reading_cards_for_any_intro": RECOVERY_MIN_SENTENCES_FOR_ANY_INTRO,
        "reading_cards_for_full_budget": RECOVERY_MIN_SENTENCES_FOR_FULL_BUDGET,
        "primary_accuracy_today": primary_accuracy_today,
    }


def _recovery_mode_intro_budget(db: Session, now: datetime, today_start: datetime) -> int:
    """Return the effective daily intro budget under acquisition overload.

    When Box 1/2 debt is normal, this returns DAILY_INTRO_CAP so the normal
    aggressive learning path is unchanged. When the pipeline is overloaded,
    new words must be earned by same-day sentence practice and accuracy.
    """
    box1_actionable, box2_due = _recovery_backlog_counts(db, now)
    overloaded = (
        box1_actionable >= RECOVERY_BOX1_UNREVIEWED_LIMIT
        or box2_due >= RECOVERY_BOX2_DUE_LIMIT
    )
    # Acquisition debt is much cheaper to count and already establishes
    # recovery mode on most overloaded days. Only scan/parse all FSRS cards
    # when the acquisition boxes themselves are healthy.
    if not overloaded:
        main_fsrs_due = _main_fsrs_due_count(db, now)
        overloaded = main_fsrs_due >= RECOVERY_FSRS_MAIN_DUE_LIMIT
    if not overloaded:
        return DAILY_INTRO_CAP

    primary_reading_reviews = _primary_reading_reviews_today(db, today_start)
    reading_cards_today = len(primary_reading_reviews)
    if reading_cards_today < RECOVERY_MIN_SENTENCES_FOR_ANY_INTRO:
        return 0

    accuracy = None
    if len(primary_reading_reviews) >= 10:
        accuracy = (
            sum(1 for r in primary_reading_reviews if r.rating >= 3)
            / len(primary_reading_reviews)
        )
        if accuracy < RECOVERY_LOW_ACCURACY_FLOOR:
            return 0

    if accuracy is not None and accuracy < RECOVERY_GOOD_ACCURACY_FLOOR:
        return RECOVERY_MID_INTRO_BUDGET

    if reading_cards_today >= RECOVERY_MIN_SENTENCES_FOR_FULL_BUDGET:
        return RECOVERY_FULL_INTRO_BUDGET
    return RECOVERY_MID_INTRO_BUDGET


def start_acquisition(
    db: Session,
    lemma_id: int,
    source: str = "study",
    due_immediately: bool = False,
    enforce_daily_cap: bool = True,
    episode_kind: str = ACQUISITION_EPISODE_NEW,
) -> UserLemmaKnowledge:
    """Start the acquisition process for a word.

    Creates or transitions ULK to acquiring state with box 1.
    If due_immediately=True, word is due right now (for auto-intro in current session).
    Otherwise, first review is due after BOX_INTERVALS[1] (4 hours).

    When enforce_daily_cap is True (default) and the daily cap is hit, the
    word is left in (or created in) `encountered` state instead of being
    promoted. Callers must check `ulk.knowledge_state == "acquiring"` if
    they need to behave differently when promotion was deferred.
    ``episode_kind='leech_reintro'`` bypasses the cap because it restarts an
    old word rather than adding vocabulary. ``source='leech_reintro'`` remains
    accepted as a compatibility shim for legacy callers, but is not written as
    provenance for a new row.
    """
    from app.services.canonical_resolution import resolve_canonical_lemma_id

    # Variants must never get their own scheduling rows; redirect to canonical.
    canonical_id = resolve_canonical_lemma_id(db, lemma_id)
    if canonical_id != lemma_id:
        logger.info(
            "start_acquisition redirecting variant %s → canonical %s (source=%r)",
            lemma_id, canonical_id, source,
        )
        lemma_id = canonical_id

    if source == ACQUISITION_EPISODE_LEECH_REINTRO:
        # Compatibility for old callers while provenance and episode semantics
        # migrate to separate fields.
        episode_kind = ACQUISITION_EPISODE_LEECH_REINTRO
    if episode_kind not in _ACQUISITION_EPISODE_KINDS:
        raise ValueError(f"Unsupported acquisition episode kind: {episode_kind!r}")

    now = datetime.now(timezone.utc)
    next_due = now if due_immediately else now + BOX_INTERVALS[1]

    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )

    # Don't demote a known/learning canonical back to acquiring just because a
    # variant collateral path landed here. Return the existing row unchanged.
    if ulk and ulk.knowledge_state in ("known", "learning"):
        return ulk

    # Already acquiring — nothing to do, return as-is (not a new intro).
    if ulk and ulk.knowledge_state == "acquiring":
        return ulk

    cap_hit = False
    if enforce_daily_cap and episode_kind != ACQUISITION_EPISODE_LEECH_REINTRO:
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        intro_count = _daily_intro_count(db, today_start)
        effective_daily_cap = _recovery_mode_intro_budget(db, now, today_start)
        if intro_count >= effective_daily_cap:
            cap_hit = True
            logger.info(
                "Daily intro budget (%d/%d) reached; lemma %s stays in encountered "
                "(source=%r, today_count=%d)",
                effective_daily_cap, DAILY_INTRO_CAP, lemma_id, source, intro_count,
            )

    if cap_hit:
        if ulk:
            # Don't promote — keep whatever state it was in (encountered, or other).
            return ulk
        # New unseen word — record it as encountered so it's tracked, but don't
        # consume an intro slot today. It can be promoted on a future day.
        ulk = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state="encountered",
            source=source,
            fsrs_card_json=None,
            times_seen=0,
            times_correct=0,
            total_encounters=0,
        )
        db.add(ulk)
        db.flush()
        return ulk

    if ulk:
        # Transition existing record (e.g. from "encountered")
        ulk.knowledge_state = "acquiring"
        ulk.acquisition_box = 1
        ulk.acquisition_next_due = next_due
        ulk.acquisition_started_at = now
        ulk.acquisition_episode_kind = episode_kind
        ulk.entered_acquiring_at = now
        ulk.introduced_at = now
        # Update source: collateral never overrides (weakest mechanism);
        # book/story always win; otherwise keep the more specific existing source.
        _OVERRIDABLE_SOURCES = {None, "study", "encountered", "auto_intro", "collateral", "leech_reintro"}
        _HIGH_PRIORITY_SOURCES = {"book", "story_import", "textbook_scan", "duolingo", "frequency_core"}
        if (
            episode_kind != ACQUISITION_EPISODE_LEECH_REINTRO
            and source != "collateral"
            and (not ulk.source or ulk.source in _OVERRIDABLE_SOURCES or source in _HIGH_PRIORITY_SOURCES)
        ):
            ulk.source = source
        ulk.fsrs_card_json = None  # No FSRS card during acquisition
    else:
        ulk = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=next_due,
            acquisition_started_at=now,
            acquisition_episode_kind=episode_kind,
            entered_acquiring_at=now,
            introduced_at=now,
            source=(
                "study"
                if source == ACQUISITION_EPISODE_LEECH_REINTRO
                else source
            ),
            fsrs_card_json=None,
            times_seen=0,
            times_correct=0,
            total_encounters=0,
        )
        db.add(ulk)

    # Intro card for all new words (A/B experiment concluded: card-first wins)
    if ulk.experiment_group is None:
        ulk.experiment_group = "intro_ab_card"

    db.flush()

    # Trigger root/pattern enrichment if this root or pattern now qualifies
    lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
    if lemma:
        if lemma.root_id:
            from app.services.root_enrichment import maybe_enrich_root
            maybe_enrich_root(lemma.root_id, db)
        if lemma.wazn:
            from app.services.pattern_enrichment import maybe_enrich_pattern
            maybe_enrich_pattern(lemma.wazn, db)

    return ulk


def submit_acquisition_review(
    db: Session,
    lemma_id: int,
    rating_int: int,
    response_ms: Optional[int] = None,
    session_id: Optional[str] = None,
    review_mode: str = "reading",
    comprehension_signal: Optional[str] = None,
    client_review_id: Optional[str] = None,
    commit: bool = True,
    was_confused: bool = False,
) -> dict:
    """Submit a review for a word in the acquisition phase.

    Rating >= 3: advance box (1→2→3), graduate from box 3 if criteria met
    Rating == 2: stay in same box, reset interval
    Rating == 1: reset to box 1

    Returns dict with new state info.
    """
    if client_review_id:
        existing = (
            db.query(ReviewLog)
            .filter(ReviewLog.client_review_id == client_review_id)
            .first()
        )
        if existing:
            ulk = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == lemma_id)
                .first()
            )
            return {
                "lemma_id": lemma_id,
                "new_state": ulk.knowledge_state if ulk else "acquiring",
                "acquisition_box": ulk.acquisition_box if ulk else None,
                "next_due": ulk.acquisition_next_due.isoformat() if ulk and ulk.acquisition_next_due else "",
                "duplicate": True,
            }

    now = datetime.now(timezone.utc)

    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if not ulk or ulk.knowledge_state != "acquiring":
        logger.warning(f"submit_acquisition_review called for non-acquiring lemma {lemma_id}")
        # Fall back to normal FSRS review
        from app.services.fsrs_service import submit_review
        return submit_review(
            db, lemma_id=lemma_id, rating_int=rating_int,
            response_ms=response_ms, session_id=session_id,
            review_mode=review_mode, comprehension_signal=comprehension_signal,
            client_review_id=client_review_id,
            commit=commit,
        )

    old_box = ulk.acquisition_box or 1
    old_times_seen = ulk.times_seen or 0
    old_times_correct = ulk.times_correct or 0
    old_knowledge_state = ulk.knowledge_state
    old_last_reviewed = ulk.last_reviewed  # captured before the update below (Tier E elapsed)
    recent_intro = _intro_shown_recently(ulk, now)

    # Update review counts
    ulk.times_seen = old_times_seen + 1
    if rating_int >= 3:
        ulk.times_correct = old_times_correct + 1
    ulk.last_reviewed = now
    ulk.total_encounters = (ulk.total_encounters or 0) + 1

    # Determine if word is actually due (for gating box 2+ advancement)
    is_due = True
    if ulk.acquisition_next_due:
        acq_due = ulk.acquisition_next_due
        if acq_due.tzinfo is None:
            acq_due = acq_due.replace(tzinfo=timezone.utc)
        is_due = acq_due <= now

    # Fast-track: first correct review → graduate immediately to FSRS.
    # Skip when the intro card was shown within FAST_GRAD_INTRO_GAP — a
    # correct rating seconds after seeing the card is working memory, not
    # learning, and bypassing acquisition robs the encoding phase.
    graduated = False
    grad_reason: Optional[str] = None
    if old_times_seen == 0 and rating_int >= 3:
        if not recent_intro:
            graduated = True
            grad_reason = "first_correct"

    # Box advancement logic
    # Box 1→2: allowed for encoding unless this is intro-card working memory
    # Box 2→3 and graduation: only when due (enforce inter-session spacing)
    if not graduated and rating_int >= 3:
        if old_box == 1:
            if recent_intro:
                # Still inside the intro-card working-memory window. Count the
                # correct exposure, but keep the word in encoding instead of
                # promoting it to next-day consolidation.
                ulk.acquisition_box = 1
                ulk.acquisition_next_due = now + FAST_INTRO_RETRY_INTERVAL
            else:
                # Box 1→2: advance once recognition is not just immediate
                # recall of the intro card.
                ulk.acquisition_box = 2
                ulk.acquisition_next_due = now + BOX_INTERVALS[2]
        elif old_box == 2 and is_due:
            # Box 2→3: only when due (1-day interval honored)
            ulk.acquisition_box = 3
            ulk.acquisition_next_due = now + BOX_INTERVALS[3]
        elif old_box >= 3 and is_due:
            # Box 3: stay, reschedule (graduation checked below)
            ulk.acquisition_box = 3
            ulk.acquisition_next_due = now + BOX_INTERVALS[3]
        else:
            # Not due yet — record the review but don't advance box or reset timer
            # This gives within-session exposure credit without bypassing spacing
            pass
    elif rating_int == 2:
        # Hard: stay in same box
        if is_due:
            if (ulk.times_correct or 0) == 0:
                ulk.acquisition_next_due = now + timedelta(minutes=10)
            else:
                ulk.acquisition_next_due = now + BOX_INTERVALS[old_box]
        # If not due, don't reset the timer
        ulk.acquisition_box = old_box
    else:
        # Again: reset to box 1 (regardless of due status — failure resets)
        ulk.acquisition_box = 1
        if (ulk.times_correct or 0) == 0:
            ulk.acquisition_next_due = now + timedelta(minutes=5)
        else:
            ulk.acquisition_next_due = now + BOX_INTERVALS[1]
        # Generate/regenerate mnemonic on failure
        import threading
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        has_hooks = lemma and lemma.memory_hooks_json
        if not has_hooks:
            from app.services.memory_hooks import generate_memory_hooks
            threading.Thread(target=generate_memory_hooks, args=(lemma_id,), daemon=True).start()
            logger.info(f"Triggered mnemonic generation for failed acquiring lemma {lemma_id}")
        elif old_box >= 2:
            # Had hooks but still failed from box 2+ — regenerate with negative example
            from app.services.memory_hooks import regenerate_memory_hooks_premium
            threading.Thread(target=regenerate_memory_hooks_premium, args=(lemma_id,), daemon=True).start()
            logger.info(f"Triggered premium mnemonic regeneration for demoted lemma {lemma_id} (box {old_box}→1)")

    # Tiered graduation: more aggressive for high-accuracy words
    # Tier 1/2: no due-gating — collateral reviews (appearing in sentences for
    # other target words) are legitimate proof of knowledge. Previously all tiers
    # required is_due, which blocked graduation for words getting 80%+ accuracy
    # across many collateral exposures but only one "due" review per 3-day cycle.
    # Tier 3: still requires is_due to enforce inter-session spacing.
    if not graduated:
        new_times_seen = ulk.times_seen
        new_times_correct = ulk.times_correct
        accuracy = new_times_correct / new_times_seen if new_times_seen > 0 else 0

        # Elapsed since the previous review — the retention interval just proven.
        elapsed_since_last = None
        if old_last_reviewed is not None:
            olr = old_last_reviewed
            if olr.tzinfo is None:
                olr = olr.replace(tzinfo=timezone.utc)
            elapsed_since_last = now - olr

        # Tier E: Elapsed-interval graduation. A correct recognition after a long
        # real gap proves consolidation more strongly than the Leitner ramp itself,
        # so graduate from any box. Requires rating >= 3 (a *failed* review after a
        # long gap means it was forgotten — no graduation), unlike tiers 1-3 which
        # ignore the current rating. The intro working-memory gate is definitionally
        # satisfied by a multi-day gap; not_recent_intro kept for symmetry.
        if (not recent_intro and rating_int >= 3
                and elapsed_since_last is not None
                and elapsed_since_last >= ELAPSED_GRADUATION_MIN_INTERVAL):
            graduated = True
            grad_reason = "elapsed_interval"
        # Tier 1: Perfect accuracy, 3+ reviews → graduate from any box.
        # The intro-card gap blocks this too; otherwise three immediate
        # same-session correct answers could still graduate on working memory.
        elif not recent_intro and accuracy >= 1.0 and new_times_seen >= 3:
            graduated = True
            grad_reason = "perfect_accuracy"
        # Tier 2: High accuracy (≥80%), 4+ reviews → graduate from box ≥ 2
        elif not recent_intro and accuracy >= 0.80 and new_times_seen >= 4 and ulk.acquisition_box >= 2:
            graduated = True
            grad_reason = "high_accuracy"
        # Tier 3: Standard (existing criteria) — requires due for spacing
        elif is_due and (ulk.acquisition_box >= 3
              and new_times_seen >= GRADUATION_MIN_REVIEWS
              and accuracy >= GRADUATION_MIN_ACCURACY
              and _reviews_span_calendar_days(db, ulk.lemma_id, GRADUATION_MIN_CALENDAR_DAYS)):
            graduated = True
            grad_reason = "standard"

    if graduated:
        _graduate(ulk, now, db=db, reason=grad_reason)

    # Log review
    log_entry = ReviewLog(
        lemma_id=lemma_id,
        rating=rating_int,
        reviewed_at=now,
        response_ms=response_ms,
        session_id=session_id,
        review_mode=review_mode,
        comprehension_signal=comprehension_signal,
        client_review_id=client_review_id,
        is_acquisition=True,
        was_confused=was_confused,
        fsrs_log_json={
            "rating": rating_int,
            "state": ulk.knowledge_state,
            "acquisition_box_before": old_box,
            "acquisition_box_after": ulk.acquisition_box,
            "graduated": graduated,
            "graduation_reason": grad_reason,
            "pre_times_seen": old_times_seen,
            "pre_times_correct": old_times_correct,
            "pre_knowledge_state": old_knowledge_state,
            "intro_working_memory_blocked": recent_intro and rating_int >= 3 and old_box == 1,
        },
    )
    db.add(log_entry)
    if commit:
        db.commit()
    else:
        db.flush()

    next_due = ""
    if ulk.acquisition_next_due:
        next_due = ulk.acquisition_next_due.isoformat()
    elif ulk.fsrs_card_json:
        card_data = parse_json_column(ulk.fsrs_card_json)
        next_due = card_data.get("due", "")

    return {
        "lemma_id": lemma_id,
        "new_state": ulk.knowledge_state,
        "acquisition_box": ulk.acquisition_box,
        "graduated": graduated,
        "next_due": next_due,
    }


def _count_known_root_siblings(db: Session, lemma_id: int) -> int:
    """Count how many known words share the same root as the given lemma."""
    lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
    if not lemma or not lemma.root_id:
        return 0
    return (
        db.query(UserLemmaKnowledge.lemma_id)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.root_id == lemma.root_id,
            Lemma.lemma_id != lemma_id,
            UserLemmaKnowledge.knowledge_state == "known",
        )
        .count()
    )


def _graduate(
    ulk: UserLemmaKnowledge,
    now: datetime,
    db: Session | None = None,
    reason: Optional[str] = None,
) -> None:
    """Graduate a word from acquisition to FSRS."""
    from fsrs import Scheduler, Card, Rating

    ulk.knowledge_state = "learning"
    ulk.acquisition_box = None
    ulk.acquisition_next_due = None
    ulk.graduated_at = now

    rating = Rating.Good
    root_boost = False
    if db is not None:
        known_siblings = _count_known_root_siblings(db, ulk.lemma_id)
        if known_siblings >= ROOT_SIBLING_THRESHOLD:
            rating = Rating.Easy
            root_boost = True

    scheduler = Scheduler()
    card = Card()
    new_card, _ = scheduler.review_card(card, rating, now)
    ulk.fsrs_card_json = new_card.to_dict()

    log_interaction(
        event="word_graduated",
        lemma_id=ulk.lemma_id,
        times_seen=ulk.times_seen,
        times_correct=ulk.times_correct,
        root_boost=root_boost,
        graduation_reason=reason,
    )


def get_acquisition_due(
    db: Session,
    now: Optional[datetime] = None,
) -> list[int]:
    """Get lemma_ids of words due for acquisition review."""
    if now is None:
        now = datetime.now(timezone.utc)

    rows = (
        db.query(UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_box.isnot(None),
            UserLemmaKnowledge.acquisition_next_due <= now,
        )
        .all()
    )
    return [r[0] for r in rows]


def get_acquisition_stats(db: Session) -> dict:
    """Get summary stats about the acquisition pipeline."""
    acquiring = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state == "acquiring")
        .all()
    )

    box_counts = {1: 0, 2: 0, 3: 0}
    for ulk in acquiring:
        box = ulk.acquisition_box or 1
        if box in box_counts:
            box_counts[box] += 1

    now = datetime.now(timezone.utc)
    due_count = 0
    for ulk in acquiring:
        if ulk.acquisition_next_due:
            due_dt = ulk.acquisition_next_due
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            if due_dt <= now:
                due_count += 1

    return {
        "total_acquiring": len(acquiring),
        "box_1": box_counts[1],
        "box_2": box_counts[2],
        "box_3": box_counts[3],
        "due_now": due_count,
    }
