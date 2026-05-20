"""Sentence picker + minimal session builder.

Read-side spine of the sentence-review pipeline (PR #3). Given a due lemma,
pick the best Sentence row to put in front of the learner. Given a desired
session size, walk the due list and assemble a list of sentences.

Deliberately scoped down from Alif's 2,864-line ``sentence_selector.py``:

- No intro cards, no reintros, no passages, no grammar-slot weighting.
- No frequency-lane / awzān / clitic / Hindawi-tier machinery.
- No diversity scoring or near-duplicate veto beyond "don't repeat the same
  sentence in one session".
- No generation fallback — when nothing fits, return ``None`` and let PR #4's
  material generator fill the gap.

The three gates the picker DOES honor (Hard Invariants from
``polyglot/CLAUDE.md``):

- **Reviewability**: ``Sentence.is_active`` AND ``mappings_verified_at IS NOT
  NULL``. ``sentence_harvest`` already excludes caps-heading sentences at
  storage, so we don't re-filter here.
- **Canonical at entry**: the caller-supplied ``lemma_id`` is redirected
  through ``resolve_canonical_lemma_id`` before any SentenceWord query.
  Hard Invariant #9.
- **Function-word / proper-name scaffold skip**: comprehensibility counts only
  content lemmas. ``FUNCTION_WORD_SETS[language_code]`` and
  ``Lemma.word_category in ('function_word', 'proper_name')`` both exclude.

Source preference (per 2026-05-20 spec):

1. **Page-first, all-known**: a textbook page sentence (``Sentence.page_id IS
   NOT NULL``) whose every content scaffold lemma is already known →
   ``known`` or ``learning`` state. Zero-friction: the user has already read
   that page.
2. **Any harvested sentence**, ranked by comprehensibility × source bonus.
   Lower-comprehensibility sentences still rank above nothing — the learner
   benefits from collateral scaffold even when not every word is known.
3. **None**, when no eligible sentence covers the lemma. Caller should fall
   back to generation (PR #4) or skip the lemma for this session.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge, Language
from app.services.canonical_resolution import resolve_canonical_lemma_id
from app.services.fsrs_service import parse_json_column
from app.services.lemma_quality import FUNCTION_WORD_SETS

logger = logging.getLogger(__name__)


KNOWN_STATES = frozenset({"known", "learning"})
DEFAULT_SESSION_LIMIT = 15

PAGE_FIRST_BONUS = 3.0
SOURCE_BONUS = {
    "textbook": 1.4,
    "story": 1.2,
    "import": 1.2,
    "manual": 1.0,
    "llm": 1.0,
}
DEFAULT_SOURCE_BONUS = 1.0


@dataclass
class WordRender:
    """Per-word payload returned to the frontend."""
    position: int
    surface_form: str
    lemma_id: Optional[int]
    lemma_form: Optional[str]
    gloss_en: Optional[str]
    is_target: bool
    is_function_word: bool
    is_proper_name: bool
    knowledge_state: str


@dataclass
class SentencePayload:
    sentence_id: int
    text: str
    translation_en: Optional[str]
    target_lemma_id: int
    source: Optional[str]
    page_id: Optional[int]
    words: list[WordRender] = field(default_factory=list)
    selection_reason: str = ""
    score: float = 0.0


@dataclass
class _Scored:
    sentence: Sentence
    score: float
    comprehensibility: float
    scaffold_total: int
    scaffold_known: int
    page_first: bool
    selection_reason: str


def pick_sentence_for_lemma(
    db: Session,
    lemma_id: int,
    language_code: str,
    exclude_sentence_ids: Optional[set[int]] = None,
) -> Optional[SentencePayload]:
    """Pick the best sentence covering ``lemma_id`` for the learner.

    Variant ``lemma_id`` is redirected to canonical at function entry.
    Returns ``None`` if no eligible Sentence row exists — caller should
    defer to generation (PR #4) or skip this lemma in the session.
    """
    canonical_id = resolve_canonical_lemma_id(db, lemma_id)
    if canonical_id is None:
        return None

    exclude = set(exclude_sentence_ids or ())

    candidate_rows: list[Sentence] = (
        db.query(Sentence)
        .join(SentenceWord, SentenceWord.sentence_id == Sentence.id)
        .filter(
            Sentence.language_code == language_code,
            Sentence.is_active.is_(True),
            Sentence.mappings_verified_at.isnot(None),
            SentenceWord.lemma_id == canonical_id,
        )
        .options(joinedload(Sentence.words))
        .distinct()
        .all()
    )
    candidates = [s for s in candidate_rows if s.id not in exclude]
    if not candidates:
        return None

    function_words = FUNCTION_WORD_SETS.get(language_code, set())

    all_lemma_ids: set[int] = set()
    for sent in candidates:
        for sw in sent.words:
            if sw.lemma_id is not None:
                all_lemma_ids.add(sw.lemma_id)

    lemmas_by_id: dict[int, Lemma] = {}
    ulks_by_lemma: dict[int, UserLemmaKnowledge] = {}
    if all_lemma_ids:
        for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(all_lemma_ids)).all():
            lemmas_by_id[lemma.lemma_id] = lemma
        for ulk in (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id.in_(all_lemma_ids))
            .all()
        ):
            ulks_by_lemma[ulk.lemma_id] = ulk

    scored: list[_Scored] = []
    for sent in candidates:
        result = _score_candidate(
            sentence=sent,
            target_canonical_id=canonical_id,
            lemmas_by_id=lemmas_by_id,
            ulks_by_lemma=ulks_by_lemma,
            function_words=function_words,
        )
        if result is not None:
            scored.append(result)

    if not scored:
        return None

    scored.sort(
        key=lambda c: (c.score, c.scaffold_total, -c.sentence.id),
        reverse=True,
    )
    best = scored[0]
    return _build_payload(
        best=best,
        target_canonical_id=canonical_id,
        lemmas_by_id=lemmas_by_id,
        ulks_by_lemma=ulks_by_lemma,
        function_words=function_words,
    )


def _score_candidate(
    sentence: Sentence,
    target_canonical_id: int,
    lemmas_by_id: dict[int, Lemma],
    ulks_by_lemma: dict[int, UserLemmaKnowledge],
    function_words: set,
) -> Optional[_Scored]:
    has_target = False
    scaffold_total = 0
    scaffold_known = 0

    for sw in sentence.words:
        if sw.lemma_id is None:
            continue
        if sw.lemma_id == target_canonical_id:
            has_target = True
            continue
        lemma = lemmas_by_id.get(sw.lemma_id)
        if lemma is None:
            continue
        if lemma.word_category in ("function_word", "proper_name"):
            continue
        if lemma.lemma_bare in function_words:
            continue

        scaffold_total += 1
        ulk = ulks_by_lemma.get(sw.lemma_id)
        if ulk is not None and ulk.knowledge_state in KNOWN_STATES:
            scaffold_known += 1

    if not has_target:
        return None

    if scaffold_total == 0:
        comprehensibility = 1.0
        all_known = True
    else:
        comprehensibility = scaffold_known / scaffold_total
        all_known = (scaffold_known == scaffold_total)

    base = 0.3 + 0.7 * comprehensibility
    source_bonus = SOURCE_BONUS.get(sentence.source or "", DEFAULT_SOURCE_BONUS)
    page_first = sentence.page_id is not None and all_known
    page_bonus = PAGE_FIRST_BONUS if page_first else 1.0

    score = base * source_bonus * page_bonus

    if page_first:
        reason = "page_first_all_known"
    elif all_known:
        reason = "all_scaffold_known"
    elif comprehensibility >= 0.6:
        reason = "comprehensible"
    else:
        reason = "best_available"

    return _Scored(
        sentence=sentence,
        score=score,
        comprehensibility=comprehensibility,
        scaffold_total=scaffold_total,
        scaffold_known=scaffold_known,
        page_first=page_first,
        selection_reason=reason,
    )


def _build_payload(
    best: _Scored,
    target_canonical_id: int,
    lemmas_by_id: dict[int, Lemma],
    ulks_by_lemma: dict[int, UserLemmaKnowledge],
    function_words: set,
) -> SentencePayload:
    sent = best.sentence
    words: list[WordRender] = []
    for sw in sorted(sent.words, key=lambda w: w.position):
        lemma = lemmas_by_id.get(sw.lemma_id) if sw.lemma_id else None
        ulk = ulks_by_lemma.get(sw.lemma_id) if sw.lemma_id else None
        is_proper_name = bool(lemma and lemma.word_category == "proper_name")
        is_function_word = bool(
            (lemma and lemma.word_category == "function_word")
            or (lemma and lemma.lemma_bare in function_words)
        )
        words.append(
            WordRender(
                position=sw.position,
                surface_form=sw.surface_form,
                lemma_id=sw.lemma_id,
                lemma_form=lemma.lemma_form if lemma else None,
                gloss_en=lemma.gloss_en if lemma else None,
                is_target=sw.lemma_id == target_canonical_id,
                is_function_word=is_function_word,
                is_proper_name=is_proper_name,
                knowledge_state=ulk.knowledge_state if ulk else "new",
            )
        )

    return SentencePayload(
        sentence_id=sent.id,
        text=sent.text,
        translation_en=sent.translation_en,
        target_lemma_id=target_canonical_id,
        source=sent.source,
        page_id=sent.page_id,
        words=words,
        selection_reason=best.selection_reason,
        score=best.score,
    )


def _fsrs_due_lemmas(
    db: Session,
    language_code: str,
    now: datetime,
    limit: int,
) -> list[tuple[UserLemmaKnowledge, Lemma, datetime]]:
    """FSRS-due rows: walk learning/known/lapsed cards and filter by due_dt.

    Mirrors the iteration shape used in the /api/reviews/due endpoint, which
    has to deserialize each fsrs_card_json because the due timestamp lives
    inside the JSON blob (a py-fsrs v6 detail).
    """
    candidates = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "lapsed"]),
            UserLemmaKnowledge.fsrs_card_json.isnot(None),
        )
        .all()
    )
    rows: list[tuple[UserLemmaKnowledge, Lemma, datetime]] = []
    for ulk, lemma in candidates:
        card = parse_json_column(ulk.fsrs_card_json)
        due_str = card.get("due")
        if not due_str:
            continue
        try:
            due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        if due_dt <= now:
            rows.append((ulk, lemma, due_dt))
    rows.sort(key=lambda t: t[2])
    return rows[:limit]


def _acquisition_due_lemmas(
    db: Session,
    language_code: str,
    now: datetime,
    limit: int,
) -> list[tuple[UserLemmaKnowledge, Lemma]]:
    return (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_next_due.isnot(None),
            UserLemmaKnowledge.acquisition_next_due <= now,
        )
        .order_by(
            UserLemmaKnowledge.acquisition_box.asc(),
            UserLemmaKnowledge.acquisition_next_due.asc(),
        )
        .limit(limit)
        .all()
    )


def build_session(
    db: Session,
    language_code: str,
    limit: int = DEFAULT_SESSION_LIMIT,
) -> list[SentencePayload]:
    """Assemble a session: pick one sentence per due lemma, up to ``limit``.

    Order: acquisition-due first (Box 1 → 2 → 3, then by due time), then
    FSRS-due (oldest due first). For each lemma we call the picker; if the
    picker returns ``None`` (no eligible sentence covers it yet), the lemma
    is skipped for this session — it will reappear once material exists.

    Within a single session we don't repeat the same sentence even if two
    due lemmas happen to share a candidate. The duplicate-avoidance shape
    mirrors Alif's ``selected_sentence_ids`` pattern but without the
    diversity/Jaccard machinery.
    """
    if not db.query(Language).filter(Language.code == language_code).first():
        return []

    now = datetime.now(timezone.utc)

    acquiring = _acquisition_due_lemmas(db, language_code, now, limit)
    remaining = max(0, limit - len(acquiring))
    fsrs = _fsrs_due_lemmas(db, language_code, now, remaining) if remaining else []

    selected: list[SentencePayload] = []
    used_sentence_ids: set[int] = set()

    for ulk, lemma in acquiring:
        if len(selected) >= limit:
            break
        payload = pick_sentence_for_lemma(
            db,
            lemma_id=lemma.lemma_id,
            language_code=language_code,
            exclude_sentence_ids=used_sentence_ids,
        )
        if payload is None:
            continue
        used_sentence_ids.add(payload.sentence_id)
        selected.append(payload)

    for ulk, lemma, _due in fsrs:
        if len(selected) >= limit:
            break
        payload = pick_sentence_for_lemma(
            db,
            lemma_id=lemma.lemma_id,
            language_code=language_code,
            exclude_sentence_ids=used_sentence_ids,
        )
        if payload is None:
            continue
        used_sentence_ids.add(payload.sentence_id)
        selected.append(payload)

    return selected
