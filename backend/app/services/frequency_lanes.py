"""Frequency-core review lane helpers.

The aggressive acquisition experiment keeps all active acquisition work in the
main lane, while low-frequency artifact debt is sampled through a slow lane.
This module centralizes the classification so stats and session building do not
drift apart.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.services.attention_policy import maintenance_clause
from app.models import BookFrequencyEntry, FrequencyCoreEntry, Lemma, UserLemmaKnowledge
from app.services.sentence_validator import is_function_word_lemma


MAIN_LANE_MAX_RANK = 5000
SLOW_LANE_SESSION_FRACTION = 0.10
ARTIFACT_SOURCES = {"textbook_scan", "book", "story_import", "scaffold", "book_ocr", "bookifier", "dragoman"}
LEARNED_STATES = {"known", "learning"}
PIPELINE_STATES = LEARNED_STATES | {"acquiring", "lapsed", "encountered"}
UNKNOWN_FREQUENCY_RANK = 1_000_000_000
LOW_PRIORITY_FREQUENCY_RANK = 20_000
LOW_PRIORITY_EXEMPT_SOURCES = {"duolingo", "avp_a1", "frequency_core"}

# book_frequency_v1 (2026-11-25): rank by running-text count in real books
# (``book_frequency_entries``). The fused core ranks every lemma already in the
# vocabulary above every missing one, so it cannot tell a frequent due word from
# a rare one. ``ALIF_BOOK_FREQUENCY_LANES=0`` restores the core-rank rules.
BOOK_FREQUENCY_ENV = "ALIF_BOOK_FREQUENCY_LANES"
BOOK_FREQUENCY_VERSION = "book_frequency_v1"
BOOK_MAIN_LANE_MAX_RANK = 2000
# A word absent from the book sample sorts after every observed book word.
BOOK_ABSENT_RANK = LOW_PRIORITY_FREQUENCY_RANK + 1
# The learner's own current reading keeps a rare word in the main lane.
BOOK_MAIN_LANE_SOURCES = {"snap", "preread"}
BOOK_MAIN_LANE_REASON_SUFFIXES = ("reading_target", "reading_recurrence", "learner_pick")


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


def book_frequency_active(db: Session) -> bool:
    """True when the book list is switched on and has been built."""
    if os.environ.get(BOOK_FREQUENCY_ENV, "1").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    return db.query(BookFrequencyEntry.id).limit(1).first() is not None


def book_frequency_ranks(db: Session, lemma_ids: Iterable[int] | None = None) -> dict[int, int]:
    """Best book rank per lemma; all linked lemmas when ``lemma_ids`` is None."""
    query = db.query(BookFrequencyEntry.lemma_id, func.min(BookFrequencyEntry.rank)).filter(
        BookFrequencyEntry.lemma_id.isnot(None)
    )
    if lemma_ids is not None:
        ids = {lid for lid in lemma_ids if lid is not None}
        if not ids:
            return {}
        query = query.filter(BookFrequencyEntry.lemma_id.in_(ids))
    return dict(query.group_by(BookFrequencyEntry.lemma_id).all())


@dataclass(frozen=True)
class LaneRanks:
    """The rank evidence one lane decision needs, fetched once per batch."""

    core: dict[int, int] = field(default_factory=dict)
    book: dict[int, int] = field(default_factory=dict)
    book_active: bool = False

    def is_main(self, knowledge: UserLemmaKnowledge, lemma: Lemma | None) -> bool:
        if self.book_active:
            return is_book_main_lane_word(knowledge, self.book.get(knowledge.lemma_id))
        return is_main_lane_word(knowledge, lemma, self.core.get(knowledge.lemma_id))


def lane_ranks(db: Session, lemma_ids: Iterable[int]) -> LaneRanks:
    ids = {lid for lid in lemma_ids if lid is not None}
    if book_frequency_active(db):
        return LaneRanks(book=book_frequency_ranks(db, ids), book_active=True)
    return LaneRanks(core=frequency_core_ranks(db, ids))


def is_book_main_lane_word(knowledge: UserLemmaKnowledge, book_rank: int | None) -> bool:
    """Main lane under book_frequency_v1: acquiring words, the top book band,
    and words the learner's own reading asked for."""
    if knowledge.knowledge_state == "acquiring":
        return True
    if book_rank is not None and book_rank <= BOOK_MAIN_LANE_MAX_RANK:
        return True
    if knowledge.source in BOOK_MAIN_LANE_SOURCES:
        return True
    reason = knowledge.attention_reason or ""
    return reason.endswith(BOOK_MAIN_LANE_REASON_SUFFIXES)


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

    The minimum positive core or surface-frequency rank is used. Missing ranks are represented by a very large
    sentinel so callers can still sort deterministically.
    """
    ids = {lid for lid in lemma_ids if lid is not None}
    if not ids:
        return {}

    if book_frequency_active(db):
        book = book_frequency_ranks(db, ids)
        return {lid: book.get(lid, BOOK_ABSENT_RANK) for lid in ids}

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
        .filter(maintenance_clause(), UserLemmaKnowledge.knowledge_state.notin_(["suspended", "encountered"]))
        .all()
    )
    lemma_ids = {k.lemma_id for k in knowledges}
    lemmas = {
        l.lemma_id: l
        for l in db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
    } if lemma_ids else {}
    ranks = lane_ranks(db, lemma_ids)

    due_ids: set[int] = set()
    fsrs_due_ids: set[int] = set()
    acquisition_due_ids: set[int] = set()
    main_due_ids: set[int] = set()
    slow_due_ids: set[int] = set()

    for k in knowledges:
        lemma = lemmas.get(k.lemma_id)
        if lemma and is_function_word_lemma(
            lemma.lemma_ar_bare, lemma.function_word_override
        ):
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
        if ranks.is_main(k, lemma):
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


BOOK_PROGRESS_BANDS = (500, 1000, 2000, 3000, 5000)
FIRM_STABILITY_DAYS = 30.0


def book_frequency_progress(db: Session) -> dict:
    """Learner state of every book-list word, per rank band.

    ``firm`` is a known word whose FSRS stability is at least 30 days. A row
    with no lemma counts as ``not_in_alif``: the band total is the list's, so a
    missing word lowers the percentage instead of disappearing.
    """
    entries = db.query(BookFrequencyEntry.rank, BookFrequencyEntry.lemma_id).filter(
        BookFrequencyEntry.rank <= BOOK_PROGRESS_BANDS[-1]
    ).all()
    lemma_ids = {lid for _, lid in entries if lid is not None}
    knowledge = {
        k.lemma_id: k
        for k in db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id.in_(lemma_ids))
    } if lemma_ids else {}

    def state(lemma_id: int | None) -> str:
        if lemma_id is None:
            return "not_in_alif"
        k = knowledge.get(lemma_id)
        if k is None or k.knowledge_state in (None, "new", "encountered"):
            return "not_started"
        if k.knowledge_state == "suspended":
            return "suspended"
        if k.knowledge_state == "acquiring":
            return "acquiring"
        card = k.fsrs_card_json
        if isinstance(card, str):
            try:
                card = json.loads(card)
            except ValueError:
                card = None
        stability = (card or {}).get("stability") or 0 if isinstance(card, dict) else 0
        if k.knowledge_state == "known" and stability >= FIRM_STABILITY_DAYS:
            return "firm"
        return "in_review"

    bands = []
    lower = 0
    for upper in BOOK_PROGRESS_BANDS:
        counts = {name: 0 for name in (
            "firm", "in_review", "acquiring", "suspended", "not_started", "not_in_alif")}
        for rank, lemma_id in entries:
            if lower < rank <= upper:
                counts[state(lemma_id)] += 1
        total = sum(counts.values())
        bands.append({
            "from_rank": lower + 1, "to_rank": upper, "total": total, **counts,
            "firm_pct": round(100 * counts["firm"] / total, 1) if total else 0.0,
        })
        lower = upper
    return {"version": BOOK_FREQUENCY_VERSION, "active": book_frequency_active(db), "bands": bands}
