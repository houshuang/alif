"""Podcast API endpoints."""

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
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

from app.services import walk_episodes

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


# ── Walk episodes ────────────────────────────────────────────────────


@router.get("/feed.xml")
def walk_feed(base: str, request: Request):
    """RSS feed of the walk episodes for a podcast app.

    The backend sits behind a private path prefix it cannot see, so the caller
    passes that prefix as ``base``; it must be an https URL on this host.
    """
    parsed = urlparse(base)
    host = (request.headers.get("host") or "").split(":")[0]
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname != host:
        raise HTTPException(status_code=400, detail="base must be an https URL on this host")
    return Response(walk_episodes.build_feed(base), media_type="application/rss+xml")


class FeedbackBody(BaseModel):
    rating: str


@router.post("/feedback/{filename}")
def podcast_feedback(filename: str, body: FeedbackBody):
    """Rate an episode too easy / right / too hard; moves the firm-word threshold."""
    try:
        return walk_episodes.record_feedback(filename, body.rating)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/walk")
def walk_status():
    """Topic queue, current topic, firm-word threshold and whether the daily job will run."""
    return walk_episodes.status()


class TopicBody(BaseModel):
    title: str
    notes: str = ""


@router.post("/walk/topics")
def walk_add_topic(body: TopicBody):
    try:
        walk_episodes.add_topic(body.title, body.notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return walk_episodes.status()


@router.delete("/walk/topics/{index}")
def walk_remove_topic(index: int):
    try:
        walk_episodes.remove_topic(index)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return walk_episodes.status()
