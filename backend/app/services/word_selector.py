"""New word selection algorithm.

Picks optimal words to introduce next based on:
- Frequency rank (40%) — high-frequency words first
- Root familiarity (30%) — prefer words whose root is partially known
- Pattern coverage (10%) — fill morphological gaps
- Recency buffer (20%) — avoid words too similar to recently introduced ones

Also handles word introduction: creating FSRS cards, tracking root familiarity,
and scheduling initial reinforcement.
"""

import math
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import Root, Lemma, UserLemmaKnowledge, ReviewLog, Sentence
from app.services.fsrs_service import create_new_card


# Semantic categories that should NOT be introduced together
AVOID_SAME_SESSION = {
    "color", "number", "day", "month", "body_part",
    "family_member", "direction",
}

MAX_NEW_PER_SESSION = 5
DEFAULT_BATCH_SIZE = 3


def _frequency_score(frequency_rank: Optional[int], max_rank: int = 50000) -> float:
    """Higher score for lower (more frequent) rank. Log scale."""
    if frequency_rank is None or frequency_rank <= 0:
        return 0.3  # unknown frequency gets moderate score
    return 1.0 / math.log2(frequency_rank + 2)


def _root_familiarity_score(
    db: Session, root_id: Optional[int]
) -> tuple[float, int, int]:
    """Score how familiar the root is. Returns (score, known_count, total_count).

    Highest score when root is partially known (some siblings learned).
    Zero score for completely unknown roots (no siblings known).
    Lower score for fully known roots (all siblings already learned).
    """
    if root_id is None:
        return 0.0, 0, 0

    total = (
        db.query(func.count(Lemma.lemma_id))
        .filter(Lemma.root_id == root_id)
        .scalar() or 0
    )
    if total <= 1:
        return 0.0, 0, total

    known = (
        db.query(func.count(UserLemmaKnowledge.id))
        .join(Lemma)
        .filter(
            Lemma.root_id == root_id,
            UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]),
        )
        .scalar() or 0
    )

    if known == 0:
        return 0.0, 0, total
    if known >= total:
        return 0.1, known, total

    # Peak score when ~30-60% of root family is known
    ratio = known / total
    return ratio * (1.0 - ratio) * 4.0, known, total


def _days_since_introduced(db: Session, root_id: Optional[int]) -> float:
    """Average days since sibling words were introduced. Used for spacing."""
    if root_id is None:
        return 999.0

    latest = (
        db.query(func.max(UserLemmaKnowledge.introduced_at))
        .join(Lemma)
        .filter(Lemma.root_id == root_id)
        .scalar()
    )
    if latest is None:
        return 999.0

    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - latest
    return delta.total_seconds() / 86400


def get_root_family(db: Session, root_id: int) -> list[dict]:
    """Get all words from a root with their knowledge state."""
    lemmas = (
        db.query(Lemma)
        .filter(Lemma.root_id == root_id)
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .all()
    )
    result = []
    for lemma in lemmas:
        knowledge = lemma.knowledge
        result.append({
            "lemma_id": lemma.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "lemma_ar_bare": lemma.lemma_ar_bare,
            "gloss_en": lemma.gloss_en,
            "pos": lemma.pos,
            "transliteration": lemma.transliteration_ala_lc,
            "state": knowledge.knowledge_state if knowledge else "unknown",
        })
    return result


