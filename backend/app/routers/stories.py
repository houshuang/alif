"""Story generation, import, and reading API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
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
    get_stories,
    get_story_detail,
    import_story,
    lookup_word,
    recalculate_readiness,
    skip_story,
    suspend_story,
    too_difficult_story,
)

router = APIRouter(prefix="/api/stories", tags=["stories"])


@router.post("/generate", response_model=StoryDetailOut)
def generate_story_endpoint(
    body: StoryGenerateIn,
    db: Session = Depends(get_db),
):
    try:
        story = generate_story(
            db,
            difficulty=body.difficulty,
            max_sentences=body.max_sentences,
            length=body.length,
            topic=body.topic,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return get_story_detail(db, story.id)


@router.post("/import", response_model=StoryDetailOut)
def import_story_endpoint(
    body: StoryImportIn,
    db: Session = Depends(get_db),
):
    try:
        story = import_story(db, arabic_text=body.arabic_text, title=body.title)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return get_story_detail(db, story.id)


@router.get("", response_model=list[StoryOut])
def list_stories(db: Session = Depends(get_db)):
    return get_stories(db)


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


@router.post("/{story_id}/skip")
def skip_story_endpoint(
    story_id: int,
    body: StoryCompleteIn,
    db: Session = Depends(get_db),
):
    try:
        return skip_story(db, story_id, body.looked_up_lemma_ids, reading_time_ms=body.reading_time_ms)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{story_id}/too-difficult")
def too_difficult_story_endpoint(
    story_id: int,
    body: StoryCompleteIn,
    db: Session = Depends(get_db),
):
    try:
        return too_difficult_story(db, story_id, body.looked_up_lemma_ids, reading_time_ms=body.reading_time_ms)
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
