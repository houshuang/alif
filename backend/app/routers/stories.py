"""Story generation, import, and reading API endpoints."""

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Story
from app.schemas import (
    BookPageDetailOut,
    PretestWordOut,
    StoryCompleteIn,
    StoryDetailOut,
    StoryGenerateIn,
    StoryGenerateOut,
    StoryImportIn,
    StoryLookupIn,
    StoryLookupOut,
    StoryOut,
    StoryReadinessOut,
)
from app.services.story_service import (
    archive_story,
    complete_story,
    delete_story,
    generate_story,
    generate_story_audio,
    get_book_page_detail,
    get_pretest_words,
    get_stories,
    get_story_detail,
    import_story,
    lookup_word,
    mark_story_heard,
    recalculate_readiness,
    suspend_story,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stories", tags=["stories"])


def _generate_story_background(
    story_id: int,
    difficulty: str,
    max_sentences: int,
    length: str,
    topic: str | None,
    format_type: str = "standard",
):
    """Run story generation in background, updating the placeholder row."""
    db = SessionLocal()
    try:
        story, new_lemma_ids = generate_story(
            db,
            difficulty=difficulty,
            max_sentences=max_sentences,
            length=length,
            topic=topic,
            format_type=format_type,
            existing_story_id=story_id,
        )
        if new_lemma_ids:
            from app.services.lemma_enrichment import enrich_lemmas_batch
            enrich_lemmas_batch(new_lemma_ids)
    except Exception as e:
        logger.exception("Background story generation failed for story %d", story_id)
        try:
            placeholder = db.query(Story).get(story_id)
            if placeholder and placeholder.status == "generating":
                placeholder.status = "failed"
                db.commit()
        except Exception:
            logger.exception("Failed to mark story %d as failed", story_id)
    finally:
        db.close()


@router.post("/generate", response_model=StoryGenerateOut)
def generate_story_endpoint(
    body: StoryGenerateIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    placeholder = Story(
        body_ar="",
        source="generated",
        status="generating",
        difficulty_level=body.difficulty,
        format_type=body.format_type,
    )
    db.add(placeholder)
    db.commit()
    db.refresh(placeholder)

    background_tasks.add_task(
        _generate_story_background,
        placeholder.id,
        body.difficulty,
        body.max_sentences,
        body.length,
        body.topic,
        body.format_type,
    )

    return {"id": placeholder.id, "status": "generating"}


@router.post("/import", response_model=StoryDetailOut)
def import_story_endpoint(
    body: StoryImportIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        story, new_lemma_ids = import_story(db, arabic_text=body.arabic_text, title=body.title)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if new_lemma_ids:
        from app.services.lemma_enrichment import enrich_lemmas_batch
        background_tasks.add_task(enrich_lemmas_batch, new_lemma_ids)

    return get_story_detail(db, story.id)


@router.get("", response_model=list[StoryOut])
def list_stories(db: Session = Depends(get_db)):
    from app.routers.stats import _get_first_known_dates, _get_pace

    stories = get_stories(db)

    first_known = _get_first_known_dates(db)
    pace = _get_pace(db, first_known_dates=first_known)

    if pace.words_per_day_7d > 0 and pace.study_days_7d > 0:
        study_frequency = pace.study_days_7d / 7.0
        effective_daily_rate = pace.words_per_day_7d * study_frequency
        if effective_daily_rate > 0:
            for s in stories:
                unknown = s.get("unknown_count", 0) if isinstance(s, dict) else getattr(s, "unknown_count", 0)
                status = s.get("status", "") if isinstance(s, dict) else getattr(s, "status", "")
                if status == "active" and unknown > 3:
                    days_est = round(unknown / effective_daily_rate)
                    if isinstance(s, dict):
                        s["estimated_days_to_ready"] = days_est

    return stories


@router.get("/{story_id}/pages/{page_number}", response_model=BookPageDetailOut)
def get_page_detail(story_id: int, page_number: int, db: Session = Depends(get_db)):
    try:
        return get_book_page_detail(db, story_id, page_number)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{story_id}", response_model=StoryDetailOut)
def get_story(story_id: int, db: Session = Depends(get_db)):
    try:
        return get_story_detail(db, story_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{story_id}/pretest-words", response_model=list[PretestWordOut])
def get_pretest_words_endpoint(story_id: int, db: Session = Depends(get_db)):
    """Top 5 cold unknown words for pretesting before reading.

    Ordered by token frequency in the story — words that appear most
    often have the highest payoff for pre-encoding.
    """
    return get_pretest_words(db, story_id)


@router.post("/{story_id}/complete")
def complete_story_endpoint(
    story_id: int,
    body: StoryCompleteIn,
    db: Session = Depends(get_db),
):
    try:
        return complete_story(db, story_id, body.looked_up_lemma_ids, reading_time_ms=body.reading_time_ms)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{story_id}/suspend")
def suspend_story_endpoint(story_id: int, db: Session = Depends(get_db)):
    try:
        return suspend_story(db, story_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{story_id}")
def delete_story_endpoint(story_id: int, db: Session = Depends(get_db)):
    try:
        return delete_story(db, story_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{story_id}/lookup", response_model=StoryLookupOut)
def lookup_word_endpoint(
    story_id: int,
    body: StoryLookupIn,
    db: Session = Depends(get_db),
):
    try:
        return lookup_word(db, story_id, body.lemma_id, body.position)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{story_id}/readiness", response_model=StoryReadinessOut)
def get_readiness(story_id: int, db: Session = Depends(get_db)):
    try:
        return recalculate_readiness(db, story_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{story_id}/archive")
def archive_story_endpoint(story_id: int, db: Session = Depends(get_db)):
    try:
        return archive_story(db, story_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{story_id}/mark-heard")
def mark_story_heard_endpoint(story_id: int, db: Session = Depends(get_db)):
    try:
        return mark_story_heard(db, story_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _generate_audio_background(story_id: int):
    """Run story audio generation in background."""
    db = SessionLocal()
    try:
        asyncio.run(generate_story_audio(db, story_id))
    except Exception as e:
        logger.exception("Background audio generation failed for story %d", story_id)
    finally:
        db.close()


@router.post("/{story_id}/generate-audio")
def generate_audio_endpoint(
    story_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    background_tasks.add_task(_generate_audio_background, story_id)
    return {"story_id": story_id, "status": "generating_audio"}


@router.get("/{story_id}/audio")
def get_story_audio(story_id: int, db: Session = Depends(get_db)):
    from app.services.tts import STORY_AUDIO_DIR

    story = db.query(Story).filter(Story.id == story_id).first()
    if not story or not story.audio_filename:
        raise HTTPException(status_code=404, detail="No audio for this story")

    path = STORY_AUDIO_DIR / story.audio_filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    return FileResponse(str(path), media_type="audio/mpeg", filename=story.audio_filename)
