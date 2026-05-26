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
- **Page-boundary exclusion**: sentences cut by PDF page breaks are omitted
  rather than stored as reusable review material.
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
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Lemma, Page, PageWord, Sentence, SentenceWord
from app.services import body_clean as body_clean_svc
from app.services.canonical_resolution import resolve_canonical_via_map
from app.services.lemma_quality import _detect_heading_sentence_indices

log = logging.getLogger(__name__)


_SENTENCE_TERMINAL_RE = re.compile(r"""[.!?;·»”’")\]]\s*$""")


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


def _page_text_for_boundary(page: Page) -> str:
    raw = (
        page.body_clean
        if page.body_clean and page.body_clean.strip()
        else page.body_src
    )
    return body_clean_svc.normalize_pdf_artifacts(raw or "", collapse_whitespace=True)


def _ends_with_sentence_terminal(text: str) -> bool:
    return bool(_SENTENCE_TERMINAL_RE.search((text or "").strip()))


def _detect_page_boundary_sentence_indices(
    db: Session,
    page: Page,
    page_words: list[PageWord],
) -> set[int]:
    """Return sentence indices that are page-boundary fragments.

    PDF pages can cut through a sentence, and sometimes through a word
    (``δημιουρ-`` / ``γούνται``). Those fragments should stay visible in the
    reader's page text but must not become reusable review sentences.
    """
    if not page_words:
        return set()

    by_idx: dict[int, list[PageWord]] = {}
    for word in page_words:
        by_idx.setdefault(word.sentence_index, []).append(word)
    if not by_idx:
        return set()

    out: set[int] = set()
    first_idx = min(by_idx)
    last_idx = max(by_idx)

    this_text = _page_text_for_boundary(page)
    if this_text and not _ends_with_sentence_terminal(this_text):
        out.add(last_idx)

    previous_page = (
        db.query(Page)
        .filter(Page.story_id == page.story_id)
        .filter(Page.page_number == page.page_number - 1)
        .first()
    )
    if previous_page is not None:
        previous_text = _page_text_for_boundary(previous_page)
        if previous_text and not _ends_with_sentence_terminal(previous_text):
            out.add(first_idx)

    return out


def harvest_page_sentences(db: Session, page: Page, *, force: bool = False) -> int:
    """Create Sentence + SentenceWord rows from a page's PageWord rows.

    Returns the number of Sentence rows created/refreshed (0 if already
    harvested-with-matching-text or page not verified). Idempotent: re-running
    against an unchanged page is a true no-op.

    Drift detection (2026-05-26): on a non-force call, the existing Sentence
    rows for the page are compared against the text that would be reconstructed
    from current PageWord rows. If any sentence's text differs (or the set of
    surviving sentence indices differs), the page is treated as if `force=True`
    — rows refresh in place, SentenceWord rows are rewritten, and
    ``translation_en`` is nulled where the text changed so the lazy translation
    fetch re-fills it. This catches the silent-stale-translation pattern that
    happens when a Story is reseeded with new content but its old Sentence rows
    were orphaned past the cascade (no Page→Sentence cascade by design, see the
    module docstring on FK preservation).

    Force semantics: ``force=True`` always rewrites; pass it from backfill paths
    that want a refresh regardless of drift.

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

    page_words = (
        db.query(PageWord)
        .filter(PageWord.page_id == page.id)
        .order_by(PageWord.position)
        .all()
    )
    if not page_words:
        return 0

    heading_indices = _detect_heading_sentence_indices(page_words)
    boundary_indices = _detect_page_boundary_sentence_indices(db, page, page_words)

    by_idx: dict[int, list[PageWord]] = {}
    for w in page_words:
        by_idx.setdefault(w.sentence_index, []).append(w)

    current_text_by_idx: dict[int, str] = {}
    for s_idx, words in by_idx.items():
        if s_idx in heading_indices or s_idx in boundary_indices:
            continue
        if not any(w.lemma_id is not None for w in words):
            continue
        text = _reconstruct_sentence_text(words)
        if text:
            current_text_by_idx[s_idx] = text

    existing_sentences = (
        db.query(Sentence).filter(Sentence.page_id == page.id).all()
    )
    existing_by_idx: dict[int, Sentence] = {
        sentence.sentence_index_in_page: sentence
        for sentence in existing_sentences
        if sentence.sentence_index_in_page is not None
    }

    if not force and existing_sentences:
        existing_text_by_idx = {
            idx: sentence.text
            for idx, sentence in existing_by_idx.items()
            if sentence.is_active
        }
        if existing_text_by_idx == current_text_by_idx:
            return 0
        drift_indices = sorted(
            idx for idx in set(existing_text_by_idx) | set(current_text_by_idx)
            if existing_text_by_idx.get(idx) != current_text_by_idx.get(idx)
        )
        log.warning(
            "Sentence drift on page %d (story %d): %d existing vs %d current, "
            "indices %s — refreshing in place",
            page.id, page.story_id,
            len(existing_text_by_idx), len(current_text_by_idx),
            drift_indices[:6],
        )
        force = True

    if force:
        for sentence in existing_sentences:
            sentence.is_active = False
            sentence.mappings_verified_at = None

    lemma_ids = {w.lemma_id for w in page_words if w.lemma_id is not None}
    canonical_map = _canonical_map_for(db, lemma_ids)

    now = datetime.now(timezone.utc)
    language_code = page.story.language_code
    created = 0
    kept_indices: set[int] = set()

    for s_idx in sorted(by_idx):
        if s_idx in heading_indices or s_idx in boundary_indices:
            continue
        words = by_idx[s_idx]
        # Sentences with no content lemmas are pure-punctuation artefacts from
        # the tokenizer (e.g. a line break treated as its own sentence).
        if not any(w.lemma_id is not None for w in words):
            continue

        text = _reconstruct_sentence_text(words)
        if not text:
            continue

        kept_indices.add(s_idx)
        sentence = existing_by_idx.get(s_idx)
        if sentence is None:
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
        else:
            if sentence.text != text:
                sentence.translation_en = None
                sentence.transliteration = None
                sentence.audio_url = None
            sentence.language_code = language_code
            sentence.text = text
            sentence.source = "textbook"
            sentence.story_id = page.story_id
            sentence.target_lemma_id = None
            sentence.page_id = page.id
            sentence.sentence_index_in_page = s_idx
            sentence.is_active = True
            sentence.mappings_verified_at = page.mappings_verified_at
            db.query(SentenceWord).filter(
                SentenceWord.sentence_id == sentence.id
            ).delete(synchronize_session=False)

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

    if force:
        stale = [
            sentence
            for idx, sentence in existing_by_idx.items()
            if idx not in kept_indices
        ]
        for sentence in stale:
            sentence.is_active = False
            sentence.mappings_verified_at = None

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
