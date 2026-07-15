import json
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case, or_
from collections import Counter

from app.database import get_db
from app.models import (
    Lemma, UserLemmaKnowledge, ReviewLog, Root,
    SentenceReviewLog, SentenceWord, FrequencyCoreEntry,
)
from app.schemas import (
    StatsOut, DailyStatsPoint, LearningPaceOut,
    CEFREstimate, AnalyticsOut, GraduatedWord, IntroducedBySource,
    DeepAnalyticsOut, StabilityBucket, RetentionStats,
    ColdRecallBand, ColdRecallBreakdown,
    StateTransitions, ComprehensionBreakdown, StrugglingWord,
    RootCoverage, SessionDetail,
    AcquisitionWord, RecentGraduation, AcquisitionPipeline,
    InsightsOut,
    TextbookBenchmark, QuranProgress, ProgressBenchmarks,
    DailyGoalOut, FrequencyCoreBand, FrequencyCoreGap, FrequencyCoreProgress,
    RecoveryStatusOut,
)
from app.services.frequency_lanes import (
    due_lane_snapshot,
    frequency_core_ranks,
    is_main_lane_word,
)
from app.services.acquisition_service import (
    true_new_acquisition_episode_filter,
    recovery_status,
)

router = APIRouter(prefix="/api/stats", tags=["stats"])

DAILY_NEW_WORD_TARGET = 30

PRIMARY_COLD_RECALL_BANDS = (
    ("<1d", 0.0, 1.0),
    ("1-3d", 1.0, 3.0),
    ("3-7d", 3.0, 7.0),
    ("7-14d", 7.0, 14.0),
    ("14-30d", 14.0, 30.0),
    ("30d+", 30.0, None),
)

# Cached function word ID set — computed once, then reused
_func_word_ids_cache: set[int] | None = None

def _get_func_word_ids(db: Session) -> set[int]:
    global _func_word_ids_cache
    if _func_word_ids_cache is None:
        from app.services.sentence_validator import _is_function_word
        _func_word_ids_cache = {
            row.lemma_id for row in db.query(Lemma.lemma_id, Lemma.lemma_ar_bare).all()
            if row.lemma_ar_bare and _is_function_word(row.lemma_ar_bare)
        }
    return _func_word_ids_cache

# CEFR reading thresholds based on KELLY project + frequency research.
# These are for *reading comprehension* (receptive vocabulary), which is
# significantly larger than productive vocabulary at each level.
CEFR_THRESHOLDS = [
    ("A1", 300),
    ("A1+", 500),
    ("A2", 800),
    ("A2+", 1200),
    ("B1", 2000),
    ("B1+", 3000),
    ("B2", 4500),
    ("B2+", 6000),
    ("C1", 8000),
    ("C1+", 10000),
    ("C2", 15000),
]


def _count_state(db: Session, state: str) -> int:
    return (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(UserLemmaKnowledge.knowledge_state == state)
        .scalar() or 0
    )


def _stats_json_dict(value: object) -> dict:
    """Return a JSON object from SQLAlchemy JSON values or legacy JSON strings."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_stats_datetime(value: object) -> datetime | None:
    """Parse stored SQLite/JSON datetimes and normalize them to UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _count_due_cards(db: Session, now: datetime) -> tuple[int, int, int]:
    """Return (total_due, fsrs_due, acquisition_due), excluding function words."""
    func_word_ids = _get_func_word_ids(db)

    now_str = now.isoformat()
    fsrs_due_q = (
        db.query(UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.fsrs_card_json.isnot(None),
            func.json_extract(UserLemmaKnowledge.fsrs_card_json, '$.due') <= now_str,
        )
        .all()
    )
    fsrs_due = sum(1 for (lid,) in fsrs_due_q if lid not in func_word_ids)

    acq_due_q = (
        db.query(UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_next_due.isnot(None),
            UserLemmaKnowledge.acquisition_next_due <= now,
        )
        .all()
    )
    acquiring_due = sum(1 for (lid,) in acq_due_q if lid not in func_word_ids)

    return fsrs_due + acquiring_due, fsrs_due, acquiring_due


def _count_fsrs_cleared_today(db: Session, today_start: datetime, now: datetime) -> int:
    """Count distinct FSRS words that were due when reviewed today.

    The current card's due date cannot prove that the review was due: any early
    collateral review also moves that date into the future. The pre-review card
    snapshot is the authoritative scheduling state. Rows without a valid
    ``pre_card.due`` timestamp are excluded rather than guessed.
    """
    rows = (
        db.query(
            ReviewLog.lemma_id,
            ReviewLog.reviewed_at,
            ReviewLog.fsrs_log_json,
        )
        .filter(
            ReviewLog.reviewed_at >= today_start,
            ReviewLog.reviewed_at <= now,
            or_(
                ReviewLog.is_acquisition.is_(False),
                ReviewLog.is_acquisition.is_(None),
            ),
        )
        .all()
    )
    cleared_ids: set[int] = set()
    for row in rows:
        payload = _stats_json_dict(row.fsrs_log_json)
        pre_card = payload.get("pre_card")
        if not isinstance(pre_card, dict):
            continue
        due_at = _parse_stats_datetime(pre_card.get("due"))
        reviewed_at = _parse_stats_datetime(row.reviewed_at)
        if due_at is not None and reviewed_at is not None and due_at <= reviewed_at:
            cleared_ids.add(row.lemma_id)
    return len(cleared_ids)


def _count_acquisition_reviewed_today(db: Session, today_start: datetime) -> int:
    """Count distinct acquisition words reviewed today."""
    return (
        db.query(func.count(func.distinct(ReviewLog.lemma_id)))
        .filter(
            ReviewLog.reviewed_at >= today_start,
            ReviewLog.is_acquisition == True,  # noqa: E712
        )
        .scalar() or 0
    )


def _count_introduced_today(db: Session, today_start: datetime) -> int:
    """Count true-new acquisition episodes started today."""
    return (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(
            UserLemmaKnowledge.acquisition_started_at >= today_start,
            true_new_acquisition_episode_filter(),
        )
        .scalar() or 0
    )


def _count_reviewed_today_by_lane(db: Session, today_start: datetime) -> tuple[int, int]:
    reviewed_ids = [
        lid for (lid,) in (
            db.query(ReviewLog.lemma_id)
            .filter(ReviewLog.reviewed_at >= today_start)
            .distinct()
            .all()
        )
    ]
    if not reviewed_ids:
        return 0, 0
    rows = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.lemma_id.in_(reviewed_ids))
        .all()
    )
    if not rows:
        return 0, 0
    core_ranks = frequency_core_ranks(db, reviewed_ids)
    main = 0
    slow = 0
    for ulk, lemma in rows:
        if is_main_lane_word(ulk, lemma, core_ranks.get(ulk.lemma_id)):
            main += 1
        else:
            slow += 1
    return main, slow


