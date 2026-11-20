"""Dialect Lines: single lines from TV episodes and clips, grouped by expression.

`data/dialect/lines.json` holds sources, lines and expressions, and
`data/dialect/audio/` the audio files. Both stay out of Git because they are
third-party content for private use. Only listening evidence is stored here.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DialectLineState
from app.services.interaction_logger import log_interaction

router = APIRouter(prefix="/api/dialect", tags=["dialect"])

DIALECT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "dialect"
SOURCE_ID_RE = re.compile(r"^[a-z0-9-]{1,48}$")
LINE_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4"}


class DialectEventIn(BaseModel):
    event: Literal["play", "arabic", "english", "hard"]
    hard: bool | None = None


def _load_catalogue() -> dict:
    path = DIALECT_DIR / "lines.json"
    if not path.exists():
        return {"sources": [], "lines": [], "expressions": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("")
def get_catalogue(db: Session = Depends(get_db)):
    catalogue = _load_catalogue()
    hard = {r.line_id for r in db.query(DialectLineState).filter(DialectLineState.hard.is_(True))}
    return {
        "sources": catalogue.get("sources", []),
        "lines": [{**line, "hard": line["id"] in hard} for line in catalogue.get("lines", [])],
        "expressions": catalogue.get("expressions", []),
    }


@router.get("/audio/{source_id}")
def source_audio(source_id: str):
    if not SOURCE_ID_RE.match(source_id):
        raise HTTPException(status_code=404, detail="Source not found")
    source = next((s for s in _load_catalogue().get("sources", []) if s["id"] == source_id), None)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    path = DIALECT_DIR / "audio" / Path(source["audio"]).name
    media_type = AUDIO_TYPES.get(path.suffix)
    if media_type is None or not path.exists():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(path, media_type=media_type)


@router.post("/lines/{line_id}/event")
def record_line_event(line_id: str, body: DialectEventIn, db: Session = Depends(get_db)):
    if not LINE_ID_RE.match(line_id) or not any(
        line["id"] == line_id for line in _load_catalogue().get("lines", [])
    ):
        raise HTTPException(status_code=404, detail="Line not found")
    if body.event == "hard" and body.hard is None:
        raise HTTPException(status_code=422, detail="hard is required")

    row = db.get(DialectLineState, line_id)
    if row is None:
        row = DialectLineState(line_id=line_id, hard=False, plays=0, arabic_reveals=0, english_reveals=0)
        db.add(row)
    if body.event == "play":
        row.plays += 1
    elif body.event == "arabic":
        row.arabic_reveals += 1
    elif body.event == "english":
        row.english_reveals += 1
    else:
        row.hard = bool(body.hard)
    row.updated_at = _utcnow()
    db.commit()

    log_interaction(event=f"dialect_{body.event}", context=f"dialect_line:{line_id}", hard=row.hard)
    return {"line_id": line_id, "hard": row.hard}
