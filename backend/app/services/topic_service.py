"""Topical learning cycle management.

Auto-selects the best topic based on available encountered words,
tracks progress, and advances when a topic is exhausted.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import Lemma, LearnerSettings, UserLemmaKnowledge

logger = logging.getLogger(__name__)

DOMAINS = [
    "school", "food", "family", "work", "travel", "home", "nature", "body",
    "time", "religion", "commerce", "media", "politics", "emotions", "social",
    "daily_routine", "language", "science", "military", "law",
]

MAX_TOPIC_BATCH = 15
MIN_TOPIC_WORDS = 5


def get_settings(db: Session) -> LearnerSettings:
    """Get or create the singleton LearnerSettings row."""
    settings = db.query(LearnerSettings).first()
    if not settings:
        settings = LearnerSettings(id=1)
        db.add(settings)
        db.flush()
    return settings


def get_available_topics(db: Session) -> list[dict]:
    """Return all topics with available encountered word counts.

    A word is "available" if it's encountered or has no ULK, is canonical,
    and has a thematic_domain set.
    """
    introduced_ids = {
        r[0] for r in
        db.query(UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.knowledge_state != "encountered")
        .all()
    }

    rows = (
        db.query(Lemma.thematic_domain, func.count(Lemma.lemma_id))
        .filter(
            Lemma.thematic_domain.isnot(None),
            Lemma.canonical_lemma_id.is_(None),
            Lemma.lemma_id.notin_(introduced_ids) if introduced_ids else True,
        )
        .group_by(Lemma.thematic_domain)
        .all()
    )
    domain_available = {domain: count for domain, count in rows}

    learned_rows = (
        db.query(Lemma.thematic_domain, func.count(Lemma.lemma_id))
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(
            Lemma.thematic_domain.isnot(None),
            UserLemmaKnowledge.knowledge_state.in_(["acquiring", "learning", "known"]),
        )
        .group_by(Lemma.thematic_domain)
        .all()
    )
    domain_learned = {domain: count for domain, count in learned_rows}

    result = []
    for domain in DOMAINS:
        available = domain_available.get(domain, 0)
        learned = domain_learned.get(domain, 0)
        result.append({
            "domain": domain,
            "available_words": available,
            "learned_words": learned,
            "eligible": available >= MIN_TOPIC_WORDS,
        })

    result.sort(key=lambda t: (-t["eligible"], -t["available_words"]))
    return result


def select_best_topic(db: Session, exclude_current: bool = True) -> Optional[str]:
    """Pick the best next topic: highest available word count among eligible topics."""
    settings = get_settings(db)
    topics = get_available_topics(db)

    for t in topics:
        if not t["eligible"]:
            continue
        if exclude_current and t["domain"] == settings.active_topic:
            continue
        return t["domain"]

    if exclude_current:
        return select_best_topic(db, exclude_current=False)

    return None


def ensure_active_topic(db: Session) -> Optional[str]:
    """Ensure there is an active topic. Auto-select if none or exhausted.

    Called before word selection. Returns the active domain, or None.
    """
    settings = get_settings(db)

    needs_new = False
    if settings.active_topic is None:
        needs_new = True
    elif (settings.words_introduced_in_topic or 0) >= MAX_TOPIC_BATCH:
        needs_new = True
    else:
        topics = get_available_topics(db)
        current = next((t for t in topics if t["domain"] == settings.active_topic), None)
        if not current or current["available_words"] == 0:
            needs_new = True

    if needs_new:
        _advance_topic(db, settings)

    return settings.active_topic


def _advance_topic(db: Session, settings: LearnerSettings) -> None:
    """Switch to the next best topic, archiving the old one."""
    now = datetime.now(timezone.utc)

    if settings.active_topic:
        history = settings.topic_history_json or []
        history.append({
            "topic": settings.active_topic,
            "started": settings.topic_started_at.isoformat() if settings.topic_started_at else None,
            "ended": now.isoformat(),
            "words_introduced": settings.words_introduced_in_topic or 0,
        })
        settings.topic_history_json = history

    new_topic = select_best_topic(db, exclude_current=True)
    settings.active_topic = new_topic
    settings.topic_started_at = now if new_topic else None
    settings.words_introduced_in_topic = 0
    db.flush()
    logger.info(f"Topic advanced to: {new_topic}")


def record_introduction(db: Session, count: int = 1) -> None:
    """Increment the words_introduced_in_topic counter."""
    settings = get_settings(db)
    settings.words_introduced_in_topic = (settings.words_introduced_in_topic or 0) + count
    db.flush()


def set_topic(db: Session, domain: str) -> LearnerSettings:
    """Manually set the active topic."""
    if domain not in DOMAINS:
        raise ValueError(f"Unknown domain: {domain}")

    settings = get_settings(db)
    if settings.active_topic and settings.active_topic != domain:
        _advance_topic(db, settings)

    settings.active_topic = domain
    settings.topic_started_at = datetime.now(timezone.utc)
    settings.words_introduced_in_topic = 0
    db.flush()
    return settings
