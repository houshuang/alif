from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Language
from app.schemas import LanguageOut
from app.services.languages import get_provider, ProviderUnavailable

router = APIRouter(prefix="/api/languages", tags=["languages"])


@router.get("", response_model=list[LanguageOut])
def list_languages(db: Session = Depends(get_db)):
    languages = db.query(Language).filter(Language.is_active == True).all()  # noqa: E712
    out = []
    for lang in languages:
        try:
            get_provider(lang.code)
            available = True
        except ProviderUnavailable:
            available = False
        out.append(LanguageOut(
            code=lang.code,
            name=lang.name,
            script=lang.script,
            direction=lang.direction,
            accent_display=lang.accent_display,
            is_active=lang.is_active,
            provider_available=available,
        ))
    return out
