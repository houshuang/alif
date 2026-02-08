"""Sentence-centric session assembly.

Selects a review session of sentences that maximally cover due words,
ordered for good learning flow (easy -> hard -> easy).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.models import (
    Lemma,
    Sentence,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.interaction_logger import log_interaction


@dataclass
class WordMeta:
    lemma_id: Optional[int]
    surface_form: str
    gloss_en: Optional[str]
    stability: Optional[float]
    is_due: bool
    is_function_word: bool = False


@dataclass
class SentenceCandidate:
    sentence_id: int
    sentence: object
    words_meta: list[WordMeta] = field(default_factory=list)
    due_words_covered: set[int] = field(default_factory=set)
    score: float = 0.0


def _get_stability(knowledge: Optional[UserLemmaKnowledge]) -> float:
    if not knowledge or not knowledge.fsrs_card_json:
        return 0.0
    card_data = knowledge.fsrs_card_json
    if isinstance(card_data, str):
        card_data = json.loads(card_data)
    return card_data.get("stability", 0.0)


def _get_due_dt(knowledge: UserLemmaKnowledge) -> Optional[datetime]:
    card_data = knowledge.fsrs_card_json
    if not card_data:
        return None
    if isinstance(card_data, str):
        card_data = json.loads(card_data)
    due_str = card_data.get("due")
    if not due_str:
        return None
    dt = datetime.fromisoformat(due_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _difficulty_match_quality(
    weakest_stability: float,
    scaffold_stabilities: list[float],
) -> float:
    """Score how well scaffold words match the difficulty needs of the weakest due word.

    Thresholds are low to work for early learners (days/weeks of study).
    Scaffold words just need to be somewhat more stable than the weakest due word.
    """
    if not scaffold_stabilities:
        return 1.0

    avg_scaffold = sum(scaffold_stabilities) / len(scaffold_stabilities)

    if weakest_stability < 0.5:
        # Very fragile word: prefer scaffolds with at least 1 day stability
        if any(s < 0.5 for s in scaffold_stabilities):
            return 0.3
        return 1.0
    elif weakest_stability < 3.0:
        # Still shaky: scaffolds should average above weakest
        if avg_scaffold < weakest_stability:
            return 0.5
        return 1.0
    else:
        return 1.0


def build_session(
    db: Session,
    limit: int = 10,
    mode: str = "reading",
) -> dict:
    """Assemble a sentence-based review session.

    Returns a dict matching SentenceSessionOut schema:
    {session_id, items, total_due_words, covered_due_words}
    """
    session_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)

    # 1. Fetch all due words
    all_knowledge = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
        .all()
    )

    due_lemma_ids: set[int] = set()
    stability_map: dict[int, float] = {}

    for k in all_knowledge:
        stability_map[k.lemma_id] = _get_stability(k)
        due_dt = _get_due_dt(k)
        if due_dt and due_dt <= now:
            due_lemma_ids.add(k.lemma_id)

    total_due = len(due_lemma_ids)

    if not due_lemma_ids:
        return {
            "session_id": session_id,
            "items": [],
            "total_due_words": 0,
            "covered_due_words": 0,
        }

    # 2. Fetch candidate sentences containing at least one due word
    sentence_words = (
        db.query(SentenceWord)
        .filter(SentenceWord.lemma_id.in_(due_lemma_ids))
        .all()
    )

    sentence_ids_with_due = {sw.sentence_id for sw in sentence_words}
    if not sentence_ids_with_due:
        return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, [], limit)

    sentences = (
        db.query(Sentence)
        .filter(
            Sentence.id.in_(sentence_ids_with_due),
            (Sentence.last_shown_at.is_(None)) | (Sentence.last_shown_at < cutoff),
        )
        .all()
    )

    if not sentences:
        return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, [], limit)

    sentence_map: dict[int, Sentence] = {s.id: s for s in sentences}

    # Load all sentence words for these sentences
    all_sw = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id.in_(sentence_map.keys()))
        .order_by(SentenceWord.sentence_id, SentenceWord.position)
        .all()
    )

    sw_by_sentence: dict[int, list[SentenceWord]] = {}
    for sw in all_sw:
        sw_by_sentence.setdefault(sw.sentence_id, []).append(sw)

    # Load lemma info
    all_lemma_ids = {sw.lemma_id for sw in all_sw if sw.lemma_id}
    all_lemma_ids |= due_lemma_ids
    lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(all_lemma_ids)).all() if all_lemma_ids else []
    lemma_map = {l.lemma_id: l for l in lemmas}

    # Build candidates
    candidates: list[SentenceCandidate] = []
    for sent in sentences:
        sws = sw_by_sentence.get(sent.id, [])
        due_covered: set[int] = set()
        word_metas: list[WordMeta] = []
        scaffold_stabilities: list[float] = []

        for sw in sws:
            lemma = lemma_map.get(sw.lemma_id) if sw.lemma_id else None
            stab = stability_map.get(sw.lemma_id, 0.0) if sw.lemma_id else None
            is_due = sw.lemma_id in due_lemma_ids if sw.lemma_id else False

            wm = WordMeta(
                lemma_id=sw.lemma_id,
                surface_form=sw.surface_form,
                gloss_en=lemma.gloss_en if lemma else None,
                stability=stab,
                is_due=is_due,
                is_function_word=sw.lemma_id is None,
            )
            word_metas.append(wm)

            if sw.lemma_id and is_due:
                due_covered.add(sw.lemma_id)
            elif sw.lemma_id and stab is not None:
                scaffold_stabilities.append(stab)

        if not due_covered:
            continue

        weakest = min(stability_map.get(lid, 0.0) for lid in due_covered)
        dmq = _difficulty_match_quality(weakest, scaffold_stabilities)
        score = (len(due_covered) ** 1.5) * dmq

        candidates.append(SentenceCandidate(
            sentence_id=sent.id,
            sentence=sent,
            words_meta=word_metas,
            due_words_covered=due_covered,
            score=score,
        ))

    # 3. Greedy set cover
    selected: list[SentenceCandidate] = []
    remaining_due = set(due_lemma_ids)

    while remaining_due and len(selected) < limit and candidates:
        for c in candidates:
            overlap = c.due_words_covered & remaining_due
            if not overlap:
                c.score = 0.0
                continue
            weakest = min(stability_map.get(lid, 0.0) for lid in overlap)
            scaffold_stabs = [w.stability for w in c.words_meta
                              if w.lemma_id and not w.is_due and w.stability is not None]
            dmq = _difficulty_match_quality(weakest, scaffold_stabs)
            c.score = (len(overlap) ** 1.5) * dmq

        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        if best.score <= 0:
            break

        selected.append(best)
        remaining_due -= best.due_words_covered
        candidates.remove(best)

        log_interaction(
            event="sentence_selected",
            session_id=session_id,
            sentence_id=best.sentence_id,
            selection_order=len(selected),
            score=round(best.score, 3),
            due_words_covered=len(best.due_words_covered),
            remaining_due=len(remaining_due),
        )

    # Track covered
    covered_ids: set[int] = set()
    for c in selected:
        covered_ids |= c.due_words_covered

    # 4. Order: easy bookends, hard in middle
    ordered = _order_session(selected, stability_map)

    # Build response items + update last_shown_at
    items: list[dict] = []
    for cand in ordered:
        sent = sentence_map[cand.sentence_id]
        sent.last_shown_at = now
        sent.times_shown = (sent.times_shown or 0) + 1

        primary_lid = sent.target_lemma_id
        if primary_lid not in due_lemma_ids and cand.due_words_covered:
            primary_lid = next(iter(cand.due_words_covered))

        primary_lemma = lemma_map.get(primary_lid)

        word_dicts = [
            {
                "lemma_id": w.lemma_id,
                "surface_form": w.surface_form,
                "gloss_en": w.gloss_en,
                "stability": w.stability,
                "is_due": w.is_due,
                "is_function_word": w.is_function_word,
            }
            for w in cand.words_meta
        ]

        items.append({
            "sentence_id": cand.sentence_id,
            "arabic_text": sent.arabic_text,
            "arabic_diacritized": sent.arabic_diacritized,
            "english_translation": sent.english_translation or "",
            "transliteration": sent.transliteration,
            "audio_url": sent.audio_url,
            "primary_lemma_id": primary_lid,
            "primary_lemma_ar": primary_lemma.lemma_ar if primary_lemma else "",
            "primary_gloss_en": primary_lemma.gloss_en if primary_lemma else "",
            "words": word_dicts,
        })

    db.commit()

    return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, items, limit, covered_ids)


def _with_fallbacks(
    db: Session,
    session_id: str,
    due_lemma_ids: set[int],
    stability_map: dict[int, float],
    total_due: int,
    items: list[dict],
    limit: int,
    covered_ids: set[int] | None = None,
) -> dict:
    """Add word-only fallback items for uncovered due words."""
    if covered_ids is None:
        covered_ids = set()

    uncovered = due_lemma_ids - covered_ids
    for lid in uncovered:
        if len(items) >= limit:
            break
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
        if lemma is None:
            continue
        items.append({
            "sentence_id": None,
            "arabic_text": lemma.lemma_ar,
            "arabic_diacritized": lemma.lemma_ar,
            "english_translation": lemma.gloss_en or "",
            "transliteration": lemma.transliteration_ala_lc,
            "primary_lemma_id": lid,
            "primary_lemma_ar": lemma.lemma_ar,
            "primary_gloss_en": lemma.gloss_en or "",
            "words": [{
                "lemma_id": lid,
                "surface_form": lemma.lemma_ar,
                "gloss_en": lemma.gloss_en,
                "stability": stability_map.get(lid, 0.0),
                "is_due": True,
                "is_function_word": False,
            }],
        })
        covered_ids.add(lid)

    return {
        "session_id": session_id,
        "items": items,
        "total_due_words": total_due,
        "covered_due_words": len(covered_ids),
    }


def _order_session(
    selected: list[SentenceCandidate],
    stability_map: dict[int, float],
) -> list[SentenceCandidate]:
    """Order sentences: easy bookends, hard in the middle."""
    if len(selected) <= 2:
        return selected

    def min_due_stability(c: SentenceCandidate) -> float:
        due_stabs = [stability_map.get(lid, 0.0) for lid in c.due_words_covered]
        return min(due_stabs) if due_stabs else 0.0

    sorted_by_difficulty = sorted(selected, key=min_due_stability, reverse=True)

    start = [sorted_by_difficulty[0]]
    end = [sorted_by_difficulty[1]] if len(sorted_by_difficulty) > 1 else []
    middle = sorted_by_difficulty[2:]

    return start + middle + end
