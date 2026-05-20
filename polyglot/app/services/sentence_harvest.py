"""Harvest reviewable sentences from textbook pages.

The Sentence/SentenceWord tables sit ready to feed the sentence-review pipeline,
but reading-intake only populates Page + PageWord — sentences had no source until
this service. This is the cheap path: leverage authentic imported text plus the
already-verified PageWord → Lemma mappings, instead of asking an LLM to generate
sentences for words the user encountered in real reading.

Hard invariants honoured:

- **Reviewability gate**: a page must have `mappings_verified_at` set (i.e. the
  quality gate passed) before its sentences become reviewable. We stamp the
  harvested Sentence's `mappings_verified_at` from the Page's, so a sentence
  with unverified words is never produced.
- **Canonical scheduling**: every SentenceWord stores the canonical lemma_id,
  not the variant. The resolver redirect happens at SentenceWord creation so
  the review-credit pipeline sees only canonicals (mirrors Hard Invariant #9
  applied at storage rather than read time — cheap because pages are small).
- **Heading exclusion**: caps-heading sentences detected by the quality gate
  carry no vocabulary value and should not surface as review material. We
  reuse `_detect_heading_sentence_indices` from `lemma_quality`.
- **Idempotency**: re-running for the same page is a no-op. The
  `(page_id, sentence_index_in_page)` unique constraint enforces it at the DB
  level; the service short-circuits earlier via a count check to avoid the
  IntegrityError path entirely.

When sentences are LLM-generated later (PR #4 — material_generator port),
their `page_id` will be NULL — the unique constraint still works because
SQLite treats NULL as distinct in unique indexes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Lemma, Page, PageWord, Sentence, SentenceWord
from app.services.canonical_resolution import resolve_canonical_via_map
from app.services.lemma_quality import _detect_heading_sentence_indices

log = logging.getLogger(__name__)


def _reconstruct_sentence_text(words: list[PageWord]) -> str:
    """Join surface forms in position order. Punctuation tokens attach to the
    preceding word without a leading space; other tokens get a separating
    space. Greek punctuation set covers `. , ; ! ? · " — ( ) [ ] : -`. The
    output is good for display and for the eventual translation LLM call;
    perfect whitespace fidelity to the source PDF is not a goal.
    """
    if not words:
        return ""
    sorted_words = sorted(words, key=lambda w: w.position)
    parts: list[str] = [sorted_words[0].surface_form]
    for w in sorted_words[1:]:
        s = w.surface_form
        is_punct = bool(s) and not any(c.isalpha() or c.isdigit() for c in s)
        if is_punct:
            parts.append(s)
        else:
            parts.append(" " + s)
    return "".join(parts).strip()


def _canonical_map_for(db: Session, lemma_ids: set[int]) -> dict[int, int | None]:
    """Pre-load canonical_lemma_id for a set of lemma_ids. Used so the harvest
    loop resolves canonical without N queries per page."""
    if not lemma_ids:
        return {}
    rows = (
        db.query(Lemma.lemma_id, Lemma.canonical_lemma_id)
        .filter(Lemma.lemma_id.in_(lemma_ids))
        .all()
    )
    return {lid: canonical for lid, canonical in rows}


def harvest_page_sentences(db: Session, page: Page, *, force: bool = False) -> int:
    """Create Sentence + SentenceWord rows from a page's PageWord rows.

    Returns the number of Sentence rows created (0 if already harvested or
    page not verified). Idempotent unless `force=True`, in which case existing
    rows for the page are deleted first and the harvest replayed.

    Pre-conditions:
        - Page must have `mappings_verified_at` set (quality gate passed).
          Without verification, sentence-word lemma assignments may be wrong
          and the sentence would violate the reviewability gate. Returns 0
          and logs at INFO if missing.

    The function holds no LLM/network calls — pure DB compute. Per CLAUDE.md
    Rule #10 (SQLite write lock discipline), no slow work happens between
    read and commit.
    """
    if page.mappings_verified_at is None:
        log.info(
            "harvest_page_sentences skipping page %d (mappings_verified_at is NULL)",
            page.id,
        )
        return 0

    if force:
        db.query(Sentence).filter(Sentence.page_id == page.id).delete()
        db.commit()
    else:
        existing = (
            db.query(Sentence).filter(Sentence.page_id == page.id).count()
        )
        if existing > 0:
            return 0

    page_words = (
        db.query(PageWord)
        .filter(PageWord.page_id == page.id)
        .order_by(PageWord.position)
        .all()
    )
    if not page_words:
        return 0

    heading_indices = _detect_heading_sentence_indices(page_words)

    by_idx: dict[int, list[PageWord]] = {}
    for w in page_words:
        by_idx.setdefault(w.sentence_index, []).append(w)

    lemma_ids = {w.lemma_id for w in page_words if w.lemma_id is not None}
    canonical_map = _canonical_map_for(db, lemma_ids)

    now = datetime.now(timezone.utc)
    language_code = page.story.language_code
    created = 0

    for s_idx in sorted(by_idx):
        if s_idx in heading_indices:
            continue
        words = by_idx[s_idx]
        # Sentences with no content lemmas are pure-punctuation artefacts from
        # the tokenizer (e.g. a line break treated as its own sentence).
        if not any(w.lemma_id is not None for w in words):
            continue

        text = _reconstruct_sentence_text(words)
        if not text:
            continue

        sentence = Sentence(
            language_code=language_code,
            text=text,
            source="textbook",
            story_id=page.story_id,
            page_id=page.id,
            sentence_index_in_page=s_idx,
            is_active=True,
            mappings_verified_at=page.mappings_verified_at,
        )
        db.add(sentence)
        db.flush()

        for w in sorted(words, key=lambda x: x.position):
            lemma_id = w.lemma_id
            if lemma_id is not None:
                lemma_id = resolve_canonical_via_map(lemma_id, canonical_map)
            db.add(SentenceWord(
                sentence_id=sentence.id,
                position=w.position,
                surface_form=w.surface_form,
                lemma_id=lemma_id,
                is_target_word=False,
            ))
        created += 1

    db.commit()
    log.info(
        "Harvested %d sentences from page %d (story %d)",
        created, page.id, page.story_id,
    )
    return created


def harvest_story_sentences(db: Session, story_id: int, *, force: bool = False) -> int:
    """Harvest sentences from every verified page in a story. Returns the
    total count of new Sentence rows across the story."""
    pages = (
        db.query(Page)
        .filter(Page.story_id == story_id)
        .filter(Page.mappings_verified_at.isnot(None))
        .order_by(Page.page_number)
        .all()
    )
    total = 0
    for page in pages:
        total += harvest_page_sentences(db, page, force=force)
    return total
