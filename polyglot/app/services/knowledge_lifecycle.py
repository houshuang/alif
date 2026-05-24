"""Lifecycle bookkeeping for known-vs-recovered vocabulary stats."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import ReviewLog, UserLemmaKnowledge

ORIGIN_PRE_KNOWN = "pre_known"
ORIGIN_COGNATE_KNOWN = "cognate_known"
ORIGIN_COGNATE_PROPAGATION = "cognate_propagation"
ORIGIN_MARKED_UNKNOWN = "marked_unknown"
ORIGIN_MARKED_RECOGNIZED = "marked_recognized"
ORIGIN_MANUAL_INTRO = "manual_intro"
ORIGIN_COLLATERAL = "collateral"
ORIGIN_ENCOUNTERED = "encountered"


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _origin_for_source(source: str | None) -> str | None:
    if source == "review_lapse":
        return ORIGIN_MARKED_UNKNOWN
    if source == "cognate":
        return ORIGIN_COGNATE_KNOWN
    if source == "cognate_propagation":
        return ORIGIN_COGNATE_PROPAGATION
    if source == "collateral":
        return ORIGIN_COLLATERAL
    if source in {"study", "manual", "auto_intro", "frequency_core"}:
        return ORIGIN_MANUAL_INTRO
    if source == "encountered":
        return ORIGIN_ENCOUNTERED
    return None


def set_origin_if_missing(ulk: UserLemmaKnowledge, origin: str | None) -> None:
    if origin and not ulk.knowledge_origin:
        ulk.knowledge_origin = origin


def origin_for_acquisition(source: str | None, *, due_immediately: bool = False) -> str | None:
    if source == "reading_intake":
        return ORIGIN_MARKED_UNKNOWN if due_immediately else ORIGIN_PRE_KNOWN
    return _origin_for_source(source)


def record_failure(
    ulk: UserLemmaKnowledge,
    when: datetime | None = None,
    *,
    origin: str | None = None,
) -> None:
    when = _as_utc(when) or datetime.now(timezone.utc)
    set_origin_if_missing(ulk, origin)
    if ulk.first_failed_at is None:
        ulk.first_failed_at = when
    ulk.last_failed_at = when
    ulk.failure_count = (ulk.failure_count or 0) + 1


def record_correct_after_failure(
    ulk: UserLemmaKnowledge,
    when: datetime | None = None,
) -> None:
    when = _as_utc(when) or datetime.now(timezone.utc)
    first_failed = _as_utc(ulk.first_failed_at)
    if first_failed is None or ulk.first_correct_after_failure_at is not None:
        return
    if when >= first_failed:
        ulk.first_correct_after_failure_at = when


def record_review_result(
    ulk: UserLemmaKnowledge,
    rating_int: int,
    when: datetime | None = None,
) -> None:
    if rating_int == 1:
        record_failure(ulk, when)
    elif rating_int >= 3:
        record_correct_after_failure(ulk, when)


def snapshot(ulk: UserLemmaKnowledge) -> dict:
    return {
        "pre_knowledge_origin": ulk.knowledge_origin,
        "pre_first_failed_at": (
            ulk.first_failed_at.isoformat() if ulk.first_failed_at is not None else None
        ),
        "pre_last_failed_at": (
            ulk.last_failed_at.isoformat() if ulk.last_failed_at is not None else None
        ),
        "pre_failure_count": ulk.failure_count,
        "pre_first_correct_after_failure_at": (
            ulk.first_correct_after_failure_at.isoformat()
            if ulk.first_correct_after_failure_at is not None
            else None
        ),
    }


def _infer_origin(ulk: UserLemmaKnowledge, review_count: int) -> str | None:
    source_origin = _origin_for_source(ulk.source)
    if source_origin is not None:
        return source_origin
    if (
        ulk.knowledge_state == "known"
        and ulk.fsrs_card_json is None
        and review_count == 0
    ):
        return ORIGIN_PRE_KNOWN
    if ulk.source == "reading_intake":
        if ulk.knowledge_state == "encountered":
            return ORIGIN_MARKED_RECOGNIZED
        if ulk.entered_acquiring_at or ulk.acquisition_started_at:
            return ORIGIN_MARKED_UNKNOWN
    return None


def backfill_knowledge_lifecycle(db: Session) -> dict[str, int]:
    """Idempotently populate lifecycle columns for existing rows.

    Historic red taps were not logged separately from acquisition start, so
    `source='reading_intake'` acquiring rows get one inferred failure at their
    acquisition/introduced timestamp when no explicit failure exists yet.
    """
    logs_by_lemma: dict[int, list[ReviewLog]] = defaultdict(list)
    for log in (
        db.query(ReviewLog)
        .order_by(ReviewLog.lemma_id, ReviewLog.reviewed_at, ReviewLog.id)
        .all()
    ):
        logs_by_lemma[log.lemma_id].append(log)

    changed = 0
    origin_filled = 0
    failure_filled = 0
    recovered_filled = 0

    for ulk in db.query(UserLemmaKnowledge).all():
        logs = logs_by_lemma.get(ulk.lemma_id, [])
        if ulk.failure_count is None:
            ulk.failure_count = 0
            changed += 1

        if not ulk.knowledge_origin:
            origin = _infer_origin(ulk, len(logs))
            if origin is not None:
                ulk.knowledge_origin = origin
                origin_filled += 1
                changed += 1

        if ulk.first_failed_at is None:
            inferred_mark_failure: datetime | None = None
            if ulk.knowledge_origin == ORIGIN_MARKED_UNKNOWN:
                inferred_mark_failure = (
                    ulk.entered_acquiring_at
                    or ulk.acquisition_started_at
                    or ulk.introduced_at
                )
            log_failure_times = [
                log.reviewed_at for log in logs
                if log.rating == 1 and log.reviewed_at is not None
            ]
            failure_times = list(log_failure_times)
            if inferred_mark_failure is not None:
                failure_times.append(inferred_mark_failure)
            if failure_times:
                ordered_failures = sorted(_as_utc(t) for t in failure_times if t is not None)
                ulk.first_failed_at = ordered_failures[0]
                ulk.last_failed_at = ordered_failures[-1]
                ulk.failure_count = len(log_failure_times) + (
                    1 if inferred_mark_failure is not None else 0
                )
                failure_filled += 1
                changed += 1

        if ulk.first_failed_at and ulk.first_correct_after_failure_at is None:
            first_failed = _as_utc(ulk.first_failed_at)
            correct_times = [
                _as_utc(log.reviewed_at) for log in logs
                if log.rating >= 3
                and log.reviewed_at is not None
                and first_failed is not None
                and _as_utc(log.reviewed_at) >= first_failed
            ]
            if correct_times:
                ulk.first_correct_after_failure_at = sorted(correct_times)[0]
                recovered_filled += 1
                changed += 1

    if changed:
        db.commit()
    return {
        "changed": changed,
        "origin_filled": origin_filled,
        "failure_filled": failure_filled,
        "recovered_filled": recovered_filled,
    }
