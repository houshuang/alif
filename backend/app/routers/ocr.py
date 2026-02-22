"""OCR endpoints for textbook scanning and story image import."""

import uuid
import logging

from fastapi import APIRouter, Depends, File, UploadFile, BackgroundTasks, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import PageUpload
from app.schemas import PageUploadOut, BatchUploadOut, OCRStoryImportOut
from app.services.ocr_service import (
    extract_text_from_image,
    process_textbook_page,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB per image


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


def _process_page_background(upload_id: int, image_bytes: bytes, start_acquiring: bool = False) -> None:
    """Background task wrapper — creates its own DB session."""
    db = SessionLocal()
    try:
        upload = db.query(PageUpload).filter(PageUpload.id == upload_id).first()
        if not upload:
            logger.error(f"PageUpload {upload_id} not found for background processing")
            return
        process_textbook_page(db, upload, image_bytes, start_acquiring=start_acquiring)
    except Exception:
        logger.exception(f"Background processing failed for upload {upload_id}")
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
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    batch_id = str(uuid.uuid4())[:8]
    uploads = []

    for file in files:
        image_bytes = await file.read()

        if len(image_bytes) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File {file.filename} exceeds 20MB limit",
            )

        if not image_bytes:
            continue

        upload = PageUpload(
            batch_id=batch_id,
            filename=file.filename,
            status="pending",
        )
        db.add(upload)
        db.flush()

        uploads.append(upload)
        background_tasks.add_task(_process_page_background, upload.id, image_bytes, start_acquiring)

    db.commit()

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