def select_next_words(
    db: Session,
    count: int = DEFAULT_BATCH_SIZE,
    exclude_lemma_ids: Optional[list[int]] = None,
) -> list[dict]:
    """Select the best words to introduce next.

    Returns a list of word dicts with scoring breakdown, sorted by score descending.
    """
    exclude = set(exclude_lemma_ids or [])

    # Get all lemmas that don't have knowledge records (never introduced)
    already_known = (
        db.query(UserLemmaKnowledge.lemma_id)
        .subquery()
    )

    candidates = (
        db.query(Lemma)
        .outerjoin(already_known, Lemma.lemma_id == already_known.c.lemma_id)
        .filter(already_known.c.lemma_id.is_(None))
        .filter(Lemma.lemma_id.notin_(exclude) if exclude else True)
        .all()
    )

    if not candidates:
        return []

    scored = []
    for lemma in candidates:
        freq_score = _frequency_score(lemma.frequency_rank)
        root_score, known_siblings, total_siblings = _root_familiarity_score(
            db, lemma.root_id
        )
        days = _days_since_introduced(db, lemma.root_id)

        # Slight boost for root family words introduced recently (1-3 days ago)
        # to cluster root family learning, but not on the same day
        recency_bonus = 0.0
        if 1.0 <= days <= 3.0 and root_score > 0:
            recency_bonus = 0.2

        total_score = (
            freq_score * 0.4
            + root_score * 0.3
            + recency_bonus * 0.2
            + 0.1  # base pattern score (placeholder until we track patterns)
        )

        scored.append({
            "lemma_id": lemma.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "lemma_ar_bare": lemma.lemma_ar_bare,
            "gloss_en": lemma.gloss_en,
            "pos": lemma.pos,
            "transliteration": lemma.transliteration_ala_lc,
            "frequency_rank": lemma.frequency_rank,
            "root_id": lemma.root_id,
            "root": lemma.root.root if lemma.root else None,
            "root_meaning": lemma.root.core_meaning_en if lemma.root else None,
            "score": round(total_score, 3),
            "score_breakdown": {
                "frequency": round(freq_score, 3),
                "root_familiarity": round(root_score, 3),
                "recency_bonus": round(recency_bonus, 3),
                "known_siblings": known_siblings,
                "total_siblings": total_siblings,
            },
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:count]


def introduce_word(db: Session, lemma_id: int) -> dict:
    """Mark a word as introduced, creating FSRS card and knowledge record.

    Returns the created knowledge record as dict.
    """
    lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
    if not lemma:
        raise ValueError(f"Lemma {lemma_id} not found")

    existing = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if existing:
        return {
            "lemma_id": lemma_id,
            "state": existing.knowledge_state,
            "already_known": True,
        }

    now = datetime.now(timezone.utc)
    card_data = create_new_card()

    knowledge = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state="learning",
        fsrs_card_json=card_data,
        introduced_at=now,
        last_reviewed=now,
        times_seen=1,
        times_correct=0,
        total_encounters=1,
        distinct_contexts=0,
        source="study",
    )
    db.add(knowledge)
    db.commit()

    root_family = []
    if lemma.root_id:
        root_family = get_root_family(db, lemma.root_id)

    return {
        "lemma_id": lemma_id,
        "lemma_ar": lemma.lemma_ar,
        "gloss_en": lemma.gloss_en,
        "state": "learning",
        "already_known": False,
        "introduced_at": now.isoformat(),
        "root": lemma.root.root if lemma.root else None,
        "root_meaning": lemma.root.core_meaning_en if lemma.root else None,
        "root_family": root_family,
    }


def get_sentence_difficulty_params(db: Session, lemma_id: int) -> dict:
    """Get sentence generation parameters scaled by word familiarity.

    Newly introduced words get shorter, simpler sentences.
    Well-known words get longer, more complex ones.
    """
    knowledge = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )

    if not knowledge or not knowledge.introduced_at:
        return {
            "max_words": 4,
            "difficulty_hint": "very simple",
            "use_only_top_known": True,
            "description": "Brand new word — shortest, simplest sentence",
        }

    times_seen = knowledge.times_seen or 0
    if knowledge.introduced_at.tzinfo is None:
        introduced = knowledge.introduced_at.replace(tzinfo=timezone.utc)
    else:
        introduced = knowledge.introduced_at
    hours_since = (datetime.now(timezone.utc) - introduced).total_seconds() / 3600

    # Stage 1: First session (< 2 hours, seen < 3 times)
    if hours_since < 2 and times_seen < 3:
        return {
            "max_words": 4,
            "difficulty_hint": "very simple",
            "use_only_top_known": True,
            "description": "Initial reinforcement — very short, very simple",
        }

    # Stage 2: Same day (< 24 hours, seen < 6 times)
    if hours_since < 24 and times_seen < 6:
        return {
            "max_words": 6,
            "difficulty_hint": "simple",
            "use_only_top_known": True,
            "description": "Same-day reinforcement — short and simple",
        }

    # Stage 3: First week (< 168 hours, seen < 10 times)
    if hours_since < 168 and times_seen < 10:
        return {
            "max_words": 8,
            "difficulty_hint": "beginner",
            "use_only_top_known": False,
            "description": "Early consolidation — moderate length",
        }

    # Stage 4: Established (> 1 week, seen 10+ times)
    return {
        "max_words": 12,
        "difficulty_hint": "intermediate",
        "use_only_top_known": False,
        "description": "Well-known — normal sentences",
    }
