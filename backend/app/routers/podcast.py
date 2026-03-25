"""Podcast API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.podcast_service import (
    complete_podcast,
    get_podcast_detail,
    get_podcast_path,
    list_podcasts,
    mark_podcast_progress,
)

router = APIRouter(prefix="/api/podcasts", tags=["podcasts"])


@router.get("")
def get_podcasts():
    """List all available podcast episodes with metadata."""
    return {"podcasts": list_podcasts()}


@router.get("/detail/{filename}")
def podcast_detail(filename: str):
    """Get full detail for a podcast including sentences."""
    detail = get_podcast_detail(filename)
    if not detail:
        raise HTTPException(status_code=404, detail="Podcast not found")
    return detail


class ProgressUpdate(BaseModel):
    progress: float
    completed: bool = False


@router.post("/progress/{filename}")
def update_progress(filename: str, body: ProgressUpdate):
    """Update listening progress or mark as completed."""
    ok = mark_podcast_progress(filename, body.progress, body.completed)
    if not ok:
        raise HTTPException(status_code=404, detail="Podcast not found")
    return {"ok": True}


@router.post("/complete/{filename}")
def complete_podcast_endpoint(filename: str, db: Session = Depends(get_db)):
    """Mark podcast as completed and credit words heard."""
    try:
        return complete_podcast(db, filename)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/audio/{filename}")
def serve_podcast(filename: str):
    """Stream a podcast audio file."""
    path = get_podcast_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Podcast not found")
    return FileResponse(
        path,
        media_type="audio/mpeg",
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/image/{filename}")
def serve_image(filename: str):
    """Serve a podcast cover image."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    from app.services.podcast_service import PODCAST_DIR
    path = PODCAST_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    mime = "image/png" if filename.endswith(".png") else "image/jpeg"
    return FileResponse(path, media_type=mime)
