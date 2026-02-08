from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ImportResultOut
from scripts.import_duolingo import run_import

router = APIRouter(prefix="/api/import", tags=["import"])


@router.post("/duolingo", response_model=ImportResultOut)
def import_duolingo(db: Session = Depends(get_db)):
    return run_import(db)
