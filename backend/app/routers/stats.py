import json
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from collections import Counter

from app.database import get_db
from app.models import Lemma, UserLemmaKnowledge, ReviewLog
from app.schemas import (
    StatsOut, DailyStatsPoint, LearningPaceOut,
    CEFREstimate, AnalyticsOut,
)

router = APIRouter(prefix="/api/stats", tags=["stats"])

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


def _count_due_cards(db: Session, now: datetime) -> int:
    due = 0
    rows = (
        db.query(UserLemmaKnowledge.fsrs_card_json)
        .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
        .all()
    )
    for (card_data,) in rows:
        if not card_data:
            continue
        if isinstance(card_data, str):
            card_data = json.loads(card_data)
        due_str = card_data.get("due")
        if not due_str:
            continue
        due_dt = datetime.fromisoformat(due_str)
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        if due_dt <= now:
            due += 1
    return due


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

    due_today = _count_due_cards(db, now)

    return StatsOut(
        total_words=total,
        known=known,
        learning=learning,
        new=new_count,
        due_today=due_today,
        reviews_today=reviews_today,
        total_reviews=total_reviews,
        lapsed=lapsed,
    )


def _extract_logged_state(fsrs_log_json: object) -> str | None:
    if not fsrs_log_json:
        return None
    payload = fsrs_log_json
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    state = payload.get("state")
    return state if isinstance(state, str) else None


def _get_first_known_dates(db: Session) -> dict[int, datetime.date]:
    """Return first date each lemma reached state='known' in review logs."""
    rows = (
        db.query(ReviewLog.lemma_id, ReviewLog.reviewed_at, ReviewLog.fsrs_log_json)
        .order_by(ReviewLog.reviewed_at.asc(), ReviewLog.id.asc())
        .all()
    )
    first_known_dates: dict[int, datetime.date] = {}
    for lemma_id, reviewed_at, fsrs_log_json in rows:
        if lemma_id in first_known_dates or reviewed_at is None:
            continue
        if _extract_logged_state(fsrs_log_json) != "known":
            continue
        first_known_dates[lemma_id] = reviewed_at.date()
    return first_known_dates


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


def _estimate_cefr(known_count: int) -> CEFREstimate:
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
        next_level=next_level,
        words_to_next=words_to_next,
        reading_coverage_pct=round(coverage, 1),
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
    cefr = _estimate_cefr(basic.known)
    history = _get_daily_history(db, days, first_known_dates=first_known_dates)

    return AnalyticsOut(
        stats=basic,
        pace=pace,
        cefr=cefr,
        daily_history=history,
    )


@router.get("/cefr", response_model=CEFREstimate)
def get_cefr(db: Session = Depends(get_db)):
    known = _count_state(db, "known")
    return _estimate_cefr(known)
