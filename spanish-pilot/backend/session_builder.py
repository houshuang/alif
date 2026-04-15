"""Session builder — ported from Alif's sentence_selector + session-building logic.

Keeps Alif's key behaviors:
  - Due cards first (overdue takes priority)
  - New lemma auto-intro (cap MAX_AUTO_INTRO_PER_SESSION = 5)
  - Intro cards interleaved BEFORE sentences using the new word (matches
    Alif's 2026-03-30 fix: interleaved, never front-loaded)
  - Sentence scoring boosts: NEVER_REVIEWED_BOOST (5.0x) for acquiring with 0
    reviews, LAPSED_BOOST (3.0x), overdue escalation up to 6.0x
  - Comprehensibility gate (target ≥60% of scaffold words known)
  - Greedy coverage: pick sentence that covers most due words first

Stripped from Alif: root cohort, grammar_fit (requires grammar_exposure data
we don't track yet), backlog-based gate tiers, rescue sentences (needs
cooldown tracking). Kept the load-bearing scoring pieces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Card, Lemma, Sentence, SentenceLemma

TARGET_SIZE = 18
MAX_AUTO_INTRO_PER_SESSION = 5
COMPREHENSIBILITY_MIN = 0.60

# Scoring boosts (direct port from Alif sentence_selector.py)
NEVER_REVIEWED_BOOST = 5.0
LAPSED_BOOST = 3.0
MAX_OVERDUE_BOOST = 6.0
SESSION_SCAFFOLD_DECAY = 0.5


@dataclass
class ReviewItem:
    kind: Literal["intro_card", "sentence"]
    # For intro cards and sentences both:
    lemma_id: int
    lemma_es: str
    is_new: bool
    # For sentence items:
    card_id: Optional[int] = None
    sentence_id: Optional[int] = None
    sentence_es: Optional[str] = None
    sentence_no: Optional[str] = None
    distractors_no: list[str] = field(default_factory=list)
    word_mapping: list[dict] = field(default_factory=list)


def _known_state_lemma_ids(db: Session, student_id: int) -> set[int]:
    """Lemmas the student has any review for (acquiring+, any state except 'new')."""
    rows = db.query(Card.lemma_id).filter(
        Card.student_id == student_id, Card.state != "new"
    ).all()
    return {r[0] for r in rows}


def _overdue_escalation(due_lemma_ids: set[int], overdue_days: dict[int, float]) -> float:
    """Ramp boost 1.0 → MAX_OVERDUE_BOOST as overdueness increases. Max across set."""
    if not due_lemma_ids:
        return 1.0
    max_days = 0.0
    for lid in due_lemma_ids:
        d = overdue_days.get(lid, 0.0)
        if d > max_days:
            max_days = d
    if max_days <= 0.5:
        return 1.0
    # Linear ramp from 0.5d → 1.0x to 7d+ → 6.0x
    t = min((max_days - 0.5) / 6.5, 1.0)
    return 1.0 + (MAX_OVERDUE_BOOST - 1.0) * t


def _score_sentence_for_lemma(
    sent: Sentence,
    target_ids: set[int],
    session_scaffold_counts: dict[int, int],
    never_reviewed_ids: set[int],
    lapsed_ids: set[int],
    overdue_days: dict[int, float],
    lemma_ids_in_sent: list[int],
    known_ids: set[int],
) -> float:
    """Compute score for this sentence's fit to the target set. Higher = better."""
    overlap = set(lemma_ids_in_sent) & target_ids
    if not overlap:
        return 0.0

    scaffold_ids = [lid for lid in lemma_ids_in_sent if lid not in target_ids]
    if scaffold_ids:
        known = sum(1 for lid in scaffold_ids if lid in known_ids)
        compr = known / len(scaffold_ids)
    else:
        compr = 1.0

    if compr < COMPREHENSIBILITY_MIN:
        return 0.0

    # Within-session scaffold diversity: penalize reuse of same scaffold words.
    if scaffold_ids:
        max_count = max(session_scaffold_counts.get(lid, 0) for lid in scaffold_ids)
        session_diversity = SESSION_SCAFFOLD_DECAY ** max_count
    else:
        session_diversity = 1.0

    nr_boost = NEVER_REVIEWED_BOOST if (overlap & never_reviewed_ids) else 1.0
    lapsed_boost = LAPSED_BOOST if (overlap & lapsed_ids) else 1.0
    overdue_boost = _overdue_escalation(overlap, overdue_days)

    # Core score: coverage^1.5 × diversity × session_diversity × boosts
    # difficulty penalty = pseudo comprehensibility of scaffold
    return (len(overlap) ** 1.5) * (0.5 + 0.5 * compr) * session_diversity * nr_boost * lapsed_boost * overdue_boost


