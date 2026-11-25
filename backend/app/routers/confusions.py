"""Pair lessons and independent learning evidence; never updates FSRS."""
from datetime import timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.database import get_db
from app.models import ConfusionAttempt, ConfusionCapture, ConfusionLink, ConfusionPair, Lemma
from app.services.confusion_learning import (NEW_PAIRS_PER_DAY, canonical, import_captures, link_capture, naive, now,
    pair_progress, prepare_pair, serialize_pair, word_card)

router = APIRouter(prefix="/api/confusions", tags=["confusions"])


def get_pair(db, pair_id):
    pair = db.get(ConfusionPair, pair_id)
    if not pair: raise HTTPException(404, "Confusion pair not found")
    return pair


def limit_new_pairs(db, pairs, at=None):
    """Only NEW_PAIRS_PER_DAY never-shown pairs become due per UTC day; reviews are unlimited."""
    today = (at or now()).date()
    first_shown = dict(db.query(ConfusionAttempt.pair_id, func.min(ConfusionAttempt.created_at)).group_by(ConfusionAttempt.pair_id).all())
    room = NEW_PAIRS_PER_DAY - sum(naive(t).date() == today for t in first_shown.values())
    # Newest reports first, so a fresh report is the one taught today.
    for pair in sorted(pairs, key=lambda p: p["last_report"] or "", reverse=True):
        if pair["id"] in first_shown or not pair["due_now"]:
            continue
        # Slots also go to pairs still being prepared, so only lessons that will be shown get generated.
        if room > 0 and pair["generation_state"] != "failed":
            room -= 1
        else:
            pair["due_now"] = False
    return pairs


@router.get("")
def list_confusions(db: Session = Depends(get_db)):
    pairs = [serialize_pair(db, p) for p in db.query(ConfusionPair).filter(ConfusionPair.id.in_(db.query(ConfusionLink.pair_id).filter(ConfusionLink.pair_id.isnot(None)))).order_by(ConfusionPair.id.desc()).all()]
    limit_new_pairs(db, pairs)
    unresolved = []
    for link, cap in db.query(ConfusionLink, ConfusionCapture).join(ConfusionCapture, ConfusionCapture.id == ConfusionLink.capture_id).filter(ConfusionLink.pair_id.is_(None)).all():
        lemma = db.get(Lemma, cap.failed_lemma_id)
        if lemma:
            unresolved.append(dict(capture_id=cap.id, word=word_card(lemma), text=cap.confused_with_text,
                state=link.state, session_id=cap.session_id, candidates=link.candidates_json or []))
    return dict(pairs=pairs, unresolved=unresolved)


@router.post("/refresh")
def refresh(db: Session = Depends(get_db)):
    try:
        import_captures(db); db.commit()
    except IntegrityError:
        db.rollback()
        import_captures(db); db.commit()  # Another intake may have linked the same captures.
    return list_confusions(db)


class ConfirmIn(BaseModel):
    lemma_id: int | None = None


@router.post("/captures/{capture_id}/confirm")
def confirm(capture_id: int, body: ConfirmIn, db: Session = Depends(get_db)):
    cap = db.get(ConfusionCapture, capture_id)
    if not cap: raise HTTPException(404, "Capture no longer exists")
    if body.lemma_id is None:
        link = db.get(ConfusionLink, capture_id)
        if not link:
            link = ConfusionLink(capture_id=capture_id); db.add(link)
        link.state = "not_a_word"; link.pair_id = None
        link.resolution_method = "manual"; link.resolved_at = now()
    else:
        try: link_capture(db, cap, body.lemma_id, "manual")
        except ValueError as exc: raise HTTPException(422, str(exc))
    db.commit()
    return list_confusions(db)


@router.get("/search")
def search(q: str, db: Session = Depends(get_db)):
    from app.services.confusion_learning import lexical_candidates
    return lexical_candidates(db, q)


@router.post("/{pair_id}/prepare")
def prepare(pair_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    pair = get_pair(db, pair_id)
    if pair.lesson_json: return serialize_pair(db, pair)
    # Atomic claim prevents duplicate paid generation across workers; failed/stale work retries after a 30-minute lease.
    if pair.generation_state != "working" or naive(pair.generation_started_at) < now() - timedelta(minutes=30):
        claimed = db.query(ConfusionPair).filter(ConfusionPair.id == pair_id,
            (ConfusionPair.generation_state != "working") | (ConfusionPair.generation_started_at < now() - timedelta(minutes=30))).update(
                dict(generation_state="working", generation_started_at=now(), generation_error=None))
        db.commit()
        if claimed: background.add_task(prepare_pair, pair_id)
        db.refresh(pair)
    return serialize_pair(db, pair)


class AttemptIn(BaseModel):
    client_attempt_id: str = Field(min_length=8, max_length=80)
    session_id: str = Field(min_length=1, max_length=80)
    question_index: int = Field(ge=-1, le=20)  # -1 records teaching exposure, never a scored answer
    chosen_lemma_id: int | None = None
    assisted: bool = False


@router.post("/{pair_id}/attempts")
def attempt(pair_id: int, body: AttemptIn, db: Session = Depends(get_db)):
    pair = get_pair(db, pair_id)
    previous = db.query(ConfusionAttempt).filter_by(client_attempt_id=body.client_attempt_id).first()
    if previous:
        if (previous.pair_id, previous.question_index, previous.session_id, previous.chosen_lemma_id, previous.assisted) != (pair_id, body.question_index, body.session_id, body.chosen_lemma_id, body.assisted):
            raise HTTPException(409, "This answer identifier was already used for another answer")
        return dict(correct=previous.correct, progress=pair_progress(db, pair))
    questions = (pair.lesson_json or {}).get("questions", [])
    if body.question_index >= len(questions): raise HTTPException(422, "Exercise no longer exists")
    if body.chosen_lemma_id not in (None, pair.lemma_a_id, pair.lemma_b_id): raise HTTPException(422, "Choose one of this pair's words")
    if body.question_index == -1 and (body.chosen_lemma_id is not None or not body.assisted):
        raise HTTPException(422, "Teaching exposure is unscored and must be marked assisted")
    target = pair.lemma_a_id if body.question_index == -1 else questions[body.question_index]["answer_lemma_id"]
    correct = body.question_index >= 0 and body.chosen_lemma_id == target and not body.assisted
    db.add(ConfusionAttempt(pair_id=pair_id, **body.model_dump(), target_lemma_id=target, correct=correct))
    db.commit()
    return dict(correct=correct, progress=pair_progress(db, pair))
