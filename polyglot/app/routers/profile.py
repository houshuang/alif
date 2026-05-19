from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Lemma, UserProfile
from app.schemas import UserProfileOut, UserProfileUpdate, LemmaCognatesOut, CognateInfo
from app.services.cognate_detector import get_user_profile, detect_external_cognates

router = APIRouter(prefix="/api", tags=["profile"])


@router.get("/profile", response_model=UserProfileOut)
def get_profile(db: Session = Depends(get_db)):
    p = get_user_profile(db)
    return UserProfileOut(
        known_languages=p.known_languages,
        native_language=p.native_language,
        cognate_auto_mark_threshold=p.cognate_auto_mark_threshold,
    )


@router.patch("/profile", response_model=UserProfileOut)
def update_profile(req: UserProfileUpdate, db: Session = Depends(get_db)):
    p = get_user_profile(db)
    if req.known_languages is not None:
        p.known_languages = req.known_languages
    if req.native_language is not None:
        p.native_language = req.native_language
    if req.cognate_auto_mark_threshold is not None:
        if req.cognate_auto_mark_threshold not in ("high", "medium", "never"):
            raise HTTPException(status_code=400, detail="threshold must be high/medium/never")
        p.cognate_auto_mark_threshold = req.cognate_auto_mark_threshold
    db.commit()
    db.refresh(p)
    return UserProfileOut(
        known_languages=p.known_languages,
        native_language=p.native_language,
        cognate_auto_mark_threshold=p.cognate_auto_mark_threshold,
    )


@router.get("/lemmas/{lemma_id}/cognates", response_model=LemmaCognatesOut)
def get_lemma_cognates(lemma_id: int, db: Session = Depends(get_db)):
    lemma = db.get(Lemma, lemma_id)
    if not lemma:
        raise HTTPException(status_code=404, detail="lemma not found")
    cognates = lemma.cognates_json or []
    return LemmaCognatesOut(
        lemma_id=lemma.lemma_id,
        lemma_form=lemma.lemma_form,
        language_code=lemma.language_code,
        cognates=[CognateInfo(**c) for c in cognates],
        detected_at=lemma.cognates_detected_at,
        cognate_lemma_id=lemma.cognate_lemma_id,
    )


@router.post("/cognates/detect")
def trigger_cognate_detection(
    language_code: str | None = None,
    limit: int = 50,
    force: bool = False,
    db: Session = Depends(get_db),
):
    """Run external cognate detection for lemmas missing it. Useful for
    backfilling. Gated by POLYGLOT_DETECT_COGNATES=1 unless force=True."""
    q = db.query(Lemma).filter(Lemma.cognates_detected_at.is_(None))
    if language_code:
        q = q.filter(Lemma.language_code == language_code)
    lemmas = q.limit(limit).all()
    if not lemmas:
        return {"processed": 0, "message": "no lemmas need detection"}
    processed = detect_external_cognates(db, lemmas, force=force)
    return {"processed": processed, "candidates": len(lemmas)}
