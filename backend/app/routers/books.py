"""Book import API endpoints: OCR children's books into reading goals."""

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import StoryDetailOut
from app.services.book_import_service import import_book
from app.services.story_service import get_story_detail

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/books", tags=["books"])

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB per image
UPLOAD_DIR = Path("data/book-uploads")


def _save_uploads(images: list[bytes]) -> Path:
    """Save uploaded images to disk for retry on failure."""
    batch_dir = UPLOAD_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    batch_dir.mkdir(parents=True, exist_ok=True)
    for i, data in enumerate(images):
        (batch_dir / f"{i:03d}.jpg").write_bytes(data)
    logger.info(f"Saved {len(images)} images to {batch_dir}")
    return batch_dir


@router.post("/import", response_model=StoryDetailOut)
async def import_book_endpoint(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    title: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Import a children's book from page photos.

    First image is treated as the cover/title page (for metadata extraction).
    Remaining images are content pages in reading order.
    """
    if len(files) < 2:
        raise HTTPException(
            status_code=422,
            detail="At least 2 images required (cover + 1 content page)",
        )

    # Read all images
    images = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File {f.filename} exceeds 20MB limit",
            )
        images.append(data)

    # Save to disk so failed imports can be retried
    _save_uploads(images)

    cover_image = images[0]
    page_images = images[1:]

    try:
        story, new_lemma_ids = import_book(
            db=db,
            cover_image=cover_image,
            page_images=page_images,
            title_override=title,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if new_lemma_ids:
        from app.services.lemma_enrichment import enrich_lemmas_batch
        background_tasks.add_task(enrich_lemmas_batch, new_lemma_ids)

    return get_story_detail(db, story.id)
