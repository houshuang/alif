from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.topic_service import (
    MAX_TOPIC_BATCH,
    get_available_topics,
    get_settings,
    ensure_active_topic,
    set_topic,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SetTopicIn(BaseModel):
    domain: str


@router.get("/topic")
def get_topic_settings(db: Session = Depends(get_db)):
    """Get current topic settings."""
    ensure_active_topic(db)
    db.commit()
    settings = get_settings(db)
    return {
        "active_topic": settings.active_topic,
        "topic_started_at": settings.topic_started_at.isoformat() if settings.topic_started_at else None,
        "words_introduced_in_topic": settings.words_introduced_in_topic or 0,
        "max_topic_batch": MAX_TOPIC_BATCH,
    }


@router.put("/topic")
def update_topic(req: SetTopicIn, db: Session = Depends(get_db)):
    """Manually set the active learning topic."""
    try:
        settings = set_topic(db, req.domain)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "active_topic": settings.active_topic,
        "words_introduced_in_topic": 0,
    }


@router.get("/topics")
def list_topics(db: Session = Depends(get_db)):
    """List all available topics with word counts."""
    return get_available_topics(db)


class TashkeelSettingsIn(BaseModel):
    mode: str
    stability_threshold: float = 30.0


@router.get("/tashkeel")
def get_tashkeel_settings(db: Session = Depends(get_db)):
    """Get tashkeel (diacritics) fading settings."""
    settings = get_settings(db)
    return {
        "mode": settings.tashkeel_mode or "always",
        "stability_threshold": settings.tashkeel_stability_threshold or 30.0,
    }


@router.put("/tashkeel")
def update_tashkeel_settings(req: TashkeelSettingsIn, db: Session = Depends(get_db)):
    """Update tashkeel fading settings."""
    if req.mode not in ("always", "fade", "never"):
        raise HTTPException(status_code=400, detail="mode must be always, fade, or never")
    if req.stability_threshold < 1.0 or req.stability_threshold > 365.0:
        raise HTTPException(status_code=400, detail="stability_threshold must be between 1 and 365 days")
    settings = get_settings(db)
    settings.tashkeel_mode = req.mode
    settings.tashkeel_stability_threshold = req.stability_threshold
    db.commit()
    return {
        "mode": settings.tashkeel_mode,
        "stability_threshold": settings.tashkeel_stability_threshold,
    }