def _get_daily_goal(db: Session, new_word_target: int | None = None) -> DailyGoalOut:
    """Progress toward the daily aggressive-learning target.

    Maintenance is intentionally computed as completed review work plus current
    due debt. The target can grow during the day when new acquisition cards
    become due, which makes the countdown conservative instead of pretending
    the morning queue was the whole day.

    The new-word target is the *effective* intro budget from the recovery gate
    (0/8/30), not the static cap: during post-hiatus recovery, intake is
    intentionally gated to ~zero, and a fixed 30/day target would pin the
    headline at 0% on days the learner is doing exactly the right thing.
    It can also grow during the day as reading volume earns budget back.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if new_word_target is None:
        from app.services.acquisition_service import _recovery_mode_intro_budget

        new_word_target = _recovery_mode_intro_budget(db, now, today_start)
    new_word_target = min(new_word_target, DAILY_NEW_WORD_TARGET)
    intake_gated = new_word_target < DAILY_NEW_WORD_TARGET

    introduced_today = _count_introduced_today(db, today_start)
    lanes = due_lane_snapshot(db, now)
    fsrs_due = len(lanes.fsrs_due_ids)
    acquisition_due = len(lanes.acquisition_due_ids)
    fsrs_reviewed_today = _count_fsrs_cleared_today(db, today_start, now)
    acquisition_reviewed_today = _count_acquisition_reviewed_today(db, today_start)
    main_reviewed_today, slow_reviewed_today = _count_reviewed_today_by_lane(db, today_start)

    maintenance_done = fsrs_reviewed_today + acquisition_reviewed_today
    main_maintenance_done = main_reviewed_today
    main_maintenance_remaining = len(lanes.main_due_ids)
    main_maintenance_target = main_maintenance_done + main_maintenance_remaining
    slow_lane_total_available = slow_reviewed_today + len(lanes.slow_due_ids)
    slow_lane_budget = min(
        slow_lane_total_available,
        max(1, round((main_maintenance_target + slow_lane_total_available) * 0.10)),
    ) if slow_lane_total_available else 0
    slow_lane_remaining = max(0, slow_lane_budget - slow_reviewed_today)
    maintenance_remaining = main_maintenance_remaining + slow_lane_remaining
    maintenance_target = maintenance_done + maintenance_remaining

    # With a zero effective budget there is nothing to introduce, so the
    # new-word goal is trivially met and the headline reflects maintenance.
    new_words_pct = (
        100.0
        if new_word_target == 0
        else min(100.0, introduced_today / new_word_target * 100.0)
    )
    maintenance_pct = (
        100.0
        if maintenance_target == 0
        else min(100.0, maintenance_done / maintenance_target * 100.0)
    )
    overall_pct = min(new_words_pct, maintenance_pct)

    return DailyGoalOut(
        new_words_target=new_word_target,
        introduced_today=introduced_today,
        introduced_remaining=max(0, new_word_target - introduced_today),
        new_words_pct=round(new_words_pct, 1),
        maintenance_done=maintenance_done,
        maintenance_remaining=maintenance_remaining,
        maintenance_target=maintenance_target,
        maintenance_pct=round(maintenance_pct, 1),
        overall_pct=round(overall_pct, 1),
        is_complete=(
            introduced_today >= new_word_target
            and main_maintenance_remaining == 0
            and slow_lane_remaining == 0
        ),
        intake_gated=intake_gated,
        fsrs_reviewed_today=fsrs_reviewed_today,
        acquisition_reviewed_today=acquisition_reviewed_today,
        fsrs_due=fsrs_due,
        acquisition_due=acquisition_due,
        main_maintenance_done=main_maintenance_done,
        main_maintenance_remaining=main_maintenance_remaining,
        main_maintenance_target=main_maintenance_target,
        slow_lane_done=slow_reviewed_today,
        slow_lane_budget=slow_lane_budget,
        slow_lane_remaining=slow_lane_remaining,
    )


def _get_basic_stats(db: Session) -> StatsOut:
    now = datetime.now(timezone.utc)
    total = db.query(func.count(Lemma.lemma_id)).scalar() or 0
    known = _count_state(db, "known")
    learning = _count_state(db, "learning")
    new_count = _count_state(db, "new")
    lapsed = _count_state(db, "lapsed")

    today_start = now.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    reviews_today = (
        db.query(func.count(ReviewLog.id))
        .filter(ReviewLog.reviewed_at >= today_start)
        .scalar() or 0
    )
    total_reviews = db.query(func.count(ReviewLog.id)).scalar() or 0
    fsrs_reviewed_today = _count_fsrs_cleared_today(db, today_start, now)

    due_today, fsrs_due, acquisition_due = _count_due_cards(db, now)

    acquiring = _count_state(db, "acquiring")
    encountered = _count_state(db, "encountered")

    return StatsOut(
        total_words=total,
        known=known,
        learning=learning,
        new=new_count,
        due_today=due_today,
        fsrs_due=fsrs_due,
        acquisition_due=acquisition_due,
        fsrs_reviewed_today=fsrs_reviewed_today,
        reviews_today=reviews_today,
        total_reviews=total_reviews,
        lapsed=lapsed,
        acquiring=acquiring,
        encountered=encountered,
    )



def _get_first_known_dates(db: Session) -> dict[int, datetime.date]:
    """Return first date each lemma reached state='known' in review logs."""
    rows = (
        db.query(
            ReviewLog.lemma_id,
            func.min(ReviewLog.reviewed_at).label("first_known_at"),
        )
        .filter(
            ReviewLog.fsrs_log_json.isnot(None),
            func.json_extract(ReviewLog.fsrs_log_json, '$.state') == 'known',
        )
        .group_by(ReviewLog.lemma_id)
        .all()
    )
    return {
        lemma_id: first_known_at.date()
        for lemma_id, first_known_at in rows
        if first_known_at is not None
    }


def _count_known_without_transition(
    db: Session,
    transitioned_known_ids: set[int],
) -> int:
    """Count currently-known lemmas that have no logged known-transition date."""
    rows = (
        db.query(UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.knowledge_state == "known")
        .all()
    )
    known_ids = {lemma_id for (lemma_id,) in rows}
    return len(known_ids - transitioned_known_ids)


def _get_daily_history(
    db: Session,
    days: int = 90,
    first_known_dates: dict[int, datetime.date] | None = None,
) -> list[DailyStatsPoint]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_date = cutoff.date()

    if first_known_dates is None:
        first_known_dates = _get_first_known_dates(db)

    rows = (
        db.query(
            func.date(ReviewLog.reviewed_at).label("day"),
            func.count(ReviewLog.id).label("reviews"),
            func.sum(
                case((ReviewLog.rating >= 3, 1), else_=0)
            ).label("correct"),
        )
        .filter(ReviewLog.reviewed_at >= cutoff)
        .group_by(func.date(ReviewLog.reviewed_at))
        .order_by(func.date(ReviewLog.reviewed_at))
        .all()
    )

    known_before = sum(1 for d in first_known_dates.values() if d < cutoff_date)
    known_before += _count_known_without_transition(db, set(first_known_dates.keys()))
    known_map = Counter(
        d.isoformat() for d in first_known_dates.values() if d >= cutoff_date
    )

    cumulative = known_before
    points = []
    for r in rows:
        day_str = str(r.day)
        day_learned = known_map.get(day_str, 0)
        cumulative += day_learned
        accuracy = (r.correct / r.reviews * 100) if r.reviews > 0 else None
        points.append(DailyStatsPoint(
            date=day_str,
            reviews=r.reviews,
            words_learned=day_learned,
            cumulative_known=cumulative,
            accuracy=round(accuracy, 1) if accuracy is not None else None,
        ))

    return points


def _get_due_backlog_history(days: int = 14) -> dict[str, int]:
    """Max total_due_words per day, from session_start interaction events.

    The burndown can't be reconstructed from ReviewLog (reviews done is not
    due remaining); session_start snapshots the real queue at build time. The
    per-day max approximates the start-of-day peak, before that day's reviews
    cleared any of it. Days without a session have no data point.
    """
    from app.config import settings

    out: dict[str, int] = {}
    today = datetime.now(timezone.utc).date()
    for offset in range(days):
        day = today - timedelta(days=offset)
        path = settings.log_dir / f"interactions_{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        peak: int | None = None
        try:
            with open(path) as fh:
                for line in fh:
                    if '"session_start"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("event") != "session_start":
                        continue
                    value = event.get("total_due_words")
                    if isinstance(value, int):
                        peak = value if peak is None else max(peak, value)
        except OSError:
            continue
        if peak is not None:
            out[day.isoformat()] = peak
    return out


def _get_study_dates(db: Session) -> list[str]:
    rows = (
        db.query(func.date(ReviewLog.reviewed_at).label("day"))
        .group_by(func.date(ReviewLog.reviewed_at))
        .order_by(func.date(ReviewLog.reviewed_at))
        .all()
    )
    return [str(r.day) for r in rows]


def _calculate_streak(study_dates: list[str]) -> tuple[int, int]:
    if not study_dates:
        return 0, 0

    today = datetime.now(timezone.utc).date()
    dates = sorted(set(study_dates))

    # Current streak
    current = 0
    check = today
    for d in reversed(dates):
        d_date = datetime.strptime(d, "%Y-%m-%d").date() if isinstance(d, str) else d
        if d_date == check:
            current += 1
            check -= timedelta(days=1)
        elif d_date < check:
            break

    # Longest streak
    longest = 1 if dates else 0
    streak = 1
    for i in range(1, len(dates)):
        prev = datetime.strptime(dates[i - 1], "%Y-%m-%d").date() if isinstance(dates[i - 1], str) else dates[i - 1]
        curr = datetime.strptime(dates[i], "%Y-%m-%d").date() if isinstance(dates[i], str) else dates[i]
        if (curr - prev).days == 1:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 1

    return current, longest


def _get_pace(
    db: Session,
    first_known_dates: dict[int, datetime.date] | None = None,
) -> LearningPaceOut:
    now = datetime.now(timezone.utc)
    study_dates = _get_study_dates(db)
    current_streak, longest_streak = _calculate_streak(study_dates)
    if first_known_dates is None:
        first_known_dates = _get_first_known_dates(db)

    def _study_days_in_window(days: int) -> int:
        cutoff = (now - timedelta(days=days)).date()
        return sum(1 for d in study_dates
                   if (datetime.strptime(d, "%Y-%m-%d").date() if isinstance(d, str) else d) >= cutoff)

    def words_learned_in(days: int) -> float:
        cutoff_date = (now - timedelta(days=days)).date()
        count = sum(1 for d in first_known_dates.values() if d >= cutoff_date)
        actual_days = _study_days_in_window(days)
        return round(count / max(actual_days, 1), 1)

    def reviews_in(days: int) -> float:
        cutoff = now - timedelta(days=days)
        count = (
            db.query(func.count(ReviewLog.id))
            .filter(ReviewLog.reviewed_at >= cutoff)
            .scalar() or 0
        )
        actual_days = _study_days_in_window(days)
        return round(count / max(actual_days, 1), 1)

    # Accuracy over last 7 days
    cutoff_7d = now - timedelta(days=7)
    acc_row = (
        db.query(
            func.count(ReviewLog.id).label("total"),
            func.sum(case((ReviewLog.rating >= 3, 1), else_=0)).label("correct"),
        )
        .filter(ReviewLog.reviewed_at >= cutoff_7d)
        .first()
    )
    accuracy_7d = None
    if acc_row and acc_row.total and acc_row.total > 0:
        accuracy_7d = round(acc_row.correct / acc_row.total * 100, 1)

    study_days_7d = _study_days_in_window(7)

    return LearningPaceOut(
        words_per_day_7d=words_learned_in(7),
        words_per_day_30d=words_learned_in(30),
        reviews_per_day_7d=reviews_in(7),
        reviews_per_day_30d=reviews_in(30),
        total_study_days=len(study_dates),
        current_streak=current_streak,
        longest_streak=longest_streak,
        accuracy_7d=accuracy_7d,
        study_days_7d=study_days_7d,
    )


def _estimate_cefr(known_count: int, acquiring_known: int = 0) -> CEFREstimate:
    level = "Pre-A1"
    sublevel = "Pre-A1"

    for cefr_level, threshold in CEFR_THRESHOLDS:
        if known_count >= threshold:
            level = cefr_level
            sublevel = cefr_level
        else:
            break

    # Find next level
    next_level = None
    words_to_next = None
    for i, (cefr_level, threshold) in enumerate(CEFR_THRESHOLDS):
        if threshold > known_count:
            next_level = cefr_level
            words_to_next = threshold - known_count
            break

    # Estimate reading coverage: at ~2000 words you can read ~80% of common
    # MSA text, at 5000 ~90%, at 10000 ~95%. Zipf's law approximation.
    if known_count == 0:
        coverage = 0.0
    elif known_count < 500:
        coverage = known_count / 500 * 50.0
    elif known_count < 2000:
        coverage = 50.0 + (known_count - 500) / 1500 * 30.0
    elif known_count < 5000:
        coverage = 80.0 + (known_count - 2000) / 3000 * 10.0
    elif known_count < 10000:
        coverage = 90.0 + (known_count - 5000) / 5000 * 5.0
    else:
        coverage = min(95.0 + (known_count - 10000) / 10000 * 4.0, 99.5)

    return CEFREstimate(
        level=level.rstrip("+"),
        sublevel=sublevel,
        known_words=known_count,
        acquiring_known=acquiring_known,
        next_level=next_level,
        words_to_next=words_to_next,
        reading_coverage_pct=round(coverage, 1),
    )


def _add_cefr_predictions(
    cefr: CEFREstimate,
    pace: LearningPaceOut,
    graduated_24h_count: int,
) -> CEFREstimate:
    if cefr.words_to_next is None or cefr.words_to_next <= 0:
        return cefr

    if pace.words_per_day_7d > 0 and pace.study_days_7d > 0:
        study_frequency = pace.study_days_7d / 7.0
        effective_daily_rate = pace.words_per_day_7d * study_frequency
        if effective_daily_rate > 0:
            cefr.days_to_next_weekly_pace = round(
                cefr.words_to_next / effective_daily_rate
            )

    if graduated_24h_count > 0:
        cefr.days_to_next_today_pace = round(
            cefr.words_to_next / graduated_24h_count
        )

    return cefr


def _compute_benchmarks(db: Session) -> ProgressBenchmarks:
    """Compute progress benchmarks: textbook coverage + Quran progress."""
    import re
    from pathlib import Path
    from app.models import QuranicVerse, QuranicVerseWord
    from app.services.sentence_validator import (
        FUNCTION_WORD_GLOSSES,
        build_lemma_lookup,
        normalize_alef,
        strip_diacritics,
        strip_punctuation,
        strip_tatweel,
        tokenize,
    )

    # Build a known-form lookup using the same form/conjugation expansion used by
    # sentence validation. The old benchmark only matched exact lemma_ar_bare
    # strings, which undercounted textbook entries that list conjugations,
    # plurals, masculine/feminine pairs, or short phrases.
    known_ulks = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.knowledge_state.in_(["known", "learning", "lapsed"])
    ).all()
    known_ids = {u.lemma_id for u in known_ulks}
    known_lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(known_ids)).all() if known_ids else []
    known_lookup = build_lemma_lookup(known_lemmas) if known_lemmas else {}
    known_bare = set(known_lookup.keys())

    total_roots = db.query(func.count(Root.root_id)).scalar() or 0
    known_root_ids = {l.root_id for l in known_lemmas if l.root_id}

    def normalize_tb(text):
        text = strip_diacritics(text)
        text = normalize_alef(text)
        text = strip_tatweel(text)
        text = re.sub(r'[؟؛«».,!?;:\[\]{}]', ' ', text)
        return text.strip()

    def candidate_variants(bare: str) -> set[str]:
        bare = normalize_tb(strip_punctuation(bare))
        if not bare:
            return set()
        variants = {bare}
        if bare.startswith("ال") and len(bare) > 2:
            variants.add(bare[2:])
        elif len(bare) >= 3:
            variants.add("ال" + bare)
        return variants

    function_bares = {normalize_tb(k) for k in FUNCTION_WORD_GLOSSES}

    def token_known(token: str) -> bool:
        variants = candidate_variants(token)
        return bool(variants & known_bare) or any(v in function_bares for v in variants)

    def textbook_entry_known(entry: str, gloss: str) -> bool:
        normalized = normalize_tb(entry)
        if not normalized:
            return False

        # Parentheses and slashes usually encode alternate forms: مصري/مصرية,
        # مازال (مازلت). Treat those alternatives independently.
        alternate_text = re.sub(r'[()/]', ' ، ', normalized)
        alternates = [
            a.strip()
            for a in re.split(r'[،,;]+', alternate_text)
            if a.strip()
        ]

        # Al-Kitaab often lists principal parts as whitespace-separated tokens
        # with no comma: past present masdar. For "to ..." glosses, any known
        # principal part is enough to count the vocabulary row as covered.
        if len(alternates) == 1:
            tokens = tokenize(alternates[0])
            if len(tokens) > 1 and (gloss or "").lower().strip().startswith("to "):
                alternates.extend(tokens)

        for alt in alternates:
            if any(v in known_bare for v in candidate_variants(alt)):
                return True
            tokens = [
                t for t in tokenize(alt)
                if normalize_tb(t) not in {"ج", "ج.", "المصدر", "ان"} and "+" not in t
            ]
            if tokens and all(token_known(t) for t in tokens):
                return True
            if any(token_known(t) for t in tokens) and len(tokens) >= 2 and (gloss or "").lower().strip().startswith("to "):
                return True

        return False

    # Textbook benchmarks
    benchmarks_dir = Path(__file__).resolve().parent.parent.parent / "data" / "benchmarks"
    textbooks = []

    tb_files = [
        ("alkitaab_part1.tsv", "Al-Kitaab Part 1", "~1st year university Arabic (Georgetown)"),
        ("madinah_book1.tsv", "Madinah Book 1", "Islamic studies beginner Arabic"),
    ]
    for filename, name, desc in tb_files:
        filepath = benchmarks_dir / filename
        if not filepath.exists():
            continue
        total = 0
        matched = 0
        with open(filepath, "r") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) < 2:
                    continue
                bare = normalize_tb(parts[0])
                if not bare:
                    continue
                total += 1
                if textbook_entry_known(parts[0], parts[1]):
                    matched += 1
        if total > 0:
            textbooks.append(TextbookBenchmark(
                name=name,
                total_words=total,
                known_count=matched,
                coverage_pct=round(matched / total * 100, 1),
                description=desc,
            ))

    # Quran progress
    quran = None
    total_verses = db.query(func.count(QuranicVerse.id)).scalar() or 0
    studied = db.query(QuranicVerse).filter(QuranicVerse.srs_level >= 1).count()
    graduated = db.query(QuranicVerse).filter(QuranicVerse.srs_level >= 8).count()

    # Current position (highest surah:ayah with srs_level > 0)
    latest = db.query(QuranicVerse).filter(
        QuranicVerse.srs_level >= 1
    ).order_by(QuranicVerse.surah.desc(), QuranicVerse.ayah.desc()).first()

    # Verse mastery: count verses rated "got_it" at least twice
    mastered = db.query(QuranicVerse).filter(
        QuranicVerse.last_rating == "got_it",
        QuranicVerse.times_reviewed >= 2,
    ).count()

    quran = QuranProgress(
        verses_studied=studied,
        verses_graduated=graduated,
        total_verses=total_verses,
        current_surah=f"{latest.surah_name_en} ({latest.surah_name_ar})" if latest else "",
        current_ayah=latest.ayah if latest else 0,
        unique_words_in_studied=mastered,  # reuse field for mastered count
        known_word_count=studied,
        word_coverage_pct=round(mastered / studied * 100, 1) if studied > 0 else 0.0,
    )

    return ProgressBenchmarks(
        total_words=len(known_ids),
        total_roots=len(known_root_ids),
        textbooks=textbooks,
        quran=quran,
    )


def _compute_frequency_core_progress(
    db: Session,
    *,
    rank_field: str = "core_rank",
    only_islamic: bool = False,
    band_thresholds: tuple[int, ...] = (100, 250, 500, 1000, 1500, 2000, 2500, 3000, 4000, 5000),
) -> FrequencyCoreProgress | None:
    """Compute continuous and banded coverage of a ranked frequency track.

    Defaults to the unified frequency core ranked by ``core_rank``. Pass
    ``rank_field="islamic_rank", only_islamic=True`` to compute the separate
    Quran-core track — the same rows, restricted to those carrying a Quran
    frequency rank and ordered by it (so #1 = the most frequent Quran word).
    """
    rank_col = getattr(FrequencyCoreEntry, rank_field)
    base_filter = [FrequencyCoreEntry.excluded_reason.is_(None)]
    if only_islamic:
        base_filter.append(FrequencyCoreEntry.islamic_rank.isnot(None))

    total_entries = (
        db.query(func.count(FrequencyCoreEntry.id))
        .filter(*base_filter)
        .scalar() or 0
    )
    if total_entries == 0:
        return None

    max_band = max(band_thresholds)
    rows = (
        db.query(
            rank_col.label("core_rank"),
            FrequencyCoreEntry.lemma_id,
            FrequencyCoreEntry.display_form,
            FrequencyCoreEntry.gloss_en,
            FrequencyCoreEntry.confidence_tier,
            FrequencyCoreEntry.gap_status,
            UserLemmaKnowledge.knowledge_state,
            UserLemmaKnowledge.introduced_at,
        )
        .outerjoin(
            UserLemmaKnowledge,
            UserLemmaKnowledge.lemma_id == FrequencyCoreEntry.lemma_id,
        )
        .filter(
            *base_filter,
            rank_col <= max_band,
        )
        .order_by(rank_col.asc())
        .all()
    )

    # Honesty pass (2026-06-03): the raw entries include function words (e.g. ال)
    # and variant/compound forms whose canonical is already known (e.g. اليوم →
    # يوم). Both used to surface as "missing"/"need intro" gaps, overstating the
    # remaining work. Drop function words entirely (frequency coverage is a
    # content-word metric) and resolve each entry to its canonical lemma so a
    # known canonical counts the variant as learned.
    from types import SimpleNamespace
    from app.services.sentence_validator import _is_function_word, strip_diacritics

    func_ids = _get_func_word_ids(db)
    entry_lemma_ids = {r.lemma_id for r in rows if r.lemma_id is not None}
    redirect: dict[int, int] = {}
    if entry_lemma_ids:
        for lid, canon in (
            db.query(Lemma.lemma_id, Lemma.canonical_lemma_id)
            .filter(Lemma.lemma_id.in_(entry_lemma_ids))
            .all()
        ):
            if canon:
                redirect[lid] = canon
    need_ids = entry_lemma_ids | set(redirect.values())
    ulk_state: dict[int, tuple] = {}
    if need_ids:
        for u in (
            db.query(
                UserLemmaKnowledge.lemma_id,
                UserLemmaKnowledge.knowledge_state,
                UserLemmaKnowledge.introduced_at,
            )
            .filter(UserLemmaKnowledge.lemma_id.in_(need_ids))
            .all()
        ):
            ulk_state[u.lemma_id] = (u.knowledge_state, u.introduced_at)

    def _is_func_row(r) -> bool:
        if r.lemma_id is not None:
            return redirect.get(r.lemma_id, r.lemma_id) in func_ids or r.lemma_id in func_ids
        return _is_function_word(strip_diacritics(r.display_form or ""))

    norm_rows = []
    for r in rows:
        if _is_func_row(r):
            continue
        canon = redirect.get(r.lemma_id, r.lemma_id) if r.lemma_id is not None else None
        state, introduced_at = ulk_state.get(canon, ulk_state.get(r.lemma_id, (None, None)))
        norm_rows.append(SimpleNamespace(
            core_rank=r.core_rank,
            lemma_id=r.lemma_id,
            display_form=r.display_form,
            gloss_en=r.gloss_en,
            confidence_tier=r.confidence_tier,
            gap_status=r.gap_status,
            knowledge_state=state,
            introduced_at=introduced_at,
        ))
    rows = norm_rows

    learned_states = {"known", "learning", "acquiring"}
    pipeline_states = learned_states | {"lapsed", "encountered"}
    introduced_states = learned_states | {"lapsed", "suspended"}

    learned_prefix_count = 0
    for r in rows:
        if r.knowledge_state in learned_states:
            learned_prefix_count = r.core_rank
            continue
        break

    bands: list[FrequencyCoreBand] = []
    # Denser at the frontier (2000-3000) so the active learning zone isn't hidden
    # by the old 2000->5000 jump; the frontend collapses completed leading tiers.
    for top_n in band_thresholds:
        band_rows = [r for r in rows if r.core_rank <= top_n]
        if not band_rows:
            continue
        state_counts: dict[str, int] = {}
        for r in band_rows:
            state = r.knowledge_state or ("unmapped" if r.lemma_id is None else "new")
            state_counts[state] = state_counts.get(state, 0) + 1
        learned_count = sum(1 for r in band_rows if r.knowledge_state in learned_states)
        pipeline_count = sum(1 for r in band_rows if r.knowledge_state in pipeline_states)
        introduced_count = sum(
            1
            for r in band_rows
            if r.lemma_id is not None
            and (r.introduced_at is not None or r.knowledge_state in introduced_states)
        )
        not_introduced_count = sum(
            1
            for r in band_rows
            if r.lemma_id is not None
            and r.introduced_at is None
            and r.knowledge_state not in introduced_states
        )
        high_confidence_count = sum(1 for r in band_rows if r.confidence_tier == "high")
        medium_confidence_count = sum(1 for r in band_rows if r.confidence_tier == "medium")
        low_confidence_count = sum(1 for r in band_rows if r.confidence_tier == "low")
        unmapped_count = sum(1 for r in band_rows if r.lemma_id is None)
        bands.append(FrequencyCoreBand(
            top_n=top_n,
            learned_count=learned_count,
            pipeline_count=pipeline_count,
            total_count=len(band_rows),
            coverage_pct=round(learned_count / len(band_rows) * 100, 1),
            introduced_count=introduced_count,
            not_introduced_count=not_introduced_count,
            high_confidence_count=high_confidence_count,
            medium_confidence_count=medium_confidence_count,
            low_confidence_count=low_confidence_count,
            unmapped_count=unmapped_count,
            state_counts=state_counts,
        ))

    next_gaps: list[FrequencyCoreGap] = []
    seen_gap_keys: set[str] = set()
    for r in rows:
        if r.introduced_at is not None or r.knowledge_state in introduced_states:
            continue
        gap_key = f"lemma:{r.lemma_id}" if r.lemma_id is not None else f"missing:{r.display_form}"
        if gap_key in seen_gap_keys:
            continue
        seen_gap_keys.add(gap_key)
        status = r.knowledge_state or ("missing_from_db" if r.lemma_id is None else "new")
        next_gaps.append(FrequencyCoreGap(
            core_rank=r.core_rank,
            lemma_id=r.lemma_id,
            display_form=r.display_form,
            gloss_en=r.gloss_en,
            status=status,
            confidence_tier=r.confidence_tier,
            gap_status=r.gap_status,
        ))
        if len(next_gaps) >= 12:
            break

    return FrequencyCoreProgress(
        total_entries=total_entries,
        learned_prefix_count=learned_prefix_count,
        bands=bands,
        next_gaps=next_gaps,
    )


def _get_words_reviewed_count(db: Session, days: int | None = None) -> int:
    """Sum of word counts across all reviewed sentences in the period."""
    word_counts = (
        db.query(
            SentenceWord.sentence_id,
            func.count(SentenceWord.id).label("wc"),
        )
        .group_by(SentenceWord.sentence_id)
        .subquery()
    )
    q = db.query(func.coalesce(func.sum(word_counts.c.wc), 0)).join(
        SentenceReviewLog,
        SentenceReviewLog.sentence_id == word_counts.c.sentence_id,
    )
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        q = q.filter(SentenceReviewLog.reviewed_at >= cutoff)
    return q.scalar() or 0


def _get_unique_words_recognized(db: Session, days_start: int, days_end: int) -> int:
    """Count distinct lemmas with rating >= 3 in the window [days_end ago, days_start ago)."""
    now = datetime.now(timezone.utc)
    cutoff_recent = now - timedelta(days=days_start)
    cutoff_old = now - timedelta(days=days_end)
    return (
        db.query(func.count(func.distinct(ReviewLog.lemma_id)))
        .filter(
            ReviewLog.reviewed_at >= cutoff_old,
            ReviewLog.reviewed_at < cutoff_recent,
            ReviewLog.rating >= 3,
        )
        .scalar() or 0
    )


@router.get("", response_model=StatsOut)
def get_stats(db: Session = Depends(get_db)):
    return _get_basic_stats(db)


@router.get("/analytics", response_model=AnalyticsOut)
def get_analytics(
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
):
    basic = _get_basic_stats(db)
    first_known_dates = _get_first_known_dates(db)
    pace = _get_pace(db, first_known_dates=first_known_dates)
    acquiring_recognized = (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.times_correct >= 1,
        )
        .scalar() or 0
    )
    cefr = _estimate_cefr(basic.known + basic.learning, acquiring_known=acquiring_recognized)
    history = _get_daily_history(db, days, first_known_dates=first_known_dates)

    comp_today = _get_comprehension_breakdown(db, 0)
    graduated_today = _get_graduated_today(db)
    introduced_today = _get_introduced_today(db)
    introduced_words_today = _get_introduced_words_today(db)
    calibration = _compute_calibration_signal(comp_today)

    graduated_24h = _get_graduated_count_24h(db)
    _add_cefr_predictions(cefr, pace, graduated_24h)

    words_reviewed_7d = _get_words_reviewed_count(db, days=7)
    words_reviewed_all = _get_words_reviewed_count(db)
    unique_recognized_7d = _get_unique_words_recognized(db, 0, 7)
    unique_recognized_prior_7d = _get_unique_words_recognized(db, 7, 14)

    benchmarks = _compute_benchmarks(db)
    recovery = recovery_status(db)
    daily_goal = _get_daily_goal(db, new_word_target=recovery["intro_budget_today"])

    # Backlog burndown overlay (bounded to a month of log files).
    backlog_history = _get_due_backlog_history(days=min(days, 30))
    for point in history:
        point.due_backlog = backlog_history.get(point.date)
    today_key = datetime.now(timezone.utc).date().isoformat()
    if history and history[-1].date == today_key and history[-1].due_backlog is None:
        # No session logged yet today — fall back to the live due count.
        history[-1].due_backlog = basic.due_today

    frequency_core = _compute_frequency_core_progress(db)
    # Separate Quran-core track: same rows that carry a Quran frequency rank,
    # ordered by Quran frequency (a primary learning goal — surfaced on its own
    # so progress isn't blended into the MSA core). Bands sized to the ~1.3k
    # mapped Quran lemmas rather than the 5k MSA range.
    quran_core = _compute_frequency_core_progress(
        db,
        rank_field="islamic_rank",
        only_islamic=True,
        band_thresholds=(50, 100, 250, 500, 750, 1000, 1500),
    )

    return AnalyticsOut(
        stats=basic,
        pace=pace,
        cefr=cefr,
        daily_history=history,
        comprehension_today=comp_today,
        graduated_today=graduated_today,
        introduced_today=introduced_today,
        introduced_words_today=introduced_words_today,
        calibration_signal=calibration,
        total_words_reviewed_7d=words_reviewed_7d,
        total_words_reviewed_alltime=words_reviewed_all,
        unique_words_recognized_7d=unique_recognized_7d,
        unique_words_recognized_prior_7d=unique_recognized_prior_7d,
        benchmarks=benchmarks,
        daily_goal=daily_goal,
        frequency_core=frequency_core,
        quran_core=quran_core,
        recovery=RecoveryStatusOut(**recovery),
    )


@router.get("/cefr", response_model=CEFREstimate)
def get_cefr(db: Session = Depends(get_db)):
    known = _count_state(db, "known") + _count_state(db, "learning")
    acq_known = (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.times_correct >= 1,
        )
        .scalar() or 0
    )
    return _estimate_cefr(known, acquiring_known=acq_known)


# --- Deep Analytics ---


STABILITY_BUCKETS = [
    ("<1h", 0.0, 1 / 24),
    ("1h-12h", 1 / 24, 0.5),
    ("12h-1d", 0.5, 1.0),
    ("1-3d", 1.0, 3.0),
    ("3-7d", 3.0, 7.0),
    ("7-30d", 7.0, 30.0),
    ("30d+", 30.0, None),
]


def _get_stability_distribution(db: Session) -> list[StabilityBucket]:
    rows = (
        db.query(UserLemmaKnowledge.fsrs_card_json)
        .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
        .all()
    )
    counts = {label: 0 for label, _, _ in STABILITY_BUCKETS}
    for (card_data,) in rows:
        if not card_data:
            continue
        if isinstance(card_data, str):
            card_data = json.loads(card_data)
        stability = card_data.get("stability")
        if stability is None:
            continue
        stability = float(stability)
        for label, lo, hi in STABILITY_BUCKETS:
            if hi is None:
                if stability >= lo:
                    counts[label] += 1
                    break
            elif lo <= stability < hi:
                counts[label] += 1
                break

    return [
        StabilityBucket(
            label=label, count=counts[label],
            min_days=lo, max_days=hi,
        )
        for label, lo, hi in STABILITY_BUCKETS
    ]


def _get_retention_stats(db: Session, days: int) -> RetentionStats:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    row = (
        db.query(
            func.count(ReviewLog.id).label("total"),
            func.sum(case((ReviewLog.rating >= 3, 1), else_=0)).label("correct"),
        )
        .filter(ReviewLog.reviewed_at >= cutoff)
        .first()
    )
    total = row.total or 0
    correct = row.correct or 0
    pct = round(correct / total * 100, 1) if total > 0 else None
    return RetentionStats(
        period_days=days,
        total_reviews=total,
        correct_reviews=correct,
        retention_pct=pct,
    )


def _get_primary_cold_recall(db: Session, days: int = 30) -> ColdRecallBreakdown:
    """Return primary FSRS recall grouped by the demonstrated review gap.

    This intentionally excludes acquisition and collateral rows. A valid
    ``pre_card.last_review`` snapshot is required so same-day reinforcement is
    visible separately from genuinely cold recall instead of being blended into
    one optimistic retention percentage.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(
            ReviewLog.rating,
            ReviewLog.reviewed_at,
            ReviewLog.fsrs_log_json,
        )
        .filter(
            ReviewLog.reviewed_at >= cutoff,
            ReviewLog.credit_type == "primary",
            or_(
                ReviewLog.is_acquisition.is_(False),
                ReviewLog.is_acquisition.is_(None),
            ),
        )
        .all()
    )

    counts = {
        label: {"total": 0, "correct": 0}
        for label, _, _ in PRIMARY_COLD_RECALL_BANDS
    }
    for row in rows:
        payload = _stats_json_dict(row.fsrs_log_json)
        pre_card = payload.get("pre_card")
        if not isinstance(pre_card, dict):
            continue
        last_review = _parse_stats_datetime(pre_card.get("last_review"))
        reviewed_at = _parse_stats_datetime(row.reviewed_at)
        if last_review is None or reviewed_at is None:
            continue
        gap_days = (reviewed_at - last_review).total_seconds() / 86400
        if gap_days < 0:
            continue
        for label, min_days, max_days in PRIMARY_COLD_RECALL_BANDS:
            if gap_days >= min_days and (max_days is None or gap_days < max_days):
                counts[label]["total"] += 1
                if row.rating >= 3:
                    counts[label]["correct"] += 1
                break

    bands = []
    for label, min_days, max_days in PRIMARY_COLD_RECALL_BANDS:
        total = counts[label]["total"]
        correct = counts[label]["correct"]
        bands.append(ColdRecallBand(
            label=label,
            min_gap_days=min_days,
            max_gap_days=max_days,
            total_reviews=total,
            correct_reviews=correct,
            retention_pct=round(correct / total * 100, 1) if total else None,
        ))
    total = sum(band.total_reviews for band in bands)
    correct = sum(band.correct_reviews for band in bands)
    return ColdRecallBreakdown(
        period_days=days,
        total_reviews=total,
        correct_reviews=correct,
        retention_pct=round(correct / total * 100, 1) if total else None,
        bands=bands,
    )


