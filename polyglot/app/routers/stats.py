"""Per-language stats.

Backs the polyglot Stats screen. Returns:
- knowledge breakdown by state (known/acquiring/learning/encountered/lapsed/...)
- today's activity (reviews, pages read, marks, transitions)
- Leitner box distribution + FSRS stability histogram
- frequency-rank coverage (top-N bands) when a frequency list is present
- last-14-days activity strip
- story progress
- a recent activity feed from ActivityLog
"""
from collections import defaultdict
from datetime import datetime, date, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Lemma, UserLemmaKnowledge, Story, Page, Language,
    ReviewLog, SentenceReviewLog, FrequencyEntry, ActivityLog,
)
from app.services.fsrs_service import parse_json_column
from app.services.knowledge_lifecycle import (
    ORIGIN_COGNATE_KNOWN,
    ORIGIN_MARKED_RECOGNIZED,
    ORIGIN_PRE_KNOWN,
)

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _utc_start_of_today() -> datetime:
    """SQLite stores naive UTC datetimes — return a matching naive boundary."""
    return datetime.combine(datetime.utcnow().date(), time.min)


def _utc_start_of_day(d: date) -> datetime:
    return datetime.combine(d, time.min)


_STABILITY_ORDER = ["<1d", "1-3d", "3-7d", "7-21d", "21-60d", "60d+"]


def _bucket_stability(days: float | None) -> str:
    if days is None:
        return "<1d"
    if days < 1: return "<1d"
    if days < 3: return "1-3d"
    if days < 7: return "3-7d"
    if days < 21: return "7-21d"
    if days < 60: return "21-60d"
    return "60d+"


def _as_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _card_due_on_or_before(card_json: dict[str, Any] | None, now: datetime) -> bool:
    if not isinstance(card_json, dict):
        return False
    due_raw = card_json.get("due")
    if not due_raw or not isinstance(due_raw, str):
        return False
    try:
        due = datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    due_naive = _as_naive_utc(due)
    return due_naive is not None and due_naive <= now


