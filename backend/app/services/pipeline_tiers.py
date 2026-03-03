"""Due-date tiered sentence pipeline allocation.

Assigns words to urgency tiers based on when they're next due for review.
Used by both the cron backfill (update_material.py) and the warm cache
(material_generator.py) to allocate sentence pipeline slots proportionally
to actual review demand instead of treating all words equally.
"""

import json as _json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import UserLemmaKnowledge


@dataclass
class TierConfig:
    """Configuration for a single due-date tier."""

    tier: int
    max_hours: Optional[float]  # None = unbounded (tier 4)
    backfill_target: int  # sentences to generate per word
    cap_floor: int  # minimum sentences to protect during cap enforcement


# Tier boundaries chosen so cron (every 3h) has multiple cycles to fill tier 3
# before words become tier 2, and tier 2 before they become tier 1.
TIER_CONFIGS = [
    TierConfig(tier=1, max_hours=12, backfill_target=3, cap_floor=2),
    TierConfig(tier=2, max_hours=36, backfill_target=2, cap_floor=1),
    TierConfig(tier=3, max_hours=72, backfill_target=1, cap_floor=0),
    TierConfig(tier=4, max_hours=None, backfill_target=0, cap_floor=0),
]

DEFAULT_TIER = TIER_CONFIGS[-1]


@dataclass
class WordTier:
    """A word with its computed tier assignment."""

    lemma_id: int
    due_dt: Optional[datetime]
    tier: int
    backfill_target: int
    cap_floor: int


def compute_word_tiers(
    db: Session,
    now: Optional[datetime] = None,
) -> list[WordTier]:
    """Compute due-date tiers for all active (non-suspended, non-encountered) words.

    Returns a list of WordTier sorted by due date (most urgent first).
    Words with no computable due date are assigned tier 4.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    knowledges = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state.notin_(["suspended", "encountered"]),
        )
        .all()
    )

    results: list[WordTier] = []
    for k in knowledges:
        due_dt = _extract_due_datetime(k)
        tier_config = _classify_tier(due_dt, now)
        results.append(
            WordTier(
                lemma_id=k.lemma_id,
                due_dt=due_dt,
                tier=tier_config.tier,
                backfill_target=tier_config.backfill_target,
                cap_floor=tier_config.cap_floor,
            )
        )

    results.sort(key=lambda w: (w.due_dt or datetime.max.replace(tzinfo=timezone.utc)))
    return results


def build_tier_lookup(word_tiers: list[WordTier]) -> dict[int, WordTier]:
    """Build a lemma_id -> WordTier dict for O(1) lookups."""
    return {wt.lemma_id: wt for wt in word_tiers}


def tier_summary(word_tiers: list[WordTier]) -> dict[int, int]:
    """Count words per tier for logging."""
    counts: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    for wt in word_tiers:
        counts[wt.tier] = counts.get(wt.tier, 0) + 1
    return counts


def _extract_due_datetime(k: UserLemmaKnowledge) -> Optional[datetime]:
    """Extract the due datetime from a ULK record, handling both
    acquisition and FSRS states. Returns timezone-aware UTC datetime."""
    if k.knowledge_state == "acquiring":
        if k.acquisition_next_due:
            due_dt = k.acquisition_next_due
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            return due_dt
        return None

    if not k.fsrs_card_json:
        return None

    try:
        card = (
            k.fsrs_card_json
            if isinstance(k.fsrs_card_json, dict)
            else _json.loads(k.fsrs_card_json)
        )
    except (TypeError, ValueError):
        return None

    due_str = card.get("due", "")
    if not due_str:
        return None

    try:
        due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        return due_dt
    except (ValueError, TypeError):
        return None


def _classify_tier(
    due_dt: Optional[datetime],
    now: datetime,
) -> TierConfig:
    """Classify a word into a tier based on its due datetime."""
    if due_dt is None:
        return DEFAULT_TIER

    hours_until_due = (due_dt - now).total_seconds() / 3600
    # Overdue words (negative hours) always go to tier 1
    if hours_until_due <= 0:
        return TIER_CONFIGS[0]

    for config in TIER_CONFIGS:
        if config.max_hours is None:
            return config
        if hours_until_due <= config.max_hours:
            return config

    return DEFAULT_TIER