def _sentence_lemma_ids_map(db: Session, sentence_ids: set[int]) -> dict[int, list[int]]:
    """Preload: sentence_id → list[lemma_id] (distinct)."""
    if not sentence_ids:
        return {}
    rows = db.query(SentenceLemma.sentence_id, SentenceLemma.lemma_id).filter(
        SentenceLemma.sentence_id.in_(sentence_ids)
    ).all()
    m: dict[int, list[int]] = {}
    for sid, lid in rows:
        m.setdefault(sid, []).append(lid)
    return m


def build_session(db: Session, student_id: int) -> list[ReviewItem]:
    """Assemble a review session. Returns mixed list of intro_card + sentence items."""
    now = datetime.utcnow()

    # --- 1) Collect due cards and gather classification (lapsed/never-reviewed/overdue) ---
    due_cards = (
        db.query(Card)
        .filter(Card.student_id == student_id)
        .filter(Card.state != "new")
        .filter(Card.next_due <= now)
        .order_by(Card.next_due.asc())
        .limit(TARGET_SIZE * 3)  # oversubscribe; final cap after sentence pairing
        .all()
    )

    due_ids: set[int] = set()
    never_reviewed_ids: set[int] = set()
    lapsed_ids: set[int] = set()
    overdue_days: dict[int, float] = {}
    cards_by_lemma: dict[int, Card] = {}
    for c in due_cards:
        due_ids.add(c.lemma_id)
        cards_by_lemma[c.lemma_id] = c
        if c.times_seen == 0 and c.state == "acquiring":
            never_reviewed_ids.add(c.lemma_id)
        if c.state == "lapsed":
            lapsed_ids.add(c.lemma_id)
        if c.next_due:
            od_days = (now - c.next_due).total_seconds() / 86400.0
            overdue_days[c.lemma_id] = max(0.0, od_days)

    known_ids = _known_state_lemma_ids(db, student_id)

    # --- 2) Pick candidate sentences for due lemmas ---
    # Find all sentences touching any due lemma
    candidate_sent_ids = {
        sid for (sid,) in db.query(SentenceLemma.sentence_id)
        .filter(SentenceLemma.lemma_id.in_(due_ids)).distinct().all()
    } if due_ids else set()
    sent_lemma_map = _sentence_lemma_ids_map(db, candidate_sent_ids)

    candidate_sents = db.query(Sentence).filter(Sentence.id.in_(candidate_sent_ids)).all() if candidate_sent_ids else []

    # --- 3) Greedy set cover ---
    selected_sents: list[tuple[Sentence, set[int]]] = []
    remaining_due = set(due_ids)
    session_scaffold_counts: dict[int, int] = {}
    used_sent_ids: set[int] = set()

    while remaining_due and len(selected_sents) < TARGET_SIZE:
        best: Optional[tuple[Sentence, float, set[int]]] = None
        for sent in candidate_sents:
            if sent.id in used_sent_ids:
                continue
            lemma_ids = sent_lemma_map.get(sent.id, [])
            overlap = set(lemma_ids) & remaining_due
            if not overlap:
                continue
            score = _score_sentence_for_lemma(
                sent, remaining_due, session_scaffold_counts,
                never_reviewed_ids, lapsed_ids, overdue_days,
                lemma_ids, known_ids,
            )
            if score <= 0:
                continue
            if best is None or score > best[1]:
                best = (sent, score, overlap)
        if best is None:
            break
        sent, _score, covered = best
        selected_sents.append((sent, covered))
        remaining_due -= covered
        used_sent_ids.add(sent.id)
        for lid in sent_lemma_map.get(sent.id, []):
            if lid not in covered:  # count non-target scaffold uses
                session_scaffold_counts[lid] = session_scaffold_counts.get(lid, 0) + 1

    # --- 4) New-word auto-intro — up to MAX_AUTO_INTRO_PER_SESSION ---
    introduced = {c.lemma_id for c in db.query(Card).filter(Card.student_id == student_id).all()}
    new_slots = min(MAX_AUTO_INTRO_PER_SESSION,
                    max(0, TARGET_SIZE - len(selected_sents)))

    new_lemmas: list[Lemma] = []
    if new_slots > 0:
        q = db.query(Lemma).order_by(Lemma.frequency_rank.asc())
        if introduced:
            q = q.filter(~Lemma.id.in_(introduced))
        new_lemmas = q.limit(new_slots * 3).all()

    # Pair each new lemma with a sentence that uses it (best comprehensibility)
    new_pairs: list[tuple[Lemma, Sentence]] = []
    for lem in new_lemmas:
        if len(new_pairs) >= new_slots:
            break
        sent_rows = (
            db.query(SentenceLemma.sentence_id)
            .filter(SentenceLemma.lemma_id == lem.id)
            .all()
        )
        sent_ids = [r[0] for r in sent_rows if r[0] not in used_sent_ids]
        if not sent_ids:
            continue
        # Pick sentence with best comprehensibility (most known scaffold)
        best_sent = None
        best_compr = -1.0
        for sid in sent_ids:
            lids = sent_lemma_map.get(sid) or [
                r[0] for r in db.query(SentenceLemma.lemma_id).filter(SentenceLemma.sentence_id == sid).all()
            ]
            sent_lemma_map.setdefault(sid, lids)
            non_target = [lid for lid in lids if lid != lem.id]
            if not non_target:
                compr = 1.0
            else:
                compr = sum(1 for lid in non_target if lid in known_ids or lid in introduced) / len(non_target)
            if compr > best_compr:
                best_compr = compr
                best_sent = db.query(Sentence).get(sid)
        if best_sent is None:
            continue
        new_pairs.append((lem, best_sent))
        used_sent_ids.add(best_sent.id)

    # --- 5) Build interleaved item list: intro_card then sentence for new words,
    #        plus pure-review sentences. Interleave new pairs among due sentences.
    items: list[ReviewItem] = []

    # Add due sentences first (core session)
    for sent, covered in selected_sents:
        target_lemma_id = next(iter(covered))
        target_lemma = db.query(Lemma).get(target_lemma_id)
        card = cards_by_lemma.get(target_lemma_id)
        items.append(ReviewItem(
            kind="sentence",
            lemma_id=target_lemma_id,
            lemma_es=target_lemma.lemma_es,
            card_id=card.id if card else None,
            sentence_id=sent.id,
            sentence_es=sent.es,
            sentence_no=sent.no,
            distractors_no=list(sent.distractors_no_json or []),
            word_mapping=list(sent.word_mapping_json or []),
            is_new=False,
        ))

    # Interleave new-word pairs: intro_card then sentence. Distribute evenly.
    if new_pairs and items:
        step = max(1, len(items) // (len(new_pairs) + 1))
        offset = step
        for lem, sent in new_pairs:
            # Pre-create card in 'new' state (first review will start it)
            card = Card(student_id=student_id, lemma_id=lem.id, state="new")
            db.add(card)
            db.flush()

            intro = ReviewItem(
                kind="intro_card", lemma_id=lem.id, lemma_es=lem.lemma_es, is_new=True,
            )
            sent_item = ReviewItem(
                kind="sentence", lemma_id=lem.id, lemma_es=lem.lemma_es, is_new=True,
                card_id=card.id, sentence_id=sent.id,
                sentence_es=sent.es, sentence_no=sent.no,
                distractors_no=list(sent.distractors_no_json or []),
                word_mapping=list(sent.word_mapping_json or []),
            )
            insert_at = min(offset, len(items))
            items.insert(insert_at, sent_item)
            items.insert(insert_at, intro)
            offset += step + 2
    elif new_pairs:
        # No due sentences — intro_card + sentence back to back for each new word
        for lem, sent in new_pairs:
            card = Card(student_id=student_id, lemma_id=lem.id, state="new")
            db.add(card)
            db.flush()
            items.append(ReviewItem(
                kind="intro_card", lemma_id=lem.id, lemma_es=lem.lemma_es, is_new=True,
            ))
            items.append(ReviewItem(
                kind="sentence", lemma_id=lem.id, lemma_es=lem.lemma_es, is_new=True,
                card_id=card.id, sentence_id=sent.id,
                sentence_es=sent.es, sentence_no=sent.no,
                distractors_no=list(sent.distractors_no_json or []),
                word_mapping=list(sent.word_mapping_json or []),
            ))

    db.commit()
    return items[:TARGET_SIZE + MAX_AUTO_INTRO_PER_SESSION]


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
        if c.next_due and c.next_due <= now and c.state not in {"new"}:
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