@router.get("")
def get_stats(language_code: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not db.query(Language).filter(Language.code == language_code).first():
        raise HTTPException(status_code=400, detail=f"Unknown language: {language_code}")

    today_start = _utc_start_of_today()
    now = datetime.utcnow()

    # ── 1. Lemma counts by state ─────────────────────────────────────────
    state_counts_rows = (
        db.query(UserLemmaKnowledge.knowledge_state, func.count(UserLemmaKnowledge.id))
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(Lemma.language_code == language_code)
        .group_by(UserLemmaKnowledge.knowledge_state)
        .all()
    )
    state_counts = {state: count for state, count in state_counts_rows}

    total_lemmas = (
        db.query(func.count(Lemma.lemma_id))
        .filter(Lemma.language_code == language_code)
        .scalar() or 0
    )
    encountered_or_better = (
        db.query(func.count(UserLemmaKnowledge.id))
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(Lemma.language_code == language_code)
        .scalar() or 0
    )
    new_count = max(total_lemmas - encountered_or_better, 0)

    by_state = {
        "known": state_counts.get("known", 0),
        "learning": state_counts.get("learning", 0),
        "lapsed": state_counts.get("lapsed", 0),
        # Legacy `acquiring` aggregates acquiring + learning so the existing
        # client doesn't regress; new clients should read `acquiring_only` +
        # `learning` separately for the funnel view.
        "acquiring": state_counts.get("acquiring", 0) + state_counts.get("learning", 0),
        "acquiring_only": state_counts.get("acquiring", 0),
        "encountered": state_counts.get("encountered", 0),
        "unknown": state_counts.get("unknown", 0),
        "ignored": state_counts.get("ignore", 0),
        "suspended": state_counts.get("suspended", 0),
    }

    # ── 2. Leitner box distribution ──────────────────────────────────────
    box_rows = (
        db.query(
            UserLemmaKnowledge.acquisition_box,
            func.count(UserLemmaKnowledge.id),
        )
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.knowledge_state == "acquiring",
        )
        .group_by(UserLemmaKnowledge.acquisition_box)
        .all()
    )
    box_counts = {1: 0, 2: 0, 3: 0}
    for box, count in box_rows:
        if box in box_counts:
            box_counts[box] = count

    box_due_now = (
        db.query(func.count(UserLemmaKnowledge.id))
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_next_due.isnot(None),
            UserLemmaKnowledge.acquisition_next_due <= now,
        )
        .scalar() or 0
    )

    leitner = {
        "total_acquiring": sum(box_counts.values()),
        "box_1": box_counts[1],
        "box_2": box_counts[2],
        "box_3": box_counts[3],
        "due_now": int(box_due_now),
    }

    # ── 3. FSRS stability histogram ──────────────────────────────────────
    fsrs_rows = (
        db.query(UserLemmaKnowledge.fsrs_card_json, UserLemmaKnowledge.knowledge_state)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.fsrs_card_json.isnot(None),
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "lapsed"]),
        )
        .all()
    )
    stability_counts: dict[str, int] = defaultdict(int)
    for card_json, _state in fsrs_rows:
        if not isinstance(card_json, dict):
            continue
        stability_counts[_bucket_stability(card_json.get("stability"))] += 1
    stability_buckets = [
        {"label": label, "count": stability_counts.get(label, 0)}
        for label in _STABILITY_ORDER
    ]

    fsrs = {
        "tracked": len(fsrs_rows),
        "stability_buckets": stability_buckets,
    }
    recovery = _recovery_progress(db, language_code)
    known_summary, judged_progress = _known_and_judged_progress(db, language_code, now)

    # ── 4. Today's activity ──────────────────────────────────────────────
    reviews_today = (
        db.query(func.count(ReviewLog.id))
        .join(Lemma, Lemma.lemma_id == ReviewLog.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            ReviewLog.reviewed_at >= today_start,
        )
        .scalar() or 0
    )
    sentence_reviews_today = (
        db.query(func.count(SentenceReviewLog.id))
        .filter(SentenceReviewLog.reviewed_at >= today_start)
        .scalar() or 0
    )
    pages_read_today = (
        db.query(func.count(Page.id))
        .join(Story, Story.id == Page.story_id)
        .filter(
            Story.language_code == language_code,
            Page.viewed_at >= today_start,
        )
        .scalar() or 0
    )
    new_lemmas_today = (
        db.query(func.count(UserLemmaKnowledge.id))
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.introduced_at >= today_start,
        )
        .scalar() or 0
    )
    graduated_today = (
        db.query(func.count(UserLemmaKnowledge.id))
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.graduated_at >= today_start,
        )
        .scalar() or 0
    )
    unknown_marked_today = (
        db.query(func.count(UserLemmaKnowledge.id))
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.first_failed_at >= today_start,
        )
        .scalar() or 0
    )

    # ── 5. Streak: consecutive past days with ≥1 review, anchored on today ──
    streak = 0
    cursor = datetime.utcnow().date()
    # If today has no reviews yet, allow a 1-day grace and start counting from yesterday.
    if reviews_today == 0:
        cursor = cursor - timedelta(days=1)
    while streak < 365:
        day_start = _utc_start_of_day(cursor)
        day_end = day_start + timedelta(days=1)
        had_review = (
            db.query(ReviewLog.id)
            .join(Lemma, Lemma.lemma_id == ReviewLog.lemma_id)
            .filter(
                Lemma.language_code == language_code,
                ReviewLog.reviewed_at >= day_start,
                ReviewLog.reviewed_at < day_end,
            )
            .first()
        )
        if not had_review:
            break
        streak += 1
        cursor = cursor - timedelta(days=1)

    today = {
        "reviews": int(reviews_today),
        "sentence_reviews": int(sentence_reviews_today),
        "pages_read": int(pages_read_today),
        "new_lemmas": int(new_lemmas_today),
        "graduated": int(graduated_today),
        "marked_unknown": int(unknown_marked_today),
        "streak": streak,
    }

    # ── 6. Last-14-day activity strip ────────────────────────────────────
    history: list[dict[str, Any]] = []
    for i in range(13, -1, -1):
        day = (datetime.utcnow().date() - timedelta(days=i))
        day_start = _utc_start_of_day(day)
        day_end = day_start + timedelta(days=1)
        r = (
            db.query(func.count(ReviewLog.id))
            .join(Lemma, Lemma.lemma_id == ReviewLog.lemma_id)
            .filter(
                Lemma.language_code == language_code,
                ReviewLog.reviewed_at >= day_start,
                ReviewLog.reviewed_at < day_end,
            )
            .scalar() or 0
        )
        p = (
            db.query(func.count(Page.id))
            .join(Story, Story.id == Page.story_id)
            .filter(
                Story.language_code == language_code,
                Page.viewed_at >= day_start,
                Page.viewed_at < day_end,
            )
            .scalar() or 0
        )
        n = (
            db.query(func.count(UserLemmaKnowledge.id))
            .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
            .filter(
                Lemma.language_code == language_code,
                UserLemmaKnowledge.introduced_at >= day_start,
                UserLemmaKnowledge.introduced_at < day_end,
            )
            .scalar() or 0
        )
        history.append({
            "date": day.isoformat(),
            "reviews": int(r),
            "pages_read": int(p),
            "new_lemmas": int(n),
        })

    # ── 6b. Weekly flow history (the conversion loop over time) ──────────
    flow_history = _flow_history(db, language_code)

    # ── 7. Frequency-rank coverage (when a frequency list is present) ────
    frequency_block = _frequency_progress(db, language_code)

    # ── 8. Story progress ────────────────────────────────────────────────
    story_rows = (
        db.query(
            Story.id, Story.title, Story.page_count, Story.total_words,
            Story.known_count, Story.unknown_count,
            func.count(Page.id).filter(Page.processed_at.isnot(None)).label("processed"),
            func.count(Page.id).filter(Page.viewed_at.isnot(None)).label("viewed"),
        )
        .outerjoin(Page, Page.story_id == Story.id)
        .filter(Story.language_code == language_code)
        .group_by(Story.id)
        .order_by(Story.created_at.desc())
        .all()
    )
    stories = [
        {
            "id": s.id,
            "title": s.title,
            "page_count": s.page_count,
            "processed_pages": int(s.processed or 0),
            "viewed_pages": int(s.viewed or 0),
            "total_words": int(s.total_words or 0),
            "known_count": int(s.known_count or 0),
            "unknown_count": int(s.unknown_count or 0),
        }
        for s in story_rows
    ]

    # ── 9. Recent activity feed (ActivityLog, last 10) ───────────────────
    activity_rows = (
        db.query(ActivityLog)
        .filter(
            (ActivityLog.language_code == language_code)
            | (ActivityLog.language_code.is_(None))
        )
        .order_by(ActivityLog.created_at.desc())
        .limit(10)
        .all()
    )
    activity = [
        {
            "event_type": a.event_type,
            "summary": a.summary,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in activity_rows
    ]

    return {
        "language_code": language_code,
        "total_lemmas": total_lemmas,
        "new": new_count,
        "by_state": by_state,
        "leitner": leitner,
        "fsrs": fsrs,
        "recovery": recovery,
        "known_summary": known_summary,
        "judged_progress": judged_progress,
        "today": today,
        "history_14d": history,
        "flow_history": flow_history,
        "frequency": frequency_block,
        "stories": stories,
        "activity": activity,
    }


def _flow_history(db: Session, language_code: str, weeks: int = 8) -> list[dict[str, Any]]:
    """Weekly counts of the conversion loop over time.

    Buckets ULK milestone timestamps by ISO week (Monday-anchored) for the last
    `weeks` weeks: assumed→exposure-confirmed (`confirmed_at`), gaps discovered
    (`first_failed_at`), graduations (`graduated_at`), and new lemmas
    (`introduced_at`). One query, bucketed in Python. Snapshot stats say how
    many words are unconfirmed; this says whether that number is shrinking.
    """
    today = datetime.utcnow().date()
    this_monday = today - timedelta(days=today.weekday())
    week_starts = [this_monday - timedelta(weeks=w) for w in range(weeks - 1, -1, -1)]
    earliest = week_starts[0]
    buckets: dict[Any, dict[str, int]] = {
        ws: {"confirmed": 0, "gaps_discovered": 0, "graduated": 0, "new_lemmas": 0}
        for ws in week_starts
    }

    rows = (
        db.query(
            UserLemmaKnowledge.confirmed_at,
            UserLemmaKnowledge.first_failed_at,
            UserLemmaKnowledge.graduated_at,
            UserLemmaKnowledge.introduced_at,
        )
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(Lemma.language_code == language_code)
        .all()
    )

    def _week_of(value) -> Any:
        if value is None:
            return None
        d = value.date() if isinstance(value, datetime) else value
        if d < earliest or d > today:
            return None
        ws = d - timedelta(days=d.weekday())
        return ws if ws in buckets else None

    for confirmed_at, first_failed_at, graduated_at, introduced_at in rows:
        for value, key in (
            (confirmed_at, "confirmed"),
            (first_failed_at, "gaps_discovered"),
            (graduated_at, "graduated"),
            (introduced_at, "new_lemmas"),
        ):
            ws = _week_of(value)
            if ws is not None:
                buckets[ws][key] += 1

    return [{"week_start": ws.isoformat(), **buckets[ws]} for ws in week_starts]


def _recovery_progress(db: Session, language_code: str) -> dict[str, int]:
    rows = (
        db.query(UserLemmaKnowledge)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(Lemma.language_code == language_code)
        .all()
    )

    stats = {
        "pre_known": 0,
        "cognate_known": 0,
        "ever_failed": 0,
        "recovered_once": 0,
        "graduated_after_failure": 0,
        "stable_after_failure_7d": 0,
        "stable_after_failure_21d": 0,
        "stable_after_failure_60d": 0,
        "currently_known_after_failure": 0,
        "learning_after_failure": 0,
        "still_acquiring_after_failure": 0,
        "lapsed_after_failure": 0,
        "failed_not_yet_recovered": 0,
    }

    for ulk in rows:
        if ulk.knowledge_origin == ORIGIN_PRE_KNOWN:
            stats["pre_known"] += 1
        if ulk.knowledge_origin == ORIGIN_COGNATE_KNOWN:
            stats["cognate_known"] += 1
        if ulk.first_failed_at is None:
            continue

        stats["ever_failed"] += 1
        if ulk.first_correct_after_failure_at is not None:
            stats["recovered_once"] += 1
        else:
            stats["failed_not_yet_recovered"] += 1
        if ulk.graduated_at is not None:
            stats["graduated_after_failure"] += 1

        if ulk.knowledge_state == "known":
            stats["currently_known_after_failure"] += 1
        elif ulk.knowledge_state == "learning":
            stats["learning_after_failure"] += 1
        elif ulk.knowledge_state == "acquiring":
            stats["still_acquiring_after_failure"] += 1
        elif ulk.knowledge_state == "lapsed":
            stats["lapsed_after_failure"] += 1

        card_json = parse_json_column(ulk.fsrs_card_json, default={})
        stability = card_json.get("stability") if isinstance(card_json, dict) else None
        if isinstance(stability, (int, float)):
            if stability >= 7:
                stats["stable_after_failure_7d"] += 1
            if stability >= 21:
                stats["stable_after_failure_21d"] += 1
            if stability >= 60:
                stats["stable_after_failure_60d"] += 1

    return stats


def _known_and_judged_progress(
    db: Session,
    language_code: str,
    now: datetime,
) -> tuple[dict[str, int], dict[str, Any]]:
    rows = (
        db.query(UserLemmaKnowledge)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(Lemma.language_code == language_code)
        .all()
    )
    by_id = {ulk.lemma_id: ulk for ulk in rows}

    red_ids: set[int] = {
        ulk.lemma_id for ulk in rows if ulk.first_failed_at is not None
    }
    green_ids: set[int] = {
        ulk.lemma_id
        for ulk in rows
        if ulk.knowledge_origin in {ORIGIN_PRE_KNOWN, ORIGIN_MARKED_RECOGNIZED}
        or ulk.first_correct_after_failure_at is not None
    }
    yellow_ids: set[int] = set()

    for lemma_id, rating in (
        db.query(ReviewLog.lemma_id, ReviewLog.rating)
        .join(Lemma, Lemma.lemma_id == ReviewLog.lemma_id)
        .filter(Lemma.language_code == language_code)
        .all()
    ):
        if rating == 1:
            red_ids.add(lemma_id)
        elif rating == 2:
            yellow_ids.add(lemma_id)
        elif rating >= 3:
            green_ids.add(lemma_id)

    judged_ids = red_ids | green_ids
    known_ids = {
        ulk.lemma_id for ulk in rows if ulk.knowledge_state == "known"
    }
    assumed_origins = {ORIGIN_PRE_KNOWN, ORIGIN_COGNATE_KNOWN}
    lapsed_from_assumed_ids = {
        ulk.lemma_id
        for ulk in rows
        if ulk.knowledge_origin in assumed_origins and ulk.first_failed_at is not None
    }

    known_summary = {
        "total": len(known_ids),
        "pre_known": sum(
            1 for ulk in rows
            if ulk.knowledge_state == "known"
            and ulk.knowledge_origin == ORIGIN_PRE_KNOWN
        ),
        "cognate_known": sum(
            1 for ulk in rows
            if ulk.knowledge_state == "known"
            and ulk.knowledge_origin == ORIGIN_COGNATE_KNOWN
        ),
        "fsrs_known": sum(
            1 for ulk in rows
            if ulk.knowledge_state == "known"
            and ulk.fsrs_card_json is not None
        ),
        "assumed_known": sum(
            1 for ulk in rows
            if ulk.knowledge_state == "known"
            and ulk.fsrs_card_json is None
        ),
        "exposure_confirmed": sum(
            1 for ulk in rows
            if ulk.knowledge_state == "known"
            and ulk.fsrs_card_json is None
            and ulk.confirmed_at is not None
        ),
        "assumed_unconfirmed": sum(
            1 for ulk in rows
            if ulk.knowledge_state == "known"
            and ulk.fsrs_card_json is None
            and ulk.confirmed_at is None
        ),
        "judged_known": len(known_ids & judged_ids),
        "unjudged_known": len(known_ids - judged_ids),
        "lapsed_from_assumed_known": len(lapsed_from_assumed_ids),
        "lapsed_from_assumed_known_to_learn": sum(
            1 for lid in lapsed_from_assumed_ids
            if by_id[lid].knowledge_state in {"acquiring", "learning", "lapsed", "suspended"}
        ),
    }

    pipeline_ids = judged_ids
    acquiring_ids = {
        lid for lid in pipeline_ids
        if by_id.get(lid) is not None and by_id[lid].knowledge_state == "acquiring"
    }
    fsrs_ids = {
        lid for lid in pipeline_ids
        if by_id.get(lid) is not None
        and by_id[lid].knowledge_state in {"learning", "known", "lapsed"}
        and by_id[lid].fsrs_card_json is not None
    }

    def _state_count(state: str) -> int:
        return sum(
            1 for lid in pipeline_ids
            if by_id.get(lid) is not None and by_id[lid].knowledge_state == state
        )

    judged_progress = {
        "total": len(judged_ids),
        "to_learn": sum(
            1 for lid in pipeline_ids
            if by_id.get(lid) is not None
            and by_id[lid].knowledge_state in {"acquiring", "learning", "lapsed", "suspended"}
        ),
        "learnt": len(known_ids & judged_ids),
        "pending": sum(
            1 for lid in pipeline_ids
            if by_id.get(lid) is not None
            and by_id[lid].knowledge_state in {"encountered", "unknown"}
        ),
        "ever_red": len(red_ids),
        "ever_green": len(green_ids),
        "yellow_only": len(yellow_ids - judged_ids),
        "lapsed_from_known": len(lapsed_from_assumed_ids),
        "lapsed_from_known_to_learn": known_summary["lapsed_from_assumed_known_to_learn"],
        "pipeline": {
            "acquiring": len(acquiring_ids),
            "box_1": sum(1 for lid in acquiring_ids if (by_id[lid].acquisition_box or 1) == 1),
            "box_2": sum(1 for lid in acquiring_ids if by_id[lid].acquisition_box == 2),
            "box_3": sum(1 for lid in acquiring_ids if by_id[lid].acquisition_box == 3),
            "acquisition_due_now": sum(
                1 for lid in acquiring_ids
                if _as_naive_utc(by_id[lid].acquisition_next_due) is not None
                and _as_naive_utc(by_id[lid].acquisition_next_due) <= now
            ),
            "learning": _state_count("learning"),
            "known": _state_count("known"),
            "lapsed": _state_count("lapsed"),
            "suspended": _state_count("suspended"),
            "fsrs_tracked": len(fsrs_ids),
            "fsrs_due_now": sum(
                1 for lid in fsrs_ids
                if _card_due_on_or_before(parse_json_column(by_id[lid].fsrs_card_json), now)
            ),
        },
    }

    return known_summary, judged_progress


# ── frequency progress (top-N bands) ──────────────────────────────────────


def _frequency_progress(db: Session, language_code: str) -> dict[str, Any] | None:
    """Top-N coverage bands when a frequency list exists for this language.

    Picks the largest available source (by entry count) and reports coverage
    for the top 100/500/1000/2000/5000 ranks. Returns None when the table is
    empty for this language — the frontend hides the card in that case.
    """
    source_row = (
        db.query(FrequencyEntry.source, func.count(FrequencyEntry.id).label("n"))
        .filter(FrequencyEntry.language_code == language_code)
        .group_by(FrequencyEntry.source)
        .order_by(func.count(FrequencyEntry.id).desc())
        .first()
    )
    if not source_row:
        return None

    source = source_row.source
    total_in_source = int(source_row.n)

    rows = (
        db.query(FrequencyEntry.rank, UserLemmaKnowledge.knowledge_state,
                 FrequencyEntry.lemma_id)
        .outerjoin(UserLemmaKnowledge,
                   UserLemmaKnowledge.lemma_id == FrequencyEntry.lemma_id)
        .filter(
            FrequencyEntry.language_code == language_code,
            FrequencyEntry.source == source,
        )
        .all()
    )
    rank_state: dict[int, str] = {}
    for rank, state, lemma_id in rows:
        if lemma_id is None:
            rank_state[rank] = "unmapped"
        else:
            rank_state[rank] = state or "new"

    band_candidates = [100, 500, 1000, 2000, 5000]
    band_sizes = [b for b in band_candidates if b <= total_in_source]
    if not band_sizes:
        band_sizes = [total_in_source]
    elif band_sizes[-1] < total_in_source:
        band_sizes.append(total_in_source)

    bands: list[dict[str, Any]] = []
    for top_n in band_sizes:
        learned = acquiring = encountered = unmapped = newer = 0
        for rank in range(1, top_n + 1):
            st = rank_state.get(rank)
            if st is None:
                newer += 1
                continue
            if st == "unmapped":
                unmapped += 1
            elif st in ("known", "learning"):
                learned += 1
            elif st in ("acquiring", "lapsed"):
                acquiring += 1
            elif st == "encountered":
                encountered += 1
            else:
                newer += 1
        coverage_pct = round((learned / top_n) * 100, 1) if top_n else 0.0
        bands.append({
            "top_n": top_n,
            "learned": learned,
            "acquiring": acquiring,
            "encountered": encountered,
            "unmapped": unmapped,
            "new": newer,
            "coverage_pct": coverage_pct,
        })

    return {
        "source": source,
        "total_entries": total_in_source,
        "bands": bands,
    }
