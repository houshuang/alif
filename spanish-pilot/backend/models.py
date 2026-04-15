"""SQLAlchemy models for spanish-pilot."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    mode_preference = Column(String(20), default="self_grade")  # self_grade | multiple_choice
    created_at = Column(DateTime, default=utcnow)


class Lemma(Base):
    __tablename__ = "lemmas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lemma_es = Column(String(80), unique=True, nullable=False, index=True)
    gloss_no = Column(Text)
    pos = Column(String(20))
    gender = Column(String(5))
    article_quirk = Column(Text)
    cefr_level = Column(String(4))
    frequency_rank = Column(Integer, index=True)
    memory_hook_no = Column(Text)
    etymology_no = Column(Text)
    example_es = Column(Text)
    example_no = Column(Text)
    conjugation_present_json = Column(JSON)  # {yo, tu, el, nosotros, vosotros, ellos}
    agreement_forms_json = Column(JSON)  # {masc_sg, fem_sg, masc_pl, fem_pl}
    plural_form = Column(String(80))
    conjugation_applicable = Column(Boolean, default=False)


class Sentence(Base):
    __tablename__ = "sentences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    es = Column(Text, nullable=False)
    no = Column(Text, nullable=False)
    difficulty_rank = Column(Integer, index=True)
    distractors_no_json = Column(JSON)  # list[str]
    word_mapping_json = Column(JSON)  # list of {position, form, lemma_es, grammatical_note}


class SentenceLemma(Base):
    """Many-to-many: which lemmas appear in which sentences. For fast lookup when
    selecting sentences by target lemma."""
    __tablename__ = "sentence_lemmas"

    sentence_id = Column(Integer, ForeignKey("sentences.id"), primary_key=True)
    lemma_id = Column(Integer, ForeignKey("lemmas.id"), primary_key=True, index=True)


class Card(Base):
    """Per-student, per-lemma scheduling state."""
    __tablename__ = "cards"
    __table_args__ = (UniqueConstraint("student_id", "lemma_id", name="uq_student_lemma"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    lemma_id = Column(Integer, ForeignKey("lemmas.id"), nullable=False, index=True)

    # Lifecycle: new → acquiring → learning → known (with lapsed detour)
    state = Column(String(20), default="new", index=True)

    # Leitner acquisition (boxes 1/2/3; NULL once in FSRS)
    acquisition_box = Column(Integer, nullable=True)
    acquisition_next_due = Column(DateTime, nullable=True)

    # FSRS state (py-fsrs Card serialized to dict)
    fsrs_state_json = Column(JSON, nullable=True)

    # Unified scheduling pointer (earlier of acquisition_next_due or fsrs due)
    next_due = Column(DateTime, index=True)
    last_reviewed = Column(DateTime, nullable=True)

    times_seen = Column(Integer, default=0)
    times_correct = Column(Integer, default=0)
    times_wrong = Column(Integer, default=0)

    introduced_at = Column(DateTime, nullable=True)
    graduated_at = Column(DateTime, nullable=True)


class ReviewLog(Base):
    __tablename__ = "review_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    lemma_id = Column(Integer, ForeignKey("lemmas.id"), nullable=False, index=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=True)
    mode = Column(String(20))  # self_grade | multiple_choice
    rating = Column(Integer)  # 1=again, 2=hard, 3=good, 4=easy
    correct = Column(Boolean, nullable=True)  # for MC mode
    response_ms = Column(Integer)
    shown_at = Column(DateTime, default=utcnow, index=True)
