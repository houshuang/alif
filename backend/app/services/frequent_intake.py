"""Intake allowance for frequent words (``frequent_word_intake_v1``).

The ordinary true-new budget and leech reintroduction both close while Box 1
is loaded. Learner picks (Snap, pre-read, textbook scan) bypass those budgets
and keep Box 1 loaded, so between 2026-11-15 and 2026-11-25 every new word was
a pick and no leech was reintroduced: 83 suspended words inside the top 2,000
book ranks had waited since July.

A frequent word is one with book rank <= ``BOOK_MAIN_LANE_MAX_RANK`` or an
open reading target in a textbook-completion program. Frequent words have
their own daily allowance, measured against their own Box-1 load, so reading
picks cannot shut them out. Everything else keeps the ordinary budget.

Active while the book list is active and ``ALIF_FREQUENT_WORD_INTAKE`` is not
``0``.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Lemma, ReadingTarget, UserLemmaKnowledge
from app.services.attention_policy import LEARNER_PICK_SOURCES, maintenance_clause
from app.services.frequency_lanes import (
    BOOK_MAIN_LANE_MAX_RANK,
    book_frequency_active,
    book_frequency_ranks,
)

FREQUENT_INTAKE_ENV = "ALIF_FREQUENT_WORD_INTAKE"
FREQUENT_INTAKE_VERSION = "frequent_word_intake_v1"
FREQUENT_DAILY_NEW = 12
FREQUENT_DAILY_REINTRO = 6
FREQUENT_NEW_PER_PASS = 4
# New and reintroduced frequent words share this Box-1 ceiling. It is the real
# throttle: intake follows how fast the learner clears frequent words.
FREQUENT_BOX1_LIMIT = 40
FREQUENT_LEECH_MAX_DELAY_DAYS = 3
TEXTBOOK_TARGET_PROGRAMS = frozenset({"alkitaab_part1", "madinah_book1"})


def frequent_intake_active(db: Session) -> bool:
    if os.environ.get(FREQUENT_INTAKE_ENV, "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return book_frequency_active(db)


def textbook_target_ids(db: Session, lemma_ids: set[int] | None = None) -> set[int]:
    query = db.query(ReadingTarget.lemma_id).filter(
        ReadingTarget.retired_at.is_(None),
        ReadingTarget.program.in_(TEXTBOOK_TARGET_PROGRAMS),
    )
    if lemma_ids is not None:
        if not lemma_ids:
            return set()
        query = query.filter(ReadingTarget.lemma_id.in_(lemma_ids))
    return {lid for lid, in query}


def frequent_lemma_ids(db: Session, lemma_ids: set[int] | None = None) -> set[int]:
    """Lemmas the allowance covers; restricted to ``lemma_ids`` when given."""
    ranked = {
        lid
        for lid, rank in book_frequency_ranks(db, lemma_ids).items()
        if rank <= BOOK_MAIN_LANE_MAX_RANK
    }
    return ranked | textbook_target_ids(db, lemma_ids)


def is_frequent_word(db: Session, lemma_id: int) -> bool:
    return lemma_id in frequent_lemma_ids(db, {lemma_id})


def _acquiring_rows(db: Session):
    return (
        db.query(UserLemmaKnowledge)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            maintenance_clause(),
            UserLemmaKnowledge.knowledge_state == "acquiring",
            Lemma.word_category.is_(None)
            | Lemma.word_category.notin_(["proper_name", "onomatopoeia"]),
        )
    )


def frequent_box1_load(db: Session) -> int:
    ids = {
        lid
        for lid, in _acquiring_rows(db)
        .filter(UserLemmaKnowledge.acquisition_box == 1)
        .with_entities(UserLemmaKnowledge.lemma_id)
    }
    return len(frequent_lemma_ids(db, ids))


def _started_today(db: Session, today_start: datetime, reintro: bool) -> int:
    from app.services.acquisition_service import (
        ACQUISITION_EPISODE_LEECH_REINTRO,
        true_new_acquisition_episode_filter,
    )

    query = db.query(UserLemmaKnowledge.lemma_id).filter(
        UserLemmaKnowledge.acquisition_started_at >= today_start
    )
    if reintro:
        query = query.filter(
            UserLemmaKnowledge.acquisition_episode_kind == ACQUISITION_EPISODE_LEECH_REINTRO
        )
    else:
        query = query.filter(
            true_new_acquisition_episode_filter(),
            UserLemmaKnowledge.source.is_(None)
            | UserLemmaKnowledge.source.notin_(LEARNER_PICK_SOURCES),
        )
    ids = {lid for lid, in query}
    return len(frequent_lemma_ids(db, ids))


def _room(db: Session, now: datetime, daily: int, reintro: bool) -> int:
    if not frequent_intake_active(db):
        return 0
    from app.services.acquisition_service import (
        RECOVERY_FSRS_MAIN_DUE_LIMIT,
        _main_fsrs_due_count,
    )

    box1_room = FREQUENT_BOX1_LIMIT - frequent_box1_load(db)
    if box1_room <= 0:
        return 0
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_room = daily - _started_today(db, today_start, reintro)
    if daily_room <= 0:
        return 0
    if _main_fsrs_due_count(db, now) >= RECOVERY_FSRS_MAIN_DUE_LIMIT:
        return 0
    return min(box1_room, daily_room)


def frequent_new_room(db: Session, now: datetime) -> int:
    """True-new frequent words that may still start today."""
    return _room(db, now, FREQUENT_DAILY_NEW, reintro=False)


def frequent_reintro_room(db: Session, now: datetime) -> int:
    """Suspended frequent words that may still be reintroduced today."""
    return _room(db, now, FREQUENT_DAILY_REINTRO, reintro=True)


def admits_new_frequent_word(db: Session, lemma_id: int, now: datetime) -> bool:
    """Whether the allowance admits this word after the ordinary budget refused it."""
    return (
        frequent_intake_active(db)
        and is_frequent_word(db, lemma_id)
        and frequent_new_room(db, now) > 0
    )


def interleave_frequent(db: Session, ordered_ids: list[int]) -> list[int]:
    """Alternate frequent and other words, keeping each group's own order.

    Used for the reserved acquiring cards: frequent words and the learner's
    reading picks each get every second card instead of queueing by overdue
    time alone.
    """
    if not ordered_ids or not frequent_intake_active(db):
        return ordered_ids
    frequent = frequent_lemma_ids(db, set(ordered_ids))
    first = [lid for lid in ordered_ids if lid in frequent]
    second = [lid for lid in ordered_ids if lid not in frequent]
    merged: list[int] = []
    for index in range(max(len(first), len(second))):
        if index < len(first):
            merged.append(first[index])
        if index < len(second):
            merged.append(second[index])
    return merged

