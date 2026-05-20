from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Language, Story, Page
from app.schemas import (
    PasteImportRequest, PdfImportRequest, StorySummary,
    PageView, TokenView, MarkWordRequest,
)
from app.services import reading_intake

router = APIRouter(prefix="/api/texts", tags=["texts"])


@router.get("", response_model=list[StorySummary])
def list_stories(db: Session = Depends(get_db)):
    stories = db.query(Story).order_by(Story.created_at.desc()).all()
    out = []
    for s in stories:
        processed = (
            db.query(func.count(Page.id))
            .filter(Page.story_id == s.id, Page.processed_at.isnot(None))
            .scalar() or 0
        )
        out.append(_story_summary(s, processed))
    return out


@router.post("/paste", response_model=StorySummary)
def import_paste(req: PasteImportRequest, db: Session = Depends(get_db)):
    _check_language(db, req.language_code)
    story = reading_intake.import_paste(
        db,
        language_code=req.language_code,
        body=req.body,
        title=req.title,
        author=req.author,
    )
    return _story_summary(story, 0)


@router.post("/pdf", response_model=StorySummary)
def import_pdf(req: PdfImportRequest, db: Session = Depends(get_db)):
    _check_language(db, req.language_code)
    try:
        story = reading_intake.import_pdf(
            db,
            language_code=req.language_code,
            pdf_path=req.pdf_path,
            title=req.title,
            author=req.author,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"PDF not found: {req.pdf_path}")
    return _story_summary(story, 0)


@router.get("/{story_id}", response_model=StorySummary)
def get_story(story_id: int, db: Session = Depends(get_db)):
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    processed = (
        db.query(func.count(Page.id))
        .filter(Page.story_id == story.id, Page.processed_at.isnot(None))
        .scalar() or 0
    )
    return _story_summary(story, processed)


@router.get("/{story_id}/pages/{page_number}", response_model=PageView)
def get_page(story_id: int, page_number: int, db: Session = Depends(get_db)):
    """Lazy: tokenizes + lemmatizes on first request.

    Stamps ``page.viewed_at`` after the (possibly slow) processing returns —
    this is the signal `warm_pages_ahead` uses to decide which pages need
    pre-warming. Cron-warmed pages don't stamp this; only actual user views do.
    """
    from datetime import datetime, timezone

    result = reading_intake.get_page_view(db, story_id, page_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Page not found")
    page, tokens = result
    page.viewed_at = datetime.now(timezone.utc)
    db.commit()
    total_pages = (
        db.query(func.count(Page.id)).filter(Page.story_id == story_id).scalar() or 0
    )
    return PageView(
        story_id=story_id,
        page_number=page.page_number,
        total_pages=total_pages,
        total_words=page.total_words,
        tokens=[TokenView(**t) for t in tokens],
    )


@router.patch("/{story_id}/mark")
def mark_word(story_id: int, req: MarkWordRequest, db: Session = Depends(get_db)):
    ulk = reading_intake.mark_lemma(db, lemma_id=req.lemma_id, state=req.state)
    # Return the (possibly newly-fetched) gloss so the frontend can display it
    # without an extra round-trip.
    from app.models import Lemma
    lemma = db.get(Lemma, ulk.lemma_id)
    return {
        "lemma_id": ulk.lemma_id,
        "state": ulk.knowledge_state,
        "gloss_en": lemma.gloss_en if lemma else None,
    }


@router.post("/{story_id}/pages/{page_number}/mark_remaining")
def mark_remaining_known(story_id: int, page_number: int, db: Session = Depends(get_db)):
    """Mark every un-marked content lemma on a page as 'known'. Called when
    the user advances pages — presumes the user knew the words they didn't
    tap. Returns the count newly marked."""
    count = reading_intake.bulk_mark_remaining_known(db, story_id, page_number)
    return {"page_number": page_number, "newly_known": count}


@router.post("/{story_id}/extract-sentences")
def extract_sentences(story_id: int, force: bool = False, db: Session = Depends(get_db)):
    """Harvest reviewable Sentence rows from every verified page in a story.

    Normally runs implicitly as part of page-view processing. Use this endpoint
    to backfill sentences for pages that were imported before the harvest service
    existed, or to re-harvest after a quality-gate sweep (`force=True` deletes
    and rebuilds).
    """
    if not db.query(Story).filter(Story.id == story_id).first():
        raise HTTPException(status_code=404, detail="Story not found")
    from app.services.sentence_harvest import harvest_story_sentences
    created = harvest_story_sentences(db, story_id, force=force)
    return {"story_id": story_id, "sentences_created": created}


# ─── helpers ───────────────────────────────────────────────────────────────

def _check_language(db: Session, code: str):
    if not db.query(Language).filter(Language.code == code).first():
        raise HTTPException(status_code=400, detail=f"Unknown language: {code}")


def _story_summary(s: Story, processed_pages: int) -> StorySummary:
    return StorySummary(
        id=s.id,
        language_code=s.language_code,
        title=s.title,
        author=s.author,
        source=s.source,
        page_count=s.page_count,
        processed_pages=processed_pages,
        total_words=s.total_words or 0,
        known_count=s.known_count or 0,
        unknown_count=s.unknown_count or 0,
        status=s.status,
        created_at=s.created_at,
    )
