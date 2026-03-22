"""Podcast API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.podcast_service import get_podcast_path, list_podcasts

router = APIRouter(prefix="/api/podcasts", tags=["podcasts"])


@router.get("")
def get_podcasts():
    """List all available podcast episodes."""
    return {"podcasts": list_podcasts()}


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
