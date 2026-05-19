"""Per-language stats: lemma counts by knowledge state, plus story progress.

Minimal compared to Alif's stats — just enough to give the user a sense of
how much they've covered. Expands later (FSRS retention, daily streak, etc.)
when those mechanisms exist.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Lemma, UserLemmaKnowledge, Story, Page, Language

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
def get_stats(language_code: str, db: Session = Depends(get_db)):
    if not db.query(Language).filter(Language.code == language_code).first():
        raise HTTPException(status_code=400, detail=f"Unknown language: {language_code}")

    # Lemma counts by state. Join Lemma → UserLemmaKnowledge (left).
    state_counts = (
        db.query(UserLemmaKnowledge.knowledge_state, func.count(UserLemmaKnowledge.id))
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(Lemma.language_code == language_code)
        .group_by(UserLemmaKnowledge.knowledge_state)
        .all()
    )
    by_state = {state: count for state, count in state_counts}

    total_lemmas = (
        db.query(func.count(Lemma.lemma_id))
        .filter(Lemma.language_code == language_code)
        .scalar() or 0
    )
    encountered_or_better = (
        db.query(func.count(UserLemmaKnowledge.id))
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(Lemma.language_code == language_code)
        .scalar() or 0
    )
    new_count = total_lemmas - encountered_or_better

    # Story progress
    stories = (
        db.query(
            Story.id, Story.title, Story.page_count,
            func.count(Page.id).filter(Page.processed_at.isnot(None)).label("processed"),
        )
        .outerjoin(Page, Page.story_id == Story.id)
        .filter(Story.language_code == language_code)
        .group_by(Story.id)
        .all()
    )
    story_progress = [
        {
            "id": s.id, "title": s.title, "page_count": s.page_count,
            "processed_pages": int(s.processed or 0),
        }
        for s in stories
    ]

    return {
        "language_code": language_code,
        "total_lemmas": total_lemmas,
        "new": new_count,
        "by_state": {
            "known": by_state.get("known", 0),
            "acquiring": by_state.get("acquiring", 0) + by_state.get("learning", 0),
            "encountered": by_state.get("encountered", 0),
            "unknown": by_state.get("unknown", 0),
            "ignored": by_state.get("ignore", 0),
        },
        "stories": story_progress,
    }