def _normalize_transition_state(value: object) -> str | None:
    """Normalize py-fsrs state names and persisted knowledge-state strings."""
    if value is None:
        return None
    if isinstance(value, int):
        return {0: "new", 1: "learning", 2: "known", 3: "lapsed"}.get(value)
    normalized = str(value).strip().lower()
    return {
        "new": "new",
        "learning": "learning",
        "review": "known",
        "known": "known",
        "relearning": "lapsed",
        "lapsed": "lapsed",
        "acquiring": "acquiring",
        "encountered": "encountered",
    }.get(normalized, normalized or None)


def _get_state_transitions(db: Session, days: int) -> StateTransitions:
    now = datetime.now(timezone.utc)
    if days == 0:
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        cutoff = now - timedelta(days=days)
    rows = (
        db.query(ReviewLog.fsrs_log_json)
        .filter(
            ReviewLog.reviewed_at >= cutoff,
            ReviewLog.fsrs_log_json.isnot(None),
        )
        .all()
    )

    transitions = StateTransitions(
        period="today" if days == 0 else f"{days}d",
    )

    for (log_json,) in rows:
        if not log_json:
            continue
        payload = _stats_json_dict(log_json)
        state = _normalize_transition_state(payload.get("state"))
        previous_state = _normalize_transition_state(
            payload.get("pre_knowledge_state") or payload.get("prev_state")
        )
        if not state or not previous_state:
            continue

        if previous_state in ("new", "encountered", "acquiring") and state == "learning":
            transitions.new_to_learning += 1
        elif previous_state in ("learning", "acquiring") and state == "known":
            transitions.learning_to_known += 1
        elif previous_state == "known" and state == "lapsed":
            transitions.known_to_lapsed += 1
        elif previous_state == "lapsed" and state in ("learning", "known"):
            transitions.lapsed_to_learning += 1

    return transitions


