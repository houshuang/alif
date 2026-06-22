"""Frequency-core review lane helpers.

The aggressive acquisition experiment keeps all active acquisition work in the
main lane, while low-frequency artifact debt is sampled through a slow lane.
This module centralizes the classification so stats and session building do not
drift apart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import FrequencyCoreEntry, Lemma, UserLemmaKnowledge
from app.services.sentence_validator import _is_function_word


MAIN_LANE_MAX_RANK = 5000
SLOW_LANE_SESSION_FRACTION = 0.10
ARTIFACT_SOURCES = {"textbook_scan", "book", "story_import", "scaffold", "book_ocr"}
LEARNED_STATES = {"known", "learning"}
PIPELINE_STATES = LEARNED_STATES | {"acquiring", "lapsed", "encountered"}
UNKNOWN_FREQUENCY_RANK = 1_000_000_000
LOW_PRIORITY_FREQUENCY_RANK = 20_000
LOW_PRIORITY_EXEMPT_SOURCES = {"duolingo", "avp_a1", "frequency_core"}


@dataclass(frozen=True)
class DueLaneSnapshot:
    due_ids: set[int]
    main_due_ids: set[int]
    slow_due_ids: set[int]
    fsrs_due_ids: set[int]
    acquisition_due_ids: set[int]


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fsrs_due_at(knowledge: UserLemmaKnowledge) -> datetime | None:
    raw = knowledge.fsrs_card_json
    if not raw:
        return None
    if isinstance(raw, dict):
        card = raw
    else:
        try:
            card = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(card, dict):
        return None
    return _parse_dt(card.get("due"))


def frequency_core_ranks(db: Session, lemma_ids: Iterable[int]) -> dict[int, int]:
    ids = {lid for lid in lemma_ids if lid is not None}
    if not ids:
        return {}
    rows = (
        db.query(FrequencyCoreEntry.lemma_id, func.min(FrequencyCoreEntry.core_rank))
        .filter(
            FrequencyCoreEntry.lemma_id.in_(ids),
            FrequencyCoreEntry.excluded_reason.is_(None),
        )
        .group_by(FrequencyCoreEntry.lemma_id)
        .all()
    )
    return {lemma_id: rank for lemma_id, rank in rows if lemma_id is not None and rank is not None}


def effective_frequency_ranks(db: Session, lemma_ids: Iterable[int]) -> dict[int, int]:
    """Return the best available rank for each lemma, lower means more frequent.

    Frequency-core rank is preferred when present, otherwise the lemma's own
    frequency rank is used. Missing ranks are represented by a very large
    sentinel so callers can still sort deterministically.
    """
    ids = {lid for lid in lemma_ids if lid is not None}
    if not ids:
        return {}

    core = frequency_core_ranks(db, ids)
    lemma_rows = (
        db.query(Lemma.lemma_id, Lemma.frequency_rank)
        .filter(Lemma.lemma_id.in_(ids))
        .all()
    )
    ranks: dict[int, int] = {}
    for lemma_id, frequency_rank in lemma_rows:
        candidates = [
            rank
            for rank in (core.get(lemma_id), frequency_rank)
            if rank is not None and rank > 0
        ]
        ranks[lemma_id] = min(candidates) if candidates else UNKNOWN_FREQUENCY_RANK
    for lid in ids:
        ranks.setdefault(lid, core.get(lid) or UNKNOWN_FREQUENCY_RANK)
    return ranks


def frequency_priority_weight(rank: int | None) -> float:
    """Scoring multiplier for due-word selection."""
    if rank is None or rank >= UNKNOWN_FREQUENCY_RANK:
        return 0.70
    if rank <= 500:
        return 2.20
    if rank <= 1000:
        return 2.00
    if rank <= 2000:
        return 1.75
    if rank <= 5000:
        return 1.50
    if rank <= 10000:
        return 1.20
    if rank <= 20000:
        return 1.00
    if rank <= 50000:
        return 0.85
    return 0.70


def frequency_priority_multiplier(
    lemma_ids: Iterable[int],
    frequency_rank_map: dict[int, int] | None,
) -> float:
    ranks = frequency_rank_map or {}
    weights = [frequency_priority_weight(ranks.get(lid)) for lid in lemma_ids]
    if not weights:
        return 1.0
    return sum(weights) / len(weights)


def frequency_priority_sort_key(
    lemma_id: int,
    frequency_rank_map: dict[int, int] | None,
    overdue_days_map: dict[int, float] | None = None,
) -> tuple[int, float, int]:
    """Sort high-frequency due words first, then older debt."""
    rank = (frequency_rank_map or {}).get(lemma_id, UNKNOWN_FREQUENCY_RANK)
    overdue = (overdue_days_map or {}).get(lemma_id, 0.0)
    return rank, -overdue, lemma_id


def is_low_priority_lemma(lemma: Lemma | None, core_rank: int | None = None) -> bool:
    """Return True for obscure/unranked words that should spend less bandwidth.

    The merged frequency-core rank is the authoritative frequency signal: a word
    inside the main lane (``core_rank <= MAIN_LANE_MAX_RANK``) is never
    low-priority, even when its per-lemma ``frequency_rank`` is unset or carries a
    sparse single-source value. ``lemma.frequency_rank`` comes from the CAMeL-only
    list (~1/3 of lemmas are NULL, and book/quran/wiktionary imports often hold a
    raw rank well past the threshold), so consulting it alone throttled genuinely
    frequent words. Mirrors the core_rank check in ``is_main_lane_word``.
    """
    if lemma is None:
        return True
    if core_rank is not None and core_rank <= MAIN_LANE_MAX_RANK:
        return False
    if lemma.source in LOW_PRIORITY_EXEMPT_SOURCES:
        return False
    rank = lemma.frequency_rank
    return rank is None or rank <= 0 or rank > LOW_PRIORITY_FREQUENCY_RANK


def is_artifact_source(ulk_source: str | None, lemma_source: str | None) -> bool:
    return (ulk_source in ARTIFACT_SOURCES) or (lemma_source in ARTIFACT_SOURCES)


def is_main_lane_word(
    knowledge: UserLemmaKnowledge,
    lemma: Lemma | None,
    core_rank: int | None = None,
) -> bool:
    """Return True when a due word should count against the main lane."""
    if knowledge.knowledge_state == "acquiring":
        return True
    if core_rank is not None and core_rank <= MAIN_LANE_MAX_RANK:
        return True
    if lemma and lemma.frequency_rank is not None and lemma.frequency_rank <= MAIN_LANE_MAX_RANK:
        return True
    if not is_artifact_source(knowledge.source, lemma.source if lemma else None):
        return True
    return False


def select_slow_lane_sample(
    slow_due_ids: set[int],
    overdue_days_map: dict[int, float],
    session_limit: int,
    frequency_rank_map: dict[int, int] | None = None,
) -> set[int]:
    if not slow_due_ids or session_limit <= 0:
        return set()
    budget = max(1, int(session_limit * SLOW_LANE_SESSION_FRACTION))
    ordered = sorted(
        slow_due_ids,
        key=lambda lid: frequency_priority_sort_key(lid, frequency_rank_map, overdue_days_map),
    )
    return set(ordered[:budget])


def due_lane_snapshot(db: Session, now: datetime | None = None) -> DueLaneSnapshot:
    """Classify currently due non-function words into main and slow lanes."""
    now = now or datetime.now(timezone.utc)
    knowledges = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state.notin_(["suspended", "encountered"]))
        .all()
    )
    lemma_ids = {k.lemma_id for k in knowledges}
    lemmas = {
        l.lemma_id: l
        for l in db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
    } if lemma_ids else {}
    core_ranks = frequency_core_ranks(db, lemma_ids)

    due_ids: set[int] = set()
    fsrs_due_ids: set[int] = set()
    acquisition_due_ids: set[int] = set()
    main_due_ids: set[int] = set()
    slow_due_ids: set[int] = set()

    for k in knowledges:
        lemma = lemmas.get(k.lemma_id)
        if lemma and lemma.lemma_ar_bare and _is_function_word(lemma.lemma_ar_bare):
            continue
        due = False
        if k.knowledge_state == "acquiring":
            due_dt = _parse_dt(k.acquisition_next_due)
            due = due_dt is not None and due_dt <= now
            if due:
                acquisition_due_ids.add(k.lemma_id)
        else:
            due_dt = _fsrs_due_at(k)
            due = due_dt is not None and due_dt <= now
            if due:
                fsrs_due_ids.add(k.lemma_id)
        if not due:
            continue
        due_ids.add(k.lemma_id)
        if is_main_lane_word(k, lemma, core_ranks.get(k.lemma_id)):
            main_due_ids.add(k.lemma_id)
        else:
            slow_due_ids.add(k.lemma_id)

    return DueLaneSnapshot(
        due_ids=due_ids,
        main_due_ids=main_due_ids,
        slow_due_ids=slow_due_ids,
        fsrs_due_ids=fsrs_due_ids,
        acquisition_due_ids=acquisition_due_ids,
    )
