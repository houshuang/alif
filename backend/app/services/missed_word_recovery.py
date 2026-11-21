"""Recently missed established words that still need sentence evidence.

`missed_word_recovery_v1` (experiment log 2026-11-20). After a red or yellow
miss on a word that already has an FSRS card, FSRS schedules a short interval,
but the due date then competes with the whole overdue pile and the word used
to return after a median of two weeks. This module names the words whose miss
is still open so the selector can serve them on their due date and the
due-word menu can write sentences for them first.

A miss is open until the word has been answered correctly in sentences
`MISSED_RECOVERY_REQUIRED_SUCCESSES` times since the miss. Word cards never
close a miss: a correct one comes minutes after the answer was shown. A missed
word card is evidence the word is weaker than one miss suggests, so it adds
one required success.

`fragment_followup_v1`: a scheduled follow-up may be a short phrase instead of
a sentence (`review_mode="fragment"`). A correct phrase counts as a success,
but the last required success must come from a full sentence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import ReviewLog

MISSED_RECOVERY_VERSION = "missed_word_recovery_v1"
FRAGMENT_REVIEW_MODE = "fragment"
MISSED_RECOVERY_WINDOW_DAYS = 30
MISSED_RECOVERY_REQUIRED_SUCCESSES = 2
MISSED_RECOVERY_WORD_CARD_MISS_EXTRA = 1
# Share of a session's base cards that may be reserved for open misses and for
# words still being learned. The rest stays with the ordinary dense selection.
MISSED_RECOVERY_SLOT_FRACTION = 0.3
ACQUIRING_RESERVED_SLOT_FRACTION = 0.3


@dataclass(frozen=True)
class OpenMiss:
    lemma_id: int
    missed_at: datetime
    rating: int
    successes: int
    required_successes: int
    word_card_misses: int
    # Sentence or phrase misses since the word last had no open miss, this one
    # included: 1 is a fresh miss, 2 means one follow-up was missed as well.
    misses_in_run: int = 1

    @property
    def remaining(self) -> int:
        return self.required_successes - self.successes


def reserved_slots(limit: int, fraction: float) -> int:
    return max(1, round(limit * fraction)) if limit > 0 else 0


def _required_successes(entry: dict) -> int:
    return MISSED_RECOVERY_REQUIRED_SUCCESSES + (
        MISSED_RECOVERY_WORD_CARD_MISS_EXTRA if entry["word_card_misses"] else 0
    )


def _successes(entry: dict) -> int:
    # Phrases can supply every success except the last one.
    return entry["sentence_successes"] + min(
        entry["fragment_successes"], _required_successes(entry) - 1
    )


def open_misses(
    db: Session, now: datetime, lemma_id: int | None = None
) -> dict[int, OpenMiss]:
    """Open misses by canonical lemma id, from non-acquisition review rows."""
    from app.services.form_recovery_service import is_form_recovery_protected_log

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = (now - timedelta(days=MISSED_RECOVERY_WINDOW_DAYS)).replace(tzinfo=None)
    query = db.query(ReviewLog).filter(
        ReviewLog.reviewed_at >= cutoff,
        ReviewLog.reviewed_at <= now.replace(tzinfo=None),
        or_(
            ReviewLog.is_acquisition.is_(False),
            ReviewLog.is_acquisition.is_(None),
        ),
    )
    if lemma_id is not None:
        query = query.filter(ReviewLog.lemma_id == lemma_id)
    rows = query.order_by(ReviewLog.reviewed_at, ReviewLog.id).all()
    state: dict[int, dict] = {}
    for row in rows:
        entry = state.get(row.lemma_id)
        if row.review_mode == "quiz":
            if entry is not None and row.rating == 1:
                entry["word_card_misses"] += 1
            continue
        if row.rating <= 2:
            if is_form_recovery_protected_log(row):
                continue
            reviewed_at = row.reviewed_at
            if reviewed_at.tzinfo is None:
                reviewed_at = reviewed_at.replace(tzinfo=timezone.utc)
            still_open = entry is not None and _successes(entry) < _required_successes(entry)
            state[row.lemma_id] = {
                "missed_at": reviewed_at,
                "rating": row.rating,
                "sentence_successes": 0,
                "fragment_successes": 0,
                "word_card_misses": 0,
                "misses_in_run": entry["misses_in_run"] + 1 if still_open else 1,
            }
        elif entry is not None:
            if row.review_mode == FRAGMENT_REVIEW_MODE:
                entry["fragment_successes"] += 1
            else:
                entry["sentence_successes"] += 1

    result: dict[int, OpenMiss] = {}
    for lemma_id, entry in state.items():
        required = _required_successes(entry)
        entry["successes"] = _successes(entry)
        if entry["successes"] >= required:
            continue
        result[lemma_id] = OpenMiss(
            lemma_id=lemma_id,
            missed_at=entry["missed_at"],
            rating=entry["rating"],
            successes=entry["successes"],
            required_successes=required,
            word_card_misses=entry["word_card_misses"],
            misses_in_run=entry["misses_in_run"],
        )
    return result


def recovery_order(misses: dict[int, OpenMiss]) -> list[int]:
    """Words with no success since the miss first, then the newest miss."""
    return sorted(
        misses,
        key=lambda lid: (
            misses[lid].successes,
            -misses[lid].word_card_misses,
            -misses[lid].missed_at.timestamp(),
            lid,
        ),
    )