def _get_comprehension_breakdown(db: Session, days: int) -> ComprehensionBreakdown:
    now = datetime.now(timezone.utc)
    if days == 0:
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        cutoff = now - timedelta(days=days)
    rows = (
        db.query(
            SentenceReviewLog.comprehension,
            func.count(SentenceReviewLog.id),
        )
        .filter(SentenceReviewLog.reviewed_at >= cutoff)
        .group_by(SentenceReviewLog.comprehension)
        .all()
    )
    result = ComprehensionBreakdown(period_days=days)
    for signal, count in rows:
        if signal == "understood":
            result.understood = count
        elif signal == "partial":
            result.partial = count
        elif signal == "no_idea":
            result.no_idea = count
    result.total = result.understood + result.partial + result.no_idea
    return result


def _get_struggling_words(db: Session) -> list[StrugglingWord]:
    rows = (
        db.query(
            UserLemmaKnowledge.lemma_id,
            Lemma.lemma_ar,
            Lemma.gloss_en,
            UserLemmaKnowledge.times_seen,
            UserLemmaKnowledge.total_encounters,
        )
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.times_seen >= 3,
            UserLemmaKnowledge.times_correct == 0,
        )
        .order_by(UserLemmaKnowledge.times_seen.desc())
        .all()
    )
    return [
        StrugglingWord(
            lemma_id=r.lemma_id,
            lemma_ar=r.lemma_ar,
            gloss_en=r.gloss_en,
            times_seen=r.times_seen,
            total_encounters=r.total_encounters or 0,
        )
        for r in rows
    ]


