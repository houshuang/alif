"""FastAPI app for Spanish pilot — Norwegian UI, student-scoped SRS."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .content_loader import load_all
from .database import get_db, Base, engine
from .models import Card, Lemma, ReviewLog, Sentence, Student
from .scheduler import apply_review
from .session_builder import build_session, dashboard_stats

app = FastAPI(title="Spanish Pilot", version="0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    load_all()


# -- Schemas -----------------------------------------------------------

class StudentOut(BaseModel):
    id: int
    name: str
    mode_preference: str
    total_reviewed: int = 0
    known_count: int = 0


class StudentCreate(BaseModel):
    name: str


class ModeUpdate(BaseModel):
    mode_preference: Literal["self_grade", "multiple_choice"]


class ReviewItemOut(BaseModel):
    card_id: int
    lemma_id: int
    lemma_es: str
    sentence_id: int
    sentence_es: str
    sentence_no: str
    distractors_no: list[str]
    word_mapping: list[dict]
    is_new: bool


class ReviewSubmit(BaseModel):
    card_id: int
    sentence_id: int
    rating: int  # 1-4
    mode: Literal["self_grade", "multiple_choice"]
    correct: Optional[bool] = None
    response_ms: int


class LemmaDetailOut(BaseModel):
    id: int
    lemma_es: str
    gloss_no: str
    pos: str
    gender: str
    article_quirk: str
    cefr_level: str
    frequency_rank: int
    memory_hook_no: str
    etymology_no: str
    example_es: str
    example_no: str
    conjugation_present: Optional[dict]
    agreement_forms: Optional[dict]
    plural_form: str
    conjugation_applicable: bool
    # per-student state
    card_state: Optional[str] = None
    leitner_box: Optional[int] = None
    times_seen: int = 0
    times_correct: int = 0


# -- Endpoints --------------------------------------------------------

@app.get("/api/students", response_model=list[StudentOut])
def list_students(db: Session = Depends(get_db)):
    out = []
    for s in db.query(Student).order_by(Student.created_at.desc()).all():
        cards = db.query(Card).filter(Card.student_id == s.id).all()
        reviewed = sum(1 for c in cards if c.times_seen > 0)
        known = sum(1 for c in cards if c.state == "known")
        out.append(StudentOut(
            id=s.id, name=s.name, mode_preference=s.mode_preference,
            total_reviewed=reviewed, known_count=known,
        ))
    return out


@app.post("/api/students", response_model=StudentOut)
def create_student(body: StudentCreate, db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Navn mangler")
    exists = db.query(Student).filter(Student.name == name).first()
    if exists:
        raise HTTPException(400, "Elev med dette navnet finnes allerede")
    s = Student(name=name)
    db.add(s)
    db.commit()
    db.refresh(s)
    return StudentOut(id=s.id, name=s.name, mode_preference=s.mode_preference)


@app.get("/api/students/{student_id}/dashboard")
def get_dashboard(student_id: int, db: Session = Depends(get_db)):
    s = db.query(Student).get(student_id)
    if not s:
        raise HTTPException(404, "Elev finnes ikke")
    stats = dashboard_stats(db, student_id)
    return {
        "student": {"id": s.id, "name": s.name, "mode_preference": s.mode_preference},
        "stats": stats,
    }


@app.put("/api/students/{student_id}/mode")
def update_mode(student_id: int, body: ModeUpdate, db: Session = Depends(get_db)):
    s = db.query(Student).get(student_id)
    if not s:
        raise HTTPException(404, "Elev finnes ikke")
    s.mode_preference = body.mode_preference
    db.commit()
    return {"ok": True, "mode_preference": s.mode_preference}


@app.get("/api/students/{student_id}/session", response_model=list[ReviewItemOut])
def get_session(student_id: int, db: Session = Depends(get_db)):
    s = db.query(Student).get(student_id)
    if not s:
        raise HTTPException(404, "Elev finnes ikke")
    items = build_session(db, student_id)
    return [ReviewItemOut(**item.__dict__) for item in items]


@app.post("/api/students/{student_id}/review")
def submit_review(student_id: int, body: ReviewSubmit, db: Session = Depends(get_db)):
    card = db.query(Card).get(body.card_id)
    if not card or card.student_id != student_id:
        raise HTTPException(404, "Kort finnes ikke")

    apply_review(card, body.rating)
    db.add(ReviewLog(
        student_id=student_id,
        lemma_id=card.lemma_id,
        sentence_id=body.sentence_id,
        mode=body.mode,
        rating=body.rating,
        correct=body.correct,
        response_ms=body.response_ms,
    ))
    db.commit()
    return {
        "ok": True,
        "state": card.state,
        "next_due": card.next_due.isoformat() if card.next_due else None,
        "leitner_box": card.acquisition_box,
    }


@app.get("/api/lemmas/by-es/{lemma_es}", response_model=LemmaDetailOut)
def get_lemma_by_es(
    lemma_es: str,
    student_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    lem = db.query(Lemma).filter(Lemma.lemma_es == lemma_es).first()
    if not lem:
        raise HTTPException(404, f"Lemma '{lemma_es}' finnes ikke")
    return _build_lemma_detail(db, lem, student_id)


@app.get("/api/lemmas/{lemma_id}/detail", response_model=LemmaDetailOut)
def get_lemma_detail(
    lemma_id: int,
    student_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    lem = db.query(Lemma).get(lemma_id)
    if not lem:
        raise HTTPException(404, "Lemma finnes ikke")
    return _build_lemma_detail(db, lem, student_id)


def _build_lemma_detail(db: Session, lem: Lemma, student_id: Optional[int]) -> "LemmaDetailOut":
    out = LemmaDetailOut(
        id=lem.id,
        lemma_es=lem.lemma_es, gloss_no=lem.gloss_no or "",
        pos=lem.pos or "", gender=lem.gender or "none",
        article_quirk=lem.article_quirk or "",
        cefr_level=lem.cefr_level or "A1", frequency_rank=lem.frequency_rank or 999,
        memory_hook_no=lem.memory_hook_no or "",
        etymology_no=lem.etymology_no or "",
        example_es=lem.example_es or "",
        example_no=lem.example_no or "",
        conjugation_present=lem.conjugation_present_json,
        agreement_forms=lem.agreement_forms_json,
        plural_form=lem.plural_form or "",
        conjugation_applicable=bool(lem.conjugation_applicable),
    )
    if student_id:
        card = db.query(Card).filter(
            Card.student_id == student_id, Card.lemma_id == lem.id
        ).first()
        if card:
            out.card_state = card.state
            out.leitner_box = card.acquisition_box
            out.times_seen = card.times_seen
            out.times_correct = card.times_correct
    return out


# -- Frontend static mount --------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    @app.get("/")
    def root():
        return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}
