"""Build review sessions for a student.

Strategy:
  1. Collect due cards (next_due <= now), prioritized: overdue first, then newer.
  2. If fewer than TARGET_SIZE, introduce new lemmas (by frequency rank),
     up to MAX_NEW_PER_SESSION.
  3. For each card, pick best sentence:
     - prefer sentences where >= COMPREHENSIBILITY of non-target lemmas are
       already known/acquiring (higher box) for this student
     - fallback: any sentence containing this lemma
  4. Cap session at TARGET_SIZE.

Output: list of review items — each has card + sentence + target lemma info.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from .models import Card, Lemma, Sentence, SentenceLemma

TARGET_SIZE = 18
MAX_NEW_PER_SESSION = 3
COMPREHENSIBILITY_MIN = 0.6  # fraction of non-target words known/acquiring


@dataclass
class ReviewItem:
    card_id: int
    lemma_id: int
    lemma_es: str
    sentence_id: int
    sentence_es: str
    sentence_no: str
    distractors_no: list[str]
    word_mapping: list[dict]
    is_new: bool  # first-ever review of this lemma


def _known_lemma_ids(db: Session, student_id: int) -> set[int]:
    """Lemmas the student has any non-new card for (acquiring+)."""
    rows = db.query(Card.lemma_id).filter(
        Card.student_id == student_id,
        Card.state != "new",
    ).all()
    return {r[0] for r in rows}


def _pick_sentence_for_lemma(
    db: Session, lemma_id: int, known_ids: set[int], used_sentence_ids: set[int]
) -> Optional[Sentence]:
    """Pick a sentence containing this lemma, preferring high comprehensibility."""
    # All sentences containing the target lemma
    candidates = (
        db.query(Sentence)
        .join(SentenceLemma, SentenceLemma.sentence_id == Sentence.id)
        .filter(SentenceLemma.lemma_id == lemma_id)
        .all()
    )
    candidates = [s for s in candidates if s.id not in used_sentence_ids]
    if not candidates:
        return None

    best = None
    best_score = -1.0
    for s in candidates:
        # Get all lemma_ids in this sentence
        lemma_rows = db.query(SentenceLemma.lemma_id).filter(SentenceLemma.sentence_id == s.id).all()
        lemma_ids_in_s = [r[0] for r in lemma_rows]
        non_target = [lid for lid in lemma_ids_in_s if lid != lemma_id]
        if not non_target:
            score = 1.0
        else:
            known_count = sum(1 for lid in non_target if lid in known_ids)
            score = known_count / len(non_target)

        # Small preference for shorter sentences (lower difficulty_rank)
        # when comprehensibility ties — cheap tiebreaker
        score_adjusted = score - (s.difficulty_rank or 0) * 0.0001

        if score_adjusted > best_score:
            best_score = score_adjusted
            best = s

    # If best comprehensibility < MIN but we have no alternative, still return it.
    return best


def build_session(db: Session, student_id: int) -> list[ReviewItem]:
    now = datetime.utcnow()

    # Phase 1: due cards (any state except new)
    due_cards = (
        db.query(Card)
        .filter(Card.student_id == student_id)
        .filter(Card.state != "new")
        .filter(Card.next_due <= now)
        .order_by(Card.next_due.asc())
        .limit(TARGET_SIZE)
        .all()
    )

    # Phase 2: new lemma introductions
    introduced_lemma_ids = {c.lemma_id for c in db.query(Card.lemma_id).filter(Card.student_id == student_id).all()}
    new_slots = min(MAX_NEW_PER_SESSION, TARGET_SIZE - len(due_cards))
    new_lemmas = []
    if new_slots > 0:
        new_lemmas = (
            db.query(Lemma)
            .filter(~Lemma.id.in_(introduced_lemma_ids) if introduced_lemma_ids else True)
            .order_by(Lemma.frequency_rank.asc())
            .limit(new_slots)
            .all()
        )

    # Phase 3: build ReviewItems
    known_ids = _known_lemma_ids(db, student_id)
    used_sentences: set[int] = set()
    items: list[ReviewItem] = []

    for card in due_cards:
        s = _pick_sentence_for_lemma(db, card.lemma_id, known_ids, used_sentences)
        if s is None:
            continue
        used_sentences.add(s.id)
        lem = db.query(Lemma).get(card.lemma_id)
        items.append(ReviewItem(
            card_id=card.id,
            lemma_id=card.lemma_id,
            lemma_es=lem.lemma_es,
            sentence_id=s.id,
            sentence_es=s.es,
            sentence_no=s.no,
            distractors_no=list(s.distractors_no_json or []),
            word_mapping=list(s.word_mapping_json or []),
            is_new=False,
        ))

    for lem in new_lemmas:
        # Create a pending card (state='new'); persisted when first reviewed.
        # But we need a card_id for the frontend — so create now.
        card = Card(student_id=student_id, lemma_id=lem.id, state="new", times_seen=0)
        db.add(card)
        db.flush()

        s = _pick_sentence_for_lemma(db, lem.id, known_ids, used_sentences)
        if s is None:
            # no sentence available — skip
            db.delete(card)
            continue
        used_sentences.add(s.id)
        items.append(ReviewItem(
            card_id=card.id,
            lemma_id=lem.id,
            lemma_es=lem.lemma_es,
            sentence_id=s.id,
            sentence_es=s.es,
            sentence_no=s.no,
            distractors_no=list(s.distractors_no_json or []),
            word_mapping=list(s.word_mapping_json or []),
            is_new=True,
        ))

    db.commit()
    return items


def dashboard_stats(db: Session, student_id: int) -> dict:
    cards = db.query(Card).filter(Card.student_id == student_id).all()
    by_state: dict[str, int] = {}
    by_box: dict[int, int] = {1: 0, 2: 0, 3: 0}
    due_now = 0
    now = datetime.utcnow()
    for c in cards:
        by_state[c.state] = by_state.get(c.state, 0) + 1
        if c.state == "acquiring" and c.acquisition_box:
            by_box[c.acquisition_box] = by_box.get(c.acquisition_box, 0) + 1
        if c.next_due and c.next_due <= now and c.state != "new":
            due_now += 1

    total_lemmas = db.query(func.count(Lemma.id)).scalar() or 0
    return {
        "total_lemmas": total_lemmas,
        "introduced": len([c for c in cards if c.state != "new"]),
        "known": by_state.get("known", 0),
        "learning": by_state.get("learning", 0),
        "acquiring": by_state.get("acquiring", 0),
        "lapsed": by_state.get("lapsed", 0),
        "due_now": due_now,
        "leitner_boxes": by_box,
    }