def _get_root_coverage(db: Session) -> RootCoverage:
    rows = (
        db.query(
            Root.root_id,
            Root.root,
            Root.core_meaning_en,
            func.count(Lemma.lemma_id).label("total_lemmas"),
            func.sum(
                case(
                    (UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]), 1),
                    else_=0,
                )
            ).label("known_lemmas"),
        )
        .join(Lemma, Lemma.root_id == Root.root_id)
        .outerjoin(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(Lemma.canonical_lemma_id.is_(None))
        .group_by(Root.root_id)
        .all()
    )

    total_roots = 0
    roots_with_known = 0
    roots_fully_mastered = 0
    partial_roots = []

    for root_id, root_text, meaning, total, known in rows:
        if total == 0:
            continue
        total_roots += 1
        known = known or 0
        if known > 0:
            roots_with_known += 1
        if known >= total:
            roots_fully_mastered += 1
        elif known > 0:
            partial_roots.append({
                "root": root_text,
                "root_meaning": meaning,
                "known": known,
                "total": total,
            })

    partial_roots.sort(key=lambda r: r["known"] / r["total"], reverse=True)

    return RootCoverage(
        total_roots=total_roots,
        roots_with_known=roots_with_known,
        roots_fully_mastered=roots_fully_mastered,
        top_partial_roots=partial_roots[:5],
    )


