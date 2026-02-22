import json
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from collections import Counter

from app.database import get_db
from app.models import (
    Lemma, UserLemmaKnowledge, ReviewLog, Root,
    SentenceReviewLog, SentenceWord,
)
from app.schemas import (
    StatsOut, DailyStatsPoint, LearningPaceOut,
    CEFREstimate, AnalyticsOut, GraduatedWord, IntroducedBySource,
    DeepAnalyticsOut, StabilityBucket, RetentionStats,
    StateTransitions, ComprehensionBreakdown, StrugglingWord,
    RootCoverage, SessionDetail,
    AcquisitionWord, RecentGraduation, AcquisitionPipeline,
    InsightsOut,
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


def _count_due_cards(db: Session, now: datetime) -> tuple[int, int, int]:
    """Return (total_due, fsrs_due, acquisition_due), excluding function words."""
    from app.services.sentence_validator import _is_function_word
    # Build function word lemma_id set for exclusion
    func_word_ids = {
        row.lemma_id for row in db.query(Lemma.lemma_id, Lemma.lemma_ar_bare).all()
        if row.lemma_ar_bare and _is_function_word(row.lemma_ar_bare)
    }

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
    calibration = _compute_calibration_signal(comp_today)

    graduated_24h = _get_graduated_count_24h(db)
    _add_cefr_predictions(cefr, pace, graduated_24h)

    words_reviewed_7d = _get_words_reviewed_count(db, days=7)
    words_reviewed_all = _get_words_reviewed_count(db)
    unique_recognized_7d = _get_unique_words_recognized(db, 0, 7)
    unique_recognized_prior_7d = _get_unique_words_recognized(db, 7, 14)

    return AnalyticsOut(
        stats=basic,
        pace=pace,
        cefr=cefr,
        daily_history=history,
        comprehension_today=comp_today,
        graduated_today=graduated_today,
        introduced_today=introduced_today,
        calibration_signal=calibration,
        total_words_reviewed_7d=words_reviewed_7d,
        total_words_reviewed_alltime=words_reviewed_all,
        unique_words_recognized_7d=unique_recognized_7d,
        unique_words_recognized_prior_7d=unique_recognized_prior_7d,
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
        payload = log_json
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict):
            continue

        state = payload.get("state")
        prev_state = payload.get("prev_state")
        if not state or not prev_state:
            continue

        # Map FSRS state names to our states
        # FSRS states: New(0), Learning(1), Review(2), Relearning(3)
        state_map = {"New": "new", "Learning": "learning", "Review": "known", "Relearning": "lapsed"}
        s = state_map.get(state, state)
        ps = state_map.get(prev_state, prev_state)

        if ps == "new" and s == "learning":
            transitions.new_to_learning += 1
        elif ps == "learning" and s == "known":
            transitions.learning_to_known += 1
        elif ps in ("known", "Review") and s in ("lapsed", "Relearning"):
            transitions.known_to_lapsed += 1
        elif ps in ("lapsed", "Relearning") and s == "learning":
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
        db.query(UserLemmaKnowledge.lemma_id, Lemma.lemma_ar, Lemma.gloss_en)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.graduated_at >= today_start)
        .order_by(UserLemmaKnowledge.graduated_at.desc())
        .all()
    )
    return [
        GraduatedWord(lemma_id=r.lemma_id, lemma_ar=r.lemma_ar, gloss_en=r.gloss_en)
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
        .filter(UserLemmaKnowledge.acquisition_started_at >= today_start)
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

    # Build flow history: entries and graduations per day for last 7 days
    flow_days = []
    for i in range(6, -1, -1):
        day_start = (now - timedelta(days=i)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_str = day_start.strftime("%m-%d")
        flow_days.append({"date": day_str, "entered": 0, "graduated": 0})

    # Count entries per day (using entered_acquiring_at)
    try:
        entry_rows = (
            db.query(
                func.date(UserLemmaKnowledge.entered_acquiring_at).label("day"),
                func.count(UserLemmaKnowledge.id).label("cnt"),
            )
            .filter(
                UserLemmaKnowledge.entered_acquiring_at >= cutoff_7d,
                UserLemmaKnowledge.entered_acquiring_at.isnot(None),
            )
            .group_by(func.date(UserLemmaKnowledge.entered_acquiring_at))
            .all()
        )
        entries_by_day = {str(r.day): r.cnt for r in entry_rows}
    except Exception:
        entries_by_day = {}

    # Count graduations per day
    try:
        grad_day_rows = (
            db.query(
                func.date(UserLemmaKnowledge.graduated_at).label("day"),
                func.count(UserLemmaKnowledge.id).label("cnt"),
            )
            .filter(
                UserLemmaKnowledge.graduated_at >= cutoff_7d,
                UserLemmaKnowledge.graduated_at.isnot(None),
            )
            .group_by(func.date(UserLemmaKnowledge.graduated_at))
            .all()
        )
        grads_by_day = {str(r.day): r.cnt for r in grad_day_rows}
    except Exception:
        grads_by_day = {}

    # Fill flow_days with actual counts
    for i in range(6, -1, -1):
        day_dt = (now - timedelta(days=i)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_key = day_dt.strftime("%Y-%m-%d")
        idx = 6 - i
        flow_days[idx]["entered"] = entries_by_day.get(day_key, 0)
        flow_days[idx]["graduated"] = grads_by_day.get(day_key, 0)

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
        .filter(UserLemmaKnowledge.entered_acquiring_at.isnot(None))
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
    return DeepAnalyticsOut(
        stability_distribution=_get_stability_distribution(db),
        retention_7d=_get_retention_stats(db, 7),
        retention_30d=_get_retention_stats(db, 30),
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
    )
