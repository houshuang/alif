"""OCR endpoints for textbook scanning and story image import."""

import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile, BackgroundTasks, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import PageUpload
from app.schemas import PageUploadOut, BatchUploadOut, OCRStoryImportOut
from app.services.ocr_service import (
    extract_text_from_image,
    process_batch,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB per image
UPLOAD_DIR = Path("data/textbook-uploads")


def _format_upload(upload: PageUpload) -> dict:
    return {
        "id": upload.id,
        "batch_id": upload.batch_id,
        "filename": upload.filename,
        "status": upload.status,
        "new_words": upload.new_words or 0,
        "existing_words": upload.existing_words or 0,
        "textbook_page_number": upload.textbook_page_number,
        "error_message": upload.error_message,
        "created_at": upload.created_at.isoformat() if upload.created_at else "",
        "completed_at": upload.completed_at.isoformat() if upload.completed_at else None,
        "extracted_words": upload.extracted_words_json or [],
    }


def _save_uploads(batch_id: str, file_images: list[tuple[str, bytes]]) -> Path:
    """Save uploaded images to disk for retry on failure."""
    batch_dir = UPLOAD_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    for filename, data in file_images:
        safe_name = filename.replace("/", "_") if filename else "page.jpg"
        (batch_dir / safe_name).write_bytes(data)
    return batch_dir


def _load_saved_images(batch_id: str) -> list[tuple[str, bytes]] | None:
    """Load previously saved images for a batch. Returns None if not found."""
    batch_dir = UPLOAD_DIR / batch_id
    if not batch_dir.exists():
        return None
    images = []
    for f in sorted(batch_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            images.append((f.name, f.read_bytes()))
    return images if images else None


def _process_batch_background(
    batch_id: str,
    file_images: list[tuple[str, bytes]],
    start_acquiring: bool = False,
) -> None:
    """Background task: OCR all pages, dedupe words, single DB import."""
    db = SessionLocal()
    try:
        process_batch(db, batch_id, file_images, start_acquiring=start_acquiring)
    except Exception:
        logger.exception(f"Background batch processing failed for {batch_id}")
        # Mark any still-processing pages as failed so they don't stay stuck
        try:
            stuck = (
                db.query(PageUpload)
                .filter(PageUpload.batch_id == batch_id, PageUpload.status == "processing")
                .all()
            )
            for u in stuck:
                u.status = "failed"
                u.error_message = "Background processing crashed"
                u.completed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            logger.exception(f"Failed to mark stuck pages for batch {batch_id}")
    finally:
        db.close()


@router.post("/scan-pages", response_model=BatchUploadOut)
async def scan_textbook_pages(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    start_acquiring: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Upload one or more textbook page images for OCR word extraction.

    Returns immediately with batch tracking info. Processing happens in background.
    Pages are OCR'd in parallel, words deduped across pages, then imported in one DB transaction.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    batch_id = str(uuid.uuid4())[:8]
    uploads = []
    file_images: list[tuple[str, bytes]] = []

    for file in files:
        image_bytes = await file.read()

        if len(image_bytes) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File {file.filename} exceeds 20MB limit",
            )

        if not image_bytes:
            continue

        # Create tracking record (lightweight, no OCR yet)
        upload = PageUpload(
            batch_id=batch_id,
            filename=file.filename,
            status="pending",
        )
        db.add(upload)
        uploads.append(upload)
        file_images.append((file.filename, image_bytes))

    db.commit()

    # Save images to disk for retry on failure
    _save_uploads(batch_id, file_images)

    # Single background task for the entire batch
    background_tasks.add_task(_process_batch_background, batch_id, file_images, start_acquiring)

    return {
        "batch_id": batch_id,
        "pages": [_format_upload(u) for u in uploads],
        "total_new": 0,
        "total_existing": 0,
    }


@router.get("/batch/{batch_id}", response_model=BatchUploadOut)
def get_batch_status(batch_id: str, db: Session = Depends(get_db)):
    """Get the status of a batch upload with all page results."""
    uploads = (
        db.query(PageUpload)
        .filter(PageUpload.batch_id == batch_id)
        .order_by(PageUpload.id)
        .all()
    )
    if not uploads:
        raise HTTPException(status_code=404, detail="Batch not found")

    total_new = sum(u.new_words or 0 for u in uploads)
    total_existing = sum(u.existing_words or 0 for u in uploads)

    return {
        "batch_id": batch_id,
        "pages": [_format_upload(u) for u in uploads],
        "total_new": total_new,
        "total_existing": total_existing,
    }


@router.get("/uploads")
def list_uploads(
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    """List recent upload batches with their results."""
    # Get distinct batch_ids ordered by most recent
    from sqlalchemy import func

    batch_ids = (
        db.query(PageUpload.batch_id, func.max(PageUpload.created_at).label("latest"))
        .group_by(PageUpload.batch_id)
        .order_by(func.max(PageUpload.created_at).desc())
        .limit(limit)
        .all()
    )

    batches = []
    for batch_id, latest in batch_ids:
        uploads = (
            db.query(PageUpload)
            .filter(PageUpload.batch_id == batch_id)
            .order_by(PageUpload.id)
            .all()
        )
        total_new = sum(u.new_words or 0 for u in uploads)
        total_existing = sum(u.existing_words or 0 for u in uploads)
        all_completed = all(u.status in ("completed", "failed") for u in uploads)
        any_failed = any(u.status == "failed" for u in uploads)

        batches.append({
            "batch_id": batch_id,
            "page_count": len(uploads),
            "status": "failed" if any_failed else ("completed" if all_completed else "processing"),
            "total_new": total_new,
            "total_existing": total_existing,
            "created_at": uploads[0].created_at.isoformat() if uploads else "",
            "pages": [_format_upload(u) for u in uploads],
        })

    return {"batches": batches}


@router.post("/batch/{batch_id}/retry", response_model=BatchUploadOut)
def retry_batch(
    batch_id: str,
    background_tasks: BackgroundTasks,
    start_acquiring: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Retry a failed or stuck batch using saved images."""
    uploads = (
        db.query(PageUpload)
        .filter(PageUpload.batch_id == batch_id)
        .order_by(PageUpload.id)
        .all()
    )
    if not uploads:
        raise HTTPException(status_code=404, detail="Batch not found")

    file_images = _load_saved_images(batch_id)
    if not file_images:
        raise HTTPException(
            status_code=404,
            detail="No saved images found for this batch — images must be re-uploaded",
        )

    # Reset all pages to pending
    for u in uploads:
        u.status = "pending"
        u.error_message = None
        u.new_words = 0
        u.existing_words = 0
        u.extracted_words_json = None
        u.completed_at = None
    db.commit()

    background_tasks.add_task(_process_batch_background, batch_id, file_images, start_acquiring)

    return {
        "batch_id": batch_id,
        "pages": [_format_upload(u) for u in uploads],
        "total_new": 0,
        "total_existing": 0,
    }


@router.post("/extract-text", response_model=OCRStoryImportOut)
async def extract_text_for_story(
    file: UploadFile = File(...),
):
    """Extract Arabic text from an image for story import.

    This is synchronous — waits for OCR completion and returns extracted text.
    The caller can then pass this text to the regular story import endpoint.
    """
    image_bytes = await file.read()

    if len(image_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 20MB limit")

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        text = extract_text_from_image(image_bytes)
    except Exception as e:
        logger.exception("OCR text extraction failed")
        raise HTTPException(status_code=500, detail=f"OCR failed: {str(e)}")

    if not text.strip():
        raise HTTPException(status_code=422, detail="No Arabic text found in image")

    return {"extracted_text": text}