def _get_recent_sessions(db: Session, limit: int = 10) -> list[SessionDetail]:
    sessions = (
        db.query(
            SentenceReviewLog.session_id,
            func.min(SentenceReviewLog.reviewed_at).label("first_review"),
            func.count(SentenceReviewLog.id).label("sentence_count"),
            func.avg(SentenceReviewLog.response_ms).label("avg_ms"),
        )
        .filter(SentenceReviewLog.session_id.isnot(None))
        .group_by(SentenceReviewLog.session_id)
        .order_by(func.min(SentenceReviewLog.reviewed_at).desc())
        .limit(limit)
        .all()
    )

    if not sessions:
        return []

    session_ids = [s.session_id for s in sessions]

    comp_rows = (
        db.query(
            SentenceReviewLog.session_id,
            SentenceReviewLog.comprehension,
            func.count(SentenceReviewLog.id),
        )
        .filter(SentenceReviewLog.session_id.in_(session_ids))
        .group_by(SentenceReviewLog.session_id, SentenceReviewLog.comprehension)
        .all()
    )

    comp_map: dict[str, dict[str, int]] = {}
    for session_id, signal, count in comp_rows:
        comp_map.setdefault(session_id, {})[signal] = count

    results = []
    for s in sessions:
        results.append(SessionDetail(
            session_id=s.session_id[:8] if s.session_id else "?",
            reviewed_at=s.first_review.isoformat() if s.first_review else "",
            sentence_count=s.sentence_count,
            comprehension=comp_map.get(s.session_id, {}),
            avg_response_ms=round(s.avg_ms, 0) if s.avg_ms else None,
        ))

    return results


