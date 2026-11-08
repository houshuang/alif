"""Dialect listening clips: short videos practised until followed without text.

`data/clips/clips.json` holds the catalogue and `data/clips/video/<id>.mp4` the
media. Both stay out of Git because they are third-party content for private use.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ClipProgress
from app.services.interaction_logger import log_interaction

router = APIRouter(prefix="/api/clips", tags=["clips"])

CLIPS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "clips"
CLIP_ID_RE = re.compile(r"^\d{1,40}$")
RETEST_LADDER_DAYS = [1, 3, 7, 21, 60]


class ClipEventIn(BaseModel):
    event: Literal["stage", "blind_rating", "pass"]
    stage: int | None = Field(default=None, ge=1, le=4)
    rating: Literal["bits", "most", "all"] | None = None


def _load_catalogue() -> dict:
    path = CLIPS_DIR / "clips.json"
    if not path.exists():
        return {"clips": [], "core": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _require_clip(clip_id: str) -> None:
    if not CLIP_ID_RE.match(clip_id):
        raise HTTPException(status_code=404, detail="Clip not found")
    if not any(c["id"] == clip_id for c in _load_catalogue()["clips"]):
        raise HTTPException(status_code=404, detail="Clip not found")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _progress_out(row: ClipProgress | None, now: datetime) -> dict:
    if row is None:
        return {"stage": 0, "pass_count": 0, "retest_due": False, "next_check_at": None}
    return {
        "stage": row.stage,
        "pass_count": row.pass_count,
        "retest_due": row.next_check_at is not None and row.next_check_at <= now,
        "next_check_at": row.next_check_at.isoformat() + "Z" if row.next_check_at else None,
    }


@router.get("")
def list_clips(db: Session = Depends(get_db)):
    catalogue = _load_catalogue()
    rows = {r.clip_id: r for r in db.query(ClipProgress).all()}
    now = _utcnow()
    return {
        "clips": [
            {**clip, "progress": _progress_out(rows.get(clip["id"]), now)}
            for clip in catalogue["clips"]
        ],
        "core": catalogue.get("core", []),
    }


@router.get("/{clip_id}/video")
def clip_video(clip_id: str):
    _require_clip(clip_id)
    path = CLIPS_DIR / "video" / f"{clip_id}.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(path, media_type="video/mp4")


@router.post("/{clip_id}/event")
def record_clip_event(clip_id: str, body: ClipEventIn, db: Session = Depends(get_db)):
    _require_clip(clip_id)
    if body.event == "stage" and body.stage is None:
        raise HTTPException(status_code=422, detail="stage is required")
    if body.event == "blind_rating" and body.rating is None:
        raise HTTPException(status_code=422, detail="rating is required")

    now = _utcnow()
    row = db.get(ClipProgress, clip_id)
    if row is None:
        row = ClipProgress(clip_id=clip_id, stage=1, pass_count=0)
        db.add(row)

    if body.event == "stage":
        row.stage = body.stage
    elif body.event == "pass":
        step = min(row.pass_count, len(RETEST_LADDER_DAYS) - 1)
        row.pass_count += 1
        row.stage = 4
        row.last_passed_at = now
        row.next_check_at = now + timedelta(days=RETEST_LADDER_DAYS[step])
    row.updated_at = now
    db.commit()

    log_interaction(
        event=f"clip_{body.event}",
        context=f"clip_id:{clip_id}",
        stage=body.stage,
        rating_label=body.rating,
    )
    return _progress_out(row, now)
