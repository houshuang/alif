from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.grammar_service import (
    get_all_features,
    get_user_progress,
    get_unlocked_features,
    seed_grammar_features,
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
