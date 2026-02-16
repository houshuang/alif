"""Story generation, import, and reading API endpoints."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    BookPageDetailOut,
    StoryCompleteIn,
    StoryDetailOut,
    StoryGenerateIn,
    StoryImportIn,
    StoryLookupIn,
    StoryLookupOut,
    StoryOut,
    StoryReadinessOut,
)
from app.services.story_service import (
    complete_story,
    delete_story,
    generate_story,
    get_book_page_detail,
    get_stories,
    get_story_detail,
    import_story,
    lookup_word,
    recalculate_readiness,
    suspend_story,
)

router = APIRouter(prefix="/api/stories", tags=["stories"])


@router.post("/generate", response_model=StoryDetailOut)
def generate_story_endpoint(
    body: StoryGenerateIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        story, new_lemma_ids = generate_story(
            db,
            difficulty=body.difficulty,
            max_sentences=body.max_sentences,
            length=body.length,
            topic=body.topic,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if new_lemma_ids:
        from app.services.lemma_enrichment import enrich_lemmas_batch
        background_tasks.add_task(enrich_lemmas_batch, new_lemma_ids)

    return get_story_detail(db, story.id)


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