def _get_graduated_today(db: Session) -> list[GraduatedWord]:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (
        db.query(
            UserLemmaKnowledge.lemma_id, Lemma.lemma_ar, Lemma.gloss_en,
            UserLemmaKnowledge.source, Lemma.transliteration_ala_lc,
            UserLemmaKnowledge.acquisition_started_at,
        )
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.graduated_at >= today_start)
        .order_by(UserLemmaKnowledge.graduated_at.desc())
        .all()
    )
    return [
        GraduatedWord(
            lemma_id=r.lemma_id, lemma_ar=r.lemma_ar, gloss_en=r.gloss_en,
            source=_SOURCE_LABELS.get(r.source or "study", r.source or "study"),
            transliteration=r.transliteration_ala_lc,
            started_at=r.acquisition_started_at.isoformat() if r.acquisition_started_at else None,
        )
        for r in rows
    ]


def _get_graduated_count_24h(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    return (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(UserLemmaKnowledge.graduated_at >= cutoff)
        .scalar()
    ) or 0


_SOURCE_LABELS = {
    "study": "Learn",
    "duolingo": "Duolingo",
    "textbook_scan": "OCR",
    "book": "Book",
    "story_import": "Story",
    "auto_intro": "Auto",
    "frequency_core": "Frequency core",
    "collateral": "Review",
    "leech_reintro": "Reintro",
}


def _get_introduced_today(db: Session) -> list[IntroducedBySource]:
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    rows = (
        db.query(
            UserLemmaKnowledge.source,
            func.count(UserLemmaKnowledge.id),
        )
        .filter(
            UserLemmaKnowledge.acquisition_started_at >= today_start,
            true_new_acquisition_episode_filter(),
        )
        .group_by(UserLemmaKnowledge.source)
        .all()
    )
    return [
        IntroducedBySource(
            source=_SOURCE_LABELS.get(src or "study", src or "study"),
            count=cnt,
        )
        for src, cnt in rows
        if cnt > 0
    ]


def _get_introduced_words_today(db: Session) -> list:
    from app.schemas import IntroducedWordDetail
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    rows = (
        db.query(
            UserLemmaKnowledge.lemma_id, Lemma.lemma_ar, Lemma.gloss_en,
            UserLemmaKnowledge.source, Lemma.transliteration_ala_lc,
        )
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.acquisition_started_at >= today_start,
            true_new_acquisition_episode_filter(),
        )
        .order_by(UserLemmaKnowledge.acquisition_started_at.desc())
        .all()
    )
    return [
        IntroducedWordDetail(
            lemma_id=r.lemma_id,
            lemma_ar=r.lemma_ar,
            gloss_en=r.gloss_en,
            source=_SOURCE_LABELS.get(r.source or "study", r.source or "study"),
            transliteration=r.transliteration_ala_lc,
        )
        for r in rows
    ]


def _compute_calibration_signal(comp: ComprehensionBreakdown) -> str:
    if comp.total < 5:
        return "not_enough_data"
    if comp.no_idea / comp.total > 0.3:
        return "too_hard"
    if comp.understood / comp.total > 0.9:
        return "too_easy"
    return "well_calibrated"


def _get_acquisition_pipeline(db: Session) -> AcquisitionPipeline:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(
            UserLemmaKnowledge.lemma_id,
            Lemma.lemma_ar,
            Lemma.gloss_en,
            UserLemmaKnowledge.acquisition_box,
            UserLemmaKnowledge.times_seen,
            UserLemmaKnowledge.times_correct,
            UserLemmaKnowledge.acquisition_next_due,
        )
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.knowledge_state == "acquiring")
        .order_by(UserLemmaKnowledge.acquisition_box, Lemma.lemma_ar)
        .all()
    )

    boxes: dict[int, list[AcquisitionWord]] = {1: [], 2: [], 3: []}
    due_per_box: dict[int, int] = {1: 0, 2: 0, 3: 0}
    for r in rows:
        box = r.acquisition_box or 1
        if box not in boxes:
            box = 1
        boxes[box].append(AcquisitionWord(
            lemma_id=r.lemma_id,
            lemma_ar=r.lemma_ar,
            gloss_en=r.gloss_en,
            acquisition_box=box,
            times_seen=r.times_seen or 0,
            times_correct=r.times_correct or 0,
        ))
        # Count due words per box
        if r.acquisition_next_due:
            acq_due = r.acquisition_next_due
            if acq_due.tzinfo is None:
                acq_due = acq_due.replace(tzinfo=timezone.utc)
            if acq_due <= now:
                due_per_box[box] += 1

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_day_start = today_start - timedelta(days=6)  # inclusive of today
    cutoff_7d = now - timedelta(days=7)

    grad_rows = (
        db.query(
            UserLemmaKnowledge.lemma_id,
            Lemma.lemma_ar,
            Lemma.gloss_en,
            UserLemmaKnowledge.graduated_at,
        )
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.graduated_at >= cutoff_7d,
            UserLemmaKnowledge.graduated_at.isnot(None),
        )
        .order_by(UserLemmaKnowledge.graduated_at.desc())
        .limit(15)
        .all()
    )

    # Per-day per-slot throughput: derived from ReviewLog box transitions +
    # ULK entered_acquiring_at (new arrivals) + ULK graduated_at (exits).
    # Replaces the old PipelineSnapshot-based delta, which froze at first
    # endpoint hit each day and made the page feel static.
    flow: dict[str, dict] = {}
    for i in range(6, -1, -1):
        day = today_start - timedelta(days=i)
        flow[day.strftime("%Y-%m-%d")] = {
            "date": day.strftime("%m-%d"),
            "entered": 0,        # new acquiring entries (via entered_acquiring_at)
            "box_1_in": 0,       # arrivals at box 1: new entries + lapses back
            "box_2_in": 0,       # promotions box 1 -> 2
            "box_3_in": 0,       # promotions box 2 -> 3
            "graduated": 0,      # exits from box 3 -> known
        }

    # Every episode enters Box 1, including leech restarts, but only true-new
    # episodes belong in the vocabulary-growth "entered" counter.
    box_1_entry_rows = (
        db.query(
            func.date(UserLemmaKnowledge.entered_acquiring_at).label("day"),
            func.count(UserLemmaKnowledge.id).label("cnt"),
        )
        .filter(
            UserLemmaKnowledge.entered_acquiring_at >= seven_day_start,
            UserLemmaKnowledge.entered_acquiring_at.isnot(None),
        )
        .group_by(func.date(UserLemmaKnowledge.entered_acquiring_at))
        .all()
    )
    for r in box_1_entry_rows:
        key = str(r.day)
        if key in flow:
            flow[key]["box_1_in"] += r.cnt

    new_entry_rows = (
        db.query(
            func.date(UserLemmaKnowledge.entered_acquiring_at).label("day"),
            func.count(UserLemmaKnowledge.id).label("cnt"),
        )
        .filter(
            UserLemmaKnowledge.entered_acquiring_at >= seven_day_start,
            UserLemmaKnowledge.entered_acquiring_at.isnot(None),
            true_new_acquisition_episode_filter(),
        )
        .group_by(func.date(UserLemmaKnowledge.entered_acquiring_at))
        .all()
    )
    for r in new_entry_rows:
        key = str(r.day)
        if key in flow:
            flow[key]["entered"] += r.cnt

    grad_day_rows = (
        db.query(
            func.date(UserLemmaKnowledge.graduated_at).label("day"),
            func.count(UserLemmaKnowledge.id).label("cnt"),
        )
        .filter(
            UserLemmaKnowledge.graduated_at >= seven_day_start,
            UserLemmaKnowledge.graduated_at.isnot(None),
        )
        .group_by(func.date(UserLemmaKnowledge.graduated_at))
        .all()
    )
    for r in grad_day_rows:
        key = str(r.day)
        if key in flow:
            flow[key]["graduated"] += r.cnt

    # Box transitions from acquisition ReviewLog rows
    log_rows = (
        db.query(ReviewLog.reviewed_at, ReviewLog.fsrs_log_json)
        .filter(
            ReviewLog.is_acquisition.is_(True),
            ReviewLog.reviewed_at >= seven_day_start.replace(tzinfo=None),
        )
        .all()
    )
    for r in log_rows:
        rev_at = r.reviewed_at
        if rev_at is None:
            continue
        if rev_at.tzinfo is None:
            rev_at = rev_at.replace(tzinfo=timezone.utc)
        key = rev_at.strftime("%Y-%m-%d")
        if key not in flow:
            continue
        log = r.fsrs_log_json
        if isinstance(log, str):
            try:
                log = json.loads(log)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(log, dict):
            continue
        before = log.get("acquisition_box_before")
        after = log.get("acquisition_box_after")
        if before == 1 and after == 2:
            flow[key]["box_2_in"] += 1
        elif before == 2 and after == 3:
            flow[key]["box_3_in"] += 1
        elif after == 1 and before in (2, 3):
            flow[key]["box_1_in"] += 1  # lapse back to box 1

    today_key = today_start.strftime("%Y-%m-%d")
    today_flow = flow[today_key]
    flow_days = [flow[k] for k in sorted(flow.keys())]

    return AcquisitionPipeline(
        box_1=boxes[1],
        box_2=boxes[2],
        box_3=boxes[3],
        box_1_count=len(boxes[1]),
        box_2_count=len(boxes[2]),
        box_3_count=len(boxes[3]),
        box_1_due=due_per_box[1],
        box_2_due=due_per_box[2],
        box_3_due=due_per_box[3],
        box_1_in_today=today_flow["box_1_in"],
        box_2_in_today=today_flow["box_2_in"],
        box_3_in_today=today_flow["box_3_in"],
        graduated_today=today_flow["graduated"],
        recent_graduations=[
            RecentGraduation(
                lemma_id=r.lemma_id,
                lemma_ar=r.lemma_ar,
                gloss_en=r.gloss_en,
                graduated_at=r.graduated_at.isoformat() if r.graduated_at else "",
            )
            for r in grad_rows
        ],
        flow_history=flow_days,
    )


