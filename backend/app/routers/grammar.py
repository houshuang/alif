from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.grammar_service import (
    get_all_features,
    get_user_progress,
    get_unlocked_features,
    seed_grammar_features,
)
from app.services.grammar_lesson_service import (
    get_lesson,
    introduce_feature,
    get_confused_features,
)

router = APIRouter(prefix="/api/grammar", tags=["grammar"])


@router.get("/features")
def features(db: Session = Depends(get_db)):
    """List all grammar features with categories."""
    seed_grammar_features(db)
    return {"features": get_all_features(db)}


@router.get("/progress")
def progress(db: Session = Depends(get_db)):
    """User's exposure and comfort score per grammar feature."""
    seed_grammar_features(db)
    return {"progress": get_user_progress(db)}


@router.get("/unlocked")
def unlocked(db: Session = Depends(get_db)):
    """Which features/tiers are unlocked at the user's current level."""
    seed_grammar_features(db)
    return get_unlocked_features(db)


@router.get("/lesson/{feature_key}")
def lesson(feature_key: str, db: Session = Depends(get_db)):
    """Get grammar lesson content for a feature."""
    seed_grammar_features(db)
    result = get_lesson(db, feature_key)
    if result is None:
        raise HTTPException(status_code=404, detail="Feature not found")
    return result


class IntroduceIn(BaseModel):
    feature_key: str


@router.post("/introduce")
def introduce(body: IntroduceIn, db: Session = Depends(get_db)):
    """Mark a grammar feature as introduced (user has seen the lesson)."""
    seed_grammar_features(db)
    result = introduce_feature(db, body.feature_key)
    if result is None:
        raise HTTPException(status_code=404, detail="Feature not found")
    return result


@router.get("/confused")
def confused(db: Session = Depends(get_db)):
    """Get features with high confusion rates that need resurfacing."""
    seed_grammar_features(db)
    return {"features": get_confused_features(db)}
