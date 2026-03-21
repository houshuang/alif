"""Per-word fluency scoring based on response time.

Computes how fast a learner recognizes each word relative to their overall
average. Words with consistently slow recognition (fluency < 1.0) indicate
weak automaticity even if FSRS ratings look healthy.

Used by sentence_selector.py to boost sentences containing slow-recognition
words so they get more practice.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.models import ReviewLog

logger = logging.getLogger(__name__)

# Minimum reviews with response_ms data before computing fluency
MIN_REVIEWS_FOR_FLUENCY = 3


def compute_word_fluency(
    db: Session,
    lemma_id: int,
    lookback_days: int = 14,
) -> Optional[float]:
    """Compute fluency score for a single word.

    Returns ratio of global_median / word_median:
    - >1.0 = faster than average (fluent)
    - <1.0 = slower than average (weak automaticity)
    - None = insufficient data (fewer than MIN_REVIEWS_FOR_FLUENCY reviews)
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).replace(
        tzinfo=None
    )

    # Get word's response times
    word_times = (
        db.query(ReviewLog.response_ms)
        .filter(
            ReviewLog.lemma_id == lemma_id,
            ReviewLog.response_ms.isnot(None),
            ReviewLog.reviewed_at >= cutoff,
        )
        .order_by(ReviewLog.reviewed_at.desc())
        .all()
    )

    word_ms_list = [r.response_ms for r in word_times if r.response_ms and r.response_ms > 0]
    if len(word_ms_list) < MIN_REVIEWS_FOR_FLUENCY:
        return None

    word_median = _median(word_ms_list)
    if word_median <= 0:
        return None

    # Get global median across all words in the lookback period
    all_times = (
        db.query(ReviewLog.response_ms)
        .filter(
            ReviewLog.response_ms.isnot(None),
            ReviewLog.reviewed_at >= cutoff,
        )
        .all()
    )

    all_ms_list = [r.response_ms for r in all_times if r.response_ms and r.response_ms > 0]
    if len(all_ms_list) < 10:
        return None

    global_median = _median(all_ms_list)
    if global_median <= 0:
        return None

    return global_median / word_median


def compute_fluency_batch(
    db: Session,
    lemma_ids: set[int],
    lookback_days: int = 14,
) -> dict[int, float]:
    """Compute fluency scores for multiple words in a single query batch.

    Returns a dict mapping lemma_id -> fluency_score for words with
    sufficient data. Words without enough reviews are omitted.

    This is the performance-critical path used by build_session() —
    uses two bulk queries instead of N per-word queries.
    """
    if not lemma_ids:
        return {}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).replace(
        tzinfo=None
    )

    # Single query: get all response times for target words in the lookback window
    word_reviews = (
        db.query(ReviewLog.lemma_id, ReviewLog.response_ms)
        .filter(
            ReviewLog.lemma_id.in_(lemma_ids),
            ReviewLog.response_ms.isnot(None),
            ReviewLog.response_ms > 0,
            ReviewLog.reviewed_at >= cutoff,
        )
        .all()
    )

    # Group by lemma_id
    times_by_word: dict[int, list[int]] = {}
    all_ms_list: list[int] = []
    for lemma_id, response_ms in word_reviews:
        times_by_word.setdefault(lemma_id, []).append(response_ms)
        all_ms_list.append(response_ms)

    # Also include response times from non-target words for a robust global median
    global_count = (
        db.query(sa_func.count(ReviewLog.id))
        .filter(
            ReviewLog.response_ms.isnot(None),
            ReviewLog.response_ms > 0,
            ReviewLog.reviewed_at >= cutoff,
        )
        .scalar()
    ) or 0

    # If target words don't give us enough global data, fetch the full set
    if global_count > len(all_ms_list):
        all_reviews = (
            db.query(ReviewLog.response_ms)
            .filter(
                ReviewLog.response_ms.isnot(None),
                ReviewLog.response_ms > 0,
                ReviewLog.reviewed_at >= cutoff,
            )
            .all()
        )
        all_ms_list = [r.response_ms for r in all_reviews]

    if len(all_ms_list) < 10:
        return {}

    global_median = _median(all_ms_list)
    if global_median <= 0:
        return {}

    result: dict[int, float] = {}
    for lid, times in times_by_word.items():
        if len(times) < MIN_REVIEWS_FOR_FLUENCY:
            continue
        word_median = _median(times)
        if word_median > 0:
            result[lid] = global_median / word_median

    return result


def _median(values: list[int]) -> float:
    """Compute median of a list of integers."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    return float(sorted_vals[mid])