def _get_insights(db: Session) -> InsightsOut:
    # 1. Average encounters to graduation (reviews BEFORE graduation, not all-time)
    from sqlalchemy import select
    subq = (
        select(
            UserLemmaKnowledge.lemma_id,
            func.count(ReviewLog.id).label('pre_grad_reviews'),
        )
        .join(ReviewLog, ReviewLog.lemma_id == UserLemmaKnowledge.lemma_id)
        .where(
            UserLemmaKnowledge.graduated_at.isnot(None),
            ReviewLog.reviewed_at <= UserLemmaKnowledge.graduated_at,
        )
        .group_by(UserLemmaKnowledge.lemma_id)
        .subquery()
    )
    avg_enc = db.query(func.avg(subq.c.pre_grad_reviews)).scalar()
    avg_encounters = round(float(avg_enc), 1) if avg_enc else None

    # 2. Graduation rate: graduated / (graduated + currently acquiring)
    graduated_count = (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(UserLemmaKnowledge.graduated_at.isnot(None))
        .scalar() or 0
    )
    acquiring_count = (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(UserLemmaKnowledge.knowledge_state == "acquiring")
        .scalar() or 0
    )
    pipeline_total = graduated_count + acquiring_count
    grad_rate = round(graduated_count / pipeline_total * 100, 1) if pipeline_total > 0 else None

    # 3. Best weekday
    best_day = None
    day_rows = (
        db.query(
            func.strftime('%w', ReviewLog.reviewed_at).label('dow'),
            func.count(ReviewLog.id).label('total'),
            func.sum(case((ReviewLog.rating >= 3, 1), else_=0)).label('correct'),
        )
        .group_by(func.strftime('%w', ReviewLog.reviewed_at))
        .having(func.count(ReviewLog.id) >= 5)
        .all()
    )
    if day_rows:
        day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        best = max(day_rows, key=lambda r: (r.correct or 0) / max(r.total, 1))
        acc = round((best.correct or 0) / max(best.total, 1) * 100, 1)
        best_day = {
            "day_name": day_names[int(best.dow)],
            "accuracy_pct": acc,
            "review_count": best.total,
        }

    # 4. Dark horse root
    dark_horse = None
    root_rows = (
        db.query(
            Root.root, Root.core_meaning_en,
            func.count(Lemma.lemma_id).label("total"),
            func.sum(
                case(
                    (UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]), 1),
                    else_=0,
                )
            ).label("known"),
        )
        .join(Lemma, Lemma.root_id == Root.root_id)
        .outerjoin(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(Lemma.canonical_lemma_id.is_(None))
        .group_by(Root.root_id)
        .having(func.count(Lemma.lemma_id) >= 3)
        .all()
    )
    if root_rows:
        partial = [r for r in root_rows if 0 < (r.known or 0) < r.total]
        if partial:
            best_root = max(partial, key=lambda r: r.total - (r.known or 0))
            dark_horse = {
                "root": best_root.root,
                "meaning": best_root.core_meaning_en,
                "known": best_root.known or 0,
                "total": best_root.total,
            }

    # 5. Sentence review counts
    unique_sent = (
        db.query(func.count(func.distinct(SentenceReviewLog.sentence_id)))
        .scalar() or 0
    )
    total_sent_reviews = (
        db.query(func.count(SentenceReviewLog.id))
        .scalar() or 0
    )

    # 6. Forgetting forecast
    now = datetime.now(timezone.utc)
    forecast = {}
    for label, skip_days in [("skip_1d", 1), ("skip_3d", 3), ("skip_7d", 7)]:
        cutoff = (now + timedelta(days=skip_days)).isoformat()
        count = (
            db.query(func.count(UserLemmaKnowledge.id))
            .filter(
                UserLemmaKnowledge.fsrs_card_json.isnot(None),
                UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]),
                func.json_extract(UserLemmaKnowledge.fsrs_card_json, '$.due') <= cutoff,
            )
            .scalar() or 0
        )
        forecast[label] = count

    # 7. Record days: most words introduced / graduated in a single day
    record_intro = None
    intro_row = (
        db.query(
            func.date(UserLemmaKnowledge.entered_acquiring_at).label("day"),
            func.count(UserLemmaKnowledge.id).label("cnt"),
        )
        .filter(
            UserLemmaKnowledge.entered_acquiring_at.isnot(None),
            true_new_acquisition_episode_filter(),
        )
        .group_by(func.date(UserLemmaKnowledge.entered_acquiring_at))
        .order_by(func.count(UserLemmaKnowledge.id).desc())
        .limit(1)
        .first()
    )
    if intro_row and intro_row.cnt > 0:
        record_intro = {"date": str(intro_row.day), "count": intro_row.cnt}

    record_grad = None
    grad_row = (
        db.query(
            func.date(UserLemmaKnowledge.graduated_at).label("day"),
            func.count(UserLemmaKnowledge.id).label("cnt"),
        )
        .filter(UserLemmaKnowledge.graduated_at.isnot(None))
        .group_by(func.date(UserLemmaKnowledge.graduated_at))
        .order_by(func.count(UserLemmaKnowledge.id).desc())
        .limit(1)
        .first()
    )
    if grad_row and grad_row.cnt > 0:
        record_grad = {"date": str(grad_row.day), "count": grad_row.cnt}

    return InsightsOut(
        avg_encounters_to_graduation=avg_encounters,
        graduation_rate_pct=grad_rate,
        best_weekday=best_day,
        dark_horse_root=dark_horse,
        unique_sentences_reviewed=unique_sent,
        total_sentence_reviews=total_sent_reviews,
        forgetting_forecast=forecast,
        record_intro_day=record_intro,
        record_graduation_day=record_grad,
    )


@router.get("/deep-analytics", response_model=DeepAnalyticsOut)
def get_deep_analytics(db: Session = Depends(get_db)):
    from app.services.book_coverage import compute_book_coverage

    try:
        book_coverage = compute_book_coverage(db)
    except Exception:
        # Coverage is decoration on this endpoint — a tokenmap or lookup
        # problem must not take down the whole deep-analytics panel.
        import logging

        logging.getLogger(__name__).exception("book coverage computation failed")
        book_coverage = []

    return DeepAnalyticsOut(
        stability_distribution=_get_stability_distribution(db),
        retention_7d=_get_retention_stats(db, 7),
        retention_30d=_get_retention_stats(db, 30),
        primary_cold_recall_30d=_get_primary_cold_recall(db, 30),
        transitions_today=_get_state_transitions(db, 0),
        transitions_7d=_get_state_transitions(db, 7),
        transitions_30d=_get_state_transitions(db, 30),
        comprehension_7d=_get_comprehension_breakdown(db, 7),
        comprehension_30d=_get_comprehension_breakdown(db, 30),
        struggling_words=_get_struggling_words(db),
        root_coverage=_get_root_coverage(db),
        recent_sessions=_get_recent_sessions(db),
        acquisition_pipeline=_get_acquisition_pipeline(db),
        insights=_get_insights(db),
        book_coverage=book_coverage,
    )
