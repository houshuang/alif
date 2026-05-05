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
) -> set[int]:
    if not slow_due_ids or session_limit <= 0:
        return set()
    budget = max(1, int(session_limit * SLOW_LANE_SESSION_FRACTION))
    ordered = sorted(
        slow_due_ids,
        key=lambda lid: (overdue_days_map.get(lid, 0.0), lid),
        reverse=True,
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
