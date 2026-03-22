"""Podcast API endpoints."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.services.podcast_service import (
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
