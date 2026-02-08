import json
from datetime import datetime, timezone
from typing import Optional

from fsrs import Scheduler, Card, Rating, State
from sqlalchemy.orm import Session

from app.models import Lemma, UserLemmaKnowledge, ReviewLog


scheduler = Scheduler()

STATE_MAP = {
    State.Learning: "learning",
    State.Review: "known",
    State.Relearning: "lapsed",
}

RATING_MAP = {
    1: Rating.Again,
    2: Rating.Hard,
    3: Rating.Good,
    4: Rating.Easy,
}


def create_new_card() -> dict:
    card = Card()
    return card.to_dict()


def get_due_cards(db: Session, limit: int = 10) -> list[dict]:
    now = datetime.now(timezone.utc)
    knowledges = (
        db.query(UserLemmaKnowledge)
        .join(Lemma)
        .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
        .all()
    )

    due_items = []
    for k in knowledges:
        card_data = k.fsrs_card_json
        if isinstance(card_data, str):
            card_data = json.loads(card_data)
        due_str = card_data.get("due")
        if due_str:
            due_dt = datetime.fromisoformat(due_str)
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            if due_dt <= now:
                due_items.append((k, due_dt))

    due_items.sort(key=lambda x: x[1])
    results = []
    for k, due_dt in due_items[:limit]:
        lemma = k.lemma
        results.append({
            "lemma_id": lemma.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "lemma_ar_bare": lemma.lemma_ar_bare,
            "gloss_en": lemma.gloss_en,
            "audio_url": lemma.audio_url,
            "knowledge_state": k.knowledge_state,
            "due": due_dt.isoformat(),
        })
    return results


def submit_review(
    db: Session,
    lemma_id: int,
    rating_int: int,
    response_ms: Optional[int] = None,
    session_id: Optional[str] = None,
    review_mode: str = "reading",
    comprehension_signal: Optional[str] = None,
) -> dict:
    knowledge = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if not knowledge:
        raise ValueError(f"No knowledge record for lemma {lemma_id}")

    card_data = knowledge.fsrs_card_json
    if isinstance(card_data, str):
        card_data = json.loads(card_data)
    card = Card.from_dict(card_data)
    fsrs_rating = RATING_MAP[rating_int]

    now = datetime.now(timezone.utc)
    new_card, review_log_entry = scheduler.review_card(card, fsrs_rating, now)

    new_state = STATE_MAP.get(new_card.state, "learning")
    knowledge.fsrs_card_json = new_card.to_dict()
    knowledge.knowledge_state = new_state
    knowledge.last_reviewed = now
    knowledge.times_seen = (knowledge.times_seen or 0) + 1
    if rating_int >= 3:
        knowledge.times_correct = (knowledge.times_correct or 0) + 1

    log_entry = ReviewLog(
        lemma_id=lemma_id,
        rating=rating_int,
        reviewed_at=now,
        response_ms=response_ms,
        session_id=session_id,
        review_mode=review_mode,
        comprehension_signal=comprehension_signal,
        fsrs_log_json={
            "rating": rating_int,
            "state": new_state,
            "scheduled_days": new_card.to_dict().get("scheduled_days"),
        },
    )
    db.add(log_entry)
    db.commit()

    return {
        "lemma_id": lemma_id,
        "new_state": new_state,
        "next_due": new_card.due.isoformat(),
    }
