"""Reading-as-mapping intake — page-based, lazy.

Flow:
1. **Import** (POST /api/texts) creates a Story and Page rows with raw text
   only — no tokenization. PDFs are extracted page-by-page; pastes become a
   single Page.
2. **View page** (GET /api/texts/{story_id}/pages/{n}) on first request:
   - **Phase 0**: body-clean via Haiku (strips footers/footnotes/headers,
     joins soft-hyphens; persisted to ``Page.body_clean``).
   - **Phase 1**: simplemma tokenize + lemmatize the cleaned text.
   - **Phase 2**: create Lemma rows for new bare forms, create PageWord
     rows, stamp ``processed_at``. Commit.
   - **Phase 2b**: batch-gloss every content lemma on the page in chunks
     of 50 via Haiku; cached on ``Lemma.gloss_en`` for the DB lifetime so
     subsequent encounters of the same lemma cost nothing.
   - **Phase 3**: per-token quality gate (Sonnet) verifying lemma
     assignments and stamping ``mappings_verified_at``.
   Subsequent views early-return — no phases re-run unless ``force=True``.
3. **Mark** (PATCH .../mark) updates UserLemmaKnowledge. Phase 2b means
   ``mark_lemma(state='unknown')``'s ``ensure_gloss`` is usually a cache
   hit; the single-form fallback only fires for batch-failed chunks.
4. **Expand** (deferred) fetches etymology, examples, conjugations for a
   lemma on explicit request.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Lemma, Story, Page, PageWord, PageReviewLog, UserLemmaKnowledge
from app.services import body_clean as body_clean_svc
from app.services import lemma_gloss
from app.services import pdf_extract
from app.services.cognate_detector import link_intra_greek_cognates, propagate_known_via_cognate
from app.services import lemma_quality
from app.services.languages import (
    NLPProvider, ProviderUnavailable, Token, get_provider,
)

log = logging.getLogger(__name__)

import os as _os
# Batch-gloss every new lemma on the page right after tokenization, so the
# reader sees English meanings the instant a tap surfaces a lookup. Default
# on — Haiku is cheap (~$0.001 per 50 lemmas, free under Max). Disable for
# tests / cost-sensitive environments.
BATCH_GLOSS_ENABLED = _os.environ.get("POLYGLOT_BATCH_GLOSS", "1") == "1"

# Citation-form repair: the configured LLM judges every newly-created lemma's form before it
# can enter the study pool, so simplemma can never leave an inflected surface
# form (εξελίχθηκαν, πλεονάσματος) in the vocabulary. Opt-in like the quality
# gate — defaults off so tests/dev don't hit the LLM; production enables it via
# POLYGLOT_LEMMA_REPAIR=1 (systemd EnvironmentFile + cron wrapper).
LEMMA_REPAIR_ENABLED = _os.environ.get("POLYGLOT_LEMMA_REPAIR", "0") == "1"


def _split_into_sentences(text: str) -> list[str]:
    """Cheap splitter: ·;.!?\\n plus Greek question mark ;. Keeps delimiters
    out — we only need sentence_index for grouping in the UI."""
    parts = re.split(r"(?<=[.!?·;\n])\s+", text.strip())
    return [p for p in parts if p.strip()]


def _lookup_lemma(db: Session, language_code: str, lemma_bare: str) -> Lemma | None:
    return (
        db.query(Lemma)
        .filter(Lemma.language_code == language_code, Lemma.lemma_bare == lemma_bare)
        .first()
    )


def _repair_lemma_before_study(db: Session, lemma_id: int) -> int | None:
    """Return the live lemma_id to study after the citation gate.

    If the LLM says the row is junk, the lemma is retired and ``None`` is
    returned so the caller does not create a ULK for a non-word. LLM failure is
    also treated as "do not study yet" rather than silently trusting an
    unaudited simplemma row.
    """
    if not LEMMA_REPAIR_ENABLED:
        return lemma_id
    lemma = db.get(Lemma, lemma_id)
    if lemma is None:
        return None
    if lemma.gates_completed_at is not None:
        return lemma_id
    try:
        from app.services.lemma_integrity import repair_lemma
        res = repair_lemma(db, lemma.language_code, lemma_id)
    except Exception as e:
        log.warning("Lemma citation repair failed before study lemma_id=%d: %s", lemma_id, e)
        return None
    if res.action == "merge" and res.target_id is not None:
        return res.target_id
    if res.action == "retire":
        db.commit()
        return None
    if res.action == "skip":
        log.warning("Lemma citation repair skipped before study lemma_id=%d: %s",
                    lemma_id, res.detail)
        return None
    return lemma_id


# ─── Import ────────────────────────────────────────────────────────────────

def import_paste(
    db: Session,
    *,
    language_code: str,
    body: str,
    title: str | None = None,
    author: str | None = None,
) -> Story:
    """Import a pasted text as a single-page Story. Pages aren't tokenized
    yet — that happens on first view."""
    story = Story(
        language_code=language_code,
        title=title,
        author=author,
        body_src=body,
        source="paste",
        page_count=1,
    )
    db.add(story)
    db.flush()
    db.add(Page(story_id=story.id, page_number=1, body_src=body))
    db.commit()
    db.refresh(story)
    return story


def import_pdf(
    db: Session,
    *,
    language_code: str,
    pdf_path: str | Path,
    title: str | None = None,
    author: str | None = None,
) -> Story:
    """Extract a PDF page-by-page into Story + Page rows. No tokenization.
    Pages are tokenized lazily on first view."""
    pages = pdf_extract.extract_pages(pdf_path)
    if not pages:
        raise ValueError(f"No pages extracted from {pdf_path}")

    story = Story(
        language_code=language_code,
        title=title or Path(pdf_path).stem,
        author=author,
        source="pdf",
        source_path=str(pdf_path),
        page_count=len(pages),
    )
    db.add(story)
    db.flush()
    for ep in pages:
        db.add(Page(story_id=story.id, page_number=ep.page_number, body_src=ep.text))
    db.commit()
    db.refresh(story)
    log.info("Imported PDF %s as Story id=%d (%d pages, all unprocessed)",
             pdf_path, story.id, len(pages))
    return story


# ─── Lazy page processing ──────────────────────────────────────────────────

def process_page(db: Session, page: Page, *, force: bool = False) -> Page:
    """Tokenize + lemmatize a page, create Lemma rows for new lemmas, create
    PageWord rows. Idempotent unless force=True.

    Two-phase: NLP work first (no DB locks), then write everything in one
    transaction. Mirrors Alif's lock-discipline pattern.
    """
    if page.processed_at and not force:
        if page.mappings_verified_at is None:
            _run_quality_gate_and_harvest(db, page)
        return page

    provider: NLPProvider = get_provider(page.story.language_code)
    language_code = page.story.language_code

    # Phase 0: structural cleanup (LLM). Strips page numbers, headers, footers,
    # bibliographies, footnote definitions; joins soft-hyphen line breaks;
    # detaches footnote-marker digits fused into words. Persisted on the Page
    # so re-tokenization doesn't pay the LLM cost twice. Falls back to
    # body_src on LLM failure — tokenizer still works, just on noisier input.
    body_clean_missing = (
        page.body_clean is None
        or (not page.body_clean.strip() and bool((page.body_src or "").strip()))
    )
    if body_clean_svc.BODY_CLEAN_ENABLED and (body_clean_missing or force):
        result = body_clean_svc.clean_body(page.body_src, language_code)
        if result is not None:
            page.body_clean = result.cleaned
            db.commit()
            log.info(
                "Cleaned page %d: %d→%d chars, %d removed segments, %d hyphen-joins",
                page.id, len(page.body_src), len(result.cleaned),
                len(result.removed), len(result.hyphen_joins),
            )

    raw_source_text = page.body_clean if page.body_clean and page.body_clean.strip() else page.body_src
    source_text = body_clean_svc.normalize_pdf_artifacts(
        raw_source_text,
        collapse_whitespace=True,
    )

    # Phase 1: pure compute
    sentences = _split_into_sentences(source_text)
    tokens_with_meta: list[tuple[int, Token, int]] = []  # (sentence_idx, token, global_pos)
    global_pos = 0
    for s_idx, sentence in enumerate(sentences):
        for tok in provider.tokenize(sentence):
            tokens_with_meta.append((s_idx, tok, global_pos))
            global_pos += 1

    # Lemmatize each unique surface; degrade to surface if toolkit unavailable
    surface_to_lemma: dict[str, tuple[str, str, str | None]] = {}
    for _, tok, _ in tokens_with_meta:
        if tok.is_punctuation or tok.surface in surface_to_lemma:
            continue
        try:
            cand = provider.lemmatize(tok.surface)
            surface_to_lemma[tok.surface] = (cand.lemma, cand.lemma_bare, cand.pos)
        except ProviderUnavailable:
            surface_to_lemma[tok.surface] = (
                tok.surface, provider.normalize_bare(tok.surface), None,
            )

    # Phase 2: DB writes (single transaction)
    # If force=True, clear old PageWords first
    if force:
        db.query(PageWord).filter(PageWord.page_id == page.id).delete()

    bare_to_lemma_id: dict[str, int] = {}
    new_lemma_ids: list[int] = []
    for surface, (lemma_form, lemma_bare, pos) in surface_to_lemma.items():
        if lemma_bare in bare_to_lemma_id:
            continue
        existing = _lookup_lemma(db, language_code, lemma_bare)
        if existing:
            from app.services.lemma_quality import FUNCTION_WORD_SETS
            if (
                existing.word_category is None
                and lemma_bare in FUNCTION_WORD_SETS.get(language_code, set())
            ):
                existing.word_category = "function_word"
            bare_to_lemma_id[lemma_bare] = existing.lemma_id
            continue
        new_lemma = Lemma(
            language_code=language_code,
            lemma_form=lemma_form,
            lemma_bare=lemma_bare,
            pos=pos,
            source="reading_intake",
        )
        db.add(new_lemma)
        db.flush()
        # Modern ↔ Ancient Greek auto-linking via bare-form match.
        # Cheap (pure DB), so always on. External (L1) cognate detection runs
        # separately and gated; see cognate_detector.detect_external_cognates.
        link_intra_greek_cognates(db, new_lemma)
        # Pre-classify function words so bulk-mark + UI can treat them
        # specially (grey-out, no scheduling enrollment).
        from app.services.lemma_quality import FUNCTION_WORD_SETS
        if lemma_bare in FUNCTION_WORD_SETS.get(language_code, set()):
            new_lemma.word_category = "function_word"
        bare_to_lemma_id[lemma_bare] = new_lemma.lemma_id
        new_lemma_ids.append(new_lemma.lemma_id)

    word_rows = 0
    for s_idx, tok, g_pos in tokens_with_meta:
        lemma_id = None
        if not tok.is_punctuation:
            _, lemma_bare, _ = surface_to_lemma[tok.surface]
            lemma_id = bare_to_lemma_id.get(lemma_bare)
            word_rows += 1
        db.add(PageWord(
            page_id=page.id,
            position=g_pos,
            surface_form=tok.surface,
            lemma_id=lemma_id,
            sentence_index=s_idx,
        ))

    page.processed_at = datetime.now(timezone.utc)
    page.total_words = word_rows
    db.commit()
    db.refresh(page)
    log.info("Processed page %d of story %d: %d tokens, %d unique lemmas",
             page.page_number, page.story_id, word_rows, len(bare_to_lemma_id))

    # Phase 2b: batch-fetch English glosses for every lemma on the page that
    # doesn't have one yet. This is what makes the tap-to-lookup feel instant
    # — when the user taps a word, the gloss is already cached. Skips
    # function words + proper names internally. Lock-safe: ensure_glosses_batch
    # commits between chunks so the SQLite write lock is released between
    # Haiku calls (CLAUDE.md rule #10).
    if BATCH_GLOSS_ENABLED:
        try:
            page_lemma_ids = list(bare_to_lemma_id.values())
            n_glossed = lemma_gloss.ensure_glosses_batch(db, page_lemma_ids)
            if n_glossed > 0:
                log.info("Glossed %d new lemmas on page %d", n_glossed, page.id)
        except Exception as e:
            log.warning("Batch gloss failed for page %d: %s", page.id, e)

    # Phase 2c: citation-form repair. The configured LLM judges every newly-created lemma's
    # form (with the just-fetched gloss as a disambiguating hint) and rewrites
    # any inflected surface form to its dictionary citation form before the
    # lemma can be studied. Runs after the gloss pass (gloss hint) and before
    # the quality gate (so the gate verifies corrected mappings). Lock-safe:
    # repair_lemmas holds no write lock across its LLM calls.
    if LEMMA_REPAIR_ENABLED and new_lemma_ids:
        try:
            from app.services.lemma_integrity import repair_lemmas
            actions = repair_lemmas(db, language_code, new_lemma_ids)
            if actions:
                log.info("Citation-repaired page %d new lemmas: %s", page.id, actions)
        except Exception as e:
            log.warning("Lemma citation repair failed for page %d: %s", page.id, e)

    # Quality gate: LLM-in-context verification of lemma assignments. Gated
    # by POLYGLOT_QUALITY_GATE=1 — for the MVP we want users to opt in once
    # they trust their prompt tuning. Runs synchronously to keep the model
    # of "page is verified by the time you see it" simple.
    if not force:
        _run_quality_gate_and_harvest(db, page)

    return page


def _run_quality_gate_and_harvest(db: Session, page: Page) -> None:
    """Retry the idempotent quality gate, then harvest once mappings are verified."""
    if lemma_quality.QUALITY_GATE_ENABLED and page.mappings_verified_at is None:
        try:
            lemma_quality.verify_page_mappings(db, page)
            db.refresh(page)
        except Exception as e:
            log.warning("Quality gate failed for page %d: %s", page.id, e)

    if page.mappings_verified_at is not None:
        try:
            from app.services.sentence_harvest import harvest_page_sentences
            harvest_page_sentences(db, page)
        except Exception as e:
            log.warning("Sentence harvest failed for page %d: %s", page.id, e)


# ─── Views ─────────────────────────────────────────────────────────────────

def get_page_view(db: Session, story_id: int, page_number: int) -> tuple[Page, list[dict]] | None:
    """Return Page + token view. Processes the page on first request."""
    page = (
        db.query(Page)
        .filter(Page.story_id == story_id, Page.page_number == page_number)
        .first()
    )
    if not page:
        return None
    if (
        page.processed_at is None
        or (lemma_quality.QUALITY_GATE_ENABLED and page.mappings_verified_at is None)
    ):
        process_page(db, page)
    return _build_token_view(db, page)


# Page translation. The reader's "Show English" reveal needs a coherent
# English rendering of the whole page (the page-scale analogue of a
# sentence-review card's translation). Generated lazily on first request and
# cached on Page.translation_en for the DB lifetime — sonnet/gpt-5.5 quality so
# the passage reads as prose, not a word-by-word gloss. Env-overridable.
PAGE_TRANSLATE_MODEL = _os.environ.get("POLYGLOT_PAGE_TRANSLATE_MODEL", "sonnet")
PAGE_TRANSLATE_TIMEOUT_S = int(_os.environ.get("POLYGLOT_PAGE_TRANSLATE_TIMEOUT_S", "90"))

_LANG_DISPLAY = {"el": "Modern Greek", "grc": "Ancient Greek", "la": "Latin"}


def _translate_page_text(language_code: str, text: str) -> str | None:
    """One free-text LLM call → an English translation of the page, or None on
    total LLM failure (caller leaves the page untranslated to retry later).

    Isolated so tests can monkeypatch it without spawning a CLI subprocess."""
    from app.services.llm_cli import call_text

    lang = _LANG_DISPLAY.get(language_code, language_code)
    prompt = (
        f"You are translating a page of {lang} text into English for someone "
        f"who is learning {lang} by reading it.\n\n"
        "Produce a faithful, natural, readable English translation of the "
        "passage below.\n"
        "- Translate the whole passage; skip nothing.\n"
        "- Preserve paragraph breaks.\n"
        "- Do not transliterate, do not add notes, headings, or commentary — "
        "output only the English translation.\n\n"
        f"Passage:\n{text}"
    )
    return call_text(
        prompt=prompt,
        model=PAGE_TRANSLATE_MODEL,
        timeout_s=PAGE_TRANSLATE_TIMEOUT_S,
        log_context="page_translation",
    )


def ensure_page_translation(
    db: Session, story_id: int, page_number: int,
) -> tuple[str | None, bool] | None:
    """Return ``(translation_en, generated)`` for a page, generating + caching
    it on first request. ``generated`` is True only when this call produced it.

    Returns ``None`` when the page doesn't exist (caller → 404).

    Lock discipline: the page text is read first, the (slow) LLM call runs with
    NO dirty session state, and only then is the result written in a short
    transaction — the SQLite write lock is never held across the LLM call
    (CLAUDE.md rule #10). Idempotent: a cached translation short-circuits.
    """
    page = (
        db.query(Page)
        .filter(Page.story_id == story_id, Page.page_number == page_number)
        .first()
    )
    if page is None:
        return None
    if page.translation_en is not None and page.translation_en.strip():
        return page.translation_en, False

    page_id = page.id
    raw_source = page.body_clean if (page.body_clean and page.body_clean.strip()) else page.body_src
    source_text = body_clean_svc.normalize_pdf_artifacts(raw_source or "", collapse_whitespace=True)
    if not any(c.isalpha() for c in source_text):
        # Nothing to translate (blank / pure-punctuation page). Cache an empty
        # string so we don't re-attempt on every reveal.
        page.translation_en = ""
        page.translated_at = datetime.now(timezone.utc)
        db.commit()
        return "", False

    # Slow work — no DB writes pending while this runs.
    english = _translate_page_text(page.story.language_code, source_text)
    if not english or not english.strip():
        return None, False  # leave NULL → retried on next reveal

    fresh = db.get(Page, page_id)
    if fresh is None:
        return None
    fresh.translation_en = english.strip()
    fresh.translated_at = datetime.now(timezone.utc)
    db.commit()
    return fresh.translation_en, True


def _build_token_view(db: Session, page: Page) -> tuple[Page, list[dict]]:
    words = (
        db.query(PageWord)
        .filter(PageWord.page_id == page.id)
        .order_by(PageWord.position)
        .all()
    )
    lemma_ids = {w.lemma_id for w in words if w.lemma_id is not None}
    lemmas_by_id = {
        l.lemma_id: l
        for l in db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
    } if lemma_ids else {}
    knowledge_by_lemma = {
        k.lemma_id: k
        for k in db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id.in_(lemma_ids)
        ).all()
    } if lemma_ids else {}
    language_code = page.story.language_code
    function_word_bares = lemma_quality.FUNCTION_WORD_SETS.get(language_code, set())

    tokens = []
    for w in words:
        lemma = lemmas_by_id.get(w.lemma_id) if w.lemma_id else None
        ulk = knowledge_by_lemma.get(w.lemma_id) if w.lemma_id else None
        state = ulk.knowledge_state if ulk else None
        is_punct = lemma is None and not any(c.isalpha() for c in w.surface_form)
        is_function_word = bool(
            lemma is not None
            and (
                lemma.word_category == "function_word"
                or lemma.lemma_bare in function_word_bares
            )
        )
        tokens.append({
            "position": w.position,
            "surface": w.surface_form,
            "is_punctuation": is_punct,
            "sentence_index": w.sentence_index,
            "lemma_id": w.lemma_id,
            "lemma_form": lemma.lemma_form if lemma else None,
            "lemma_bare": lemma.lemma_bare if lemma else None,
            "pos": lemma.pos if lemma else None,
            "gloss_en": lemma.gloss_en if lemma else None,
            "is_function_word": is_function_word,
            "is_heading": w.quality_note == "heading",
            "is_known": state == "known",
            "is_acquiring": state in ("acquiring", "learning"),
            "is_encountered": state == "encountered",
            "is_unknown": state == "unknown",
            "is_ignored": state == "ignore",
            "is_new": ulk is None and lemma is not None and not is_function_word,
            "is_oov": lemma is None and not is_punct,
        })
    return page, tokens


def bulk_mark_remaining_known(db: Session, story_id: int, page_number: int) -> int:
    """When the user advances to the next page, every lemma they didn't tap
    is presumed known. This is the heart of the reading-as-mapping flow for
    an intermediate learner — patchy knowledge means most words on a page
    are already known; only the gaps need explicit marking.

    Skips:
      - punctuation (no lemma)
      - function words (Lemma.word_category='function_word' OR in FUNCTION_WORDS list)
      - lemmas that already have a ULK in *any* state (don't overwrite user
        decisions — they may have actively marked something 'unknown' but
        forgotten to revisit it)

    Returns the count of lemmas newly marked known.

    Cognate propagation runs per-lemma via mark_lemma's existing hook —
    bulk-marking a Modern Greek page also seeds Ancient cognates as
    'encountered' bidirectionally.
    """
    from app.services.lemma_quality import FUNCTION_WORD_SETS

    page = (
        db.query(Page)
        .filter(Page.story_id == story_id, Page.page_number == page_number)
        .first()
    )
    if not page:
        return 0
    # Process the page first if it hasn't been (so bulk-mark sees actual lemmas).
    if page.processed_at is None:
        process_page(db, page)

    language_code = page.story.language_code
    function_word_bares = FUNCTION_WORD_SETS.get(language_code, set())

    def _eligible_ids() -> list[int]:
        # Collect distinct lemma_ids on the page
        lemma_ids = {
            w.lemma_id
            for w in db.query(PageWord).filter(PageWord.page_id == page.id).all()
            if w.lemma_id is not None
        }
        if not lemma_ids:
            return []

        # Filter out lemmas that already have any ULK
        already_known_ids = {
            ulk.lemma_id
            for ulk in db.query(UserLemmaKnowledge).filter(
                UserLemmaKnowledge.lemma_id.in_(lemma_ids)
            ).all()
        }
        pending_ids = lemma_ids - already_known_ids

        # Filter out function words / proper names (don't enrol them in scheduling)
        lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(pending_ids)).all()
        return [
            l.lemma_id for l in lemmas
            if l.word_category not in ("function_word", "proper_name", "not_word")
            and l.lemma_bare not in function_word_bares
        ]

    eligible_ids = _eligible_ids()
    if not eligible_ids:
        return 0

    # If a page was processed before the citation gate existed (or a previous
    # gate attempt failed), batch-repair before bulk-enrolling "known" rows.
    if LEMMA_REPAIR_ENABLED:
        repair_ids = [
            lid for (lid,) in db.query(Lemma.lemma_id)
            .filter(Lemma.lemma_id.in_(eligible_ids), Lemma.gates_completed_at.is_(None))
            .all()
        ]
        if repair_ids:
            try:
                from app.services.lemma_integrity import repair_lemmas
                actions = repair_lemmas(db, language_code, repair_ids)
                log.info("Citation-repaired bulk-known candidates on page %d: %s",
                         page.id, actions)
            except Exception as e:
                log.warning("Bulk-known citation repair failed for page %d: %s", page.id, e)
                return 0
            eligible_ids = _eligible_ids()
            if not eligible_ids:
                return 0

    # Bulk-mark via mark_lemma so cognate propagation runs uniformly
    count = 0
    for lid in eligible_ids:
        if mark_lemma(db, lemma_id=lid, state="known", fetch_gloss=False) is not None:
            count += 1
    log.info("Bulk-marked %d lemmas as known on page %d of story %d",
             count, page_number, story_id)
    return count


def apply_page_review(
    db: Session,
    story_id: int,
    page_number: int,
    tapped_lemma_ids: list[int] | None = None,
    *,
    unknown_lemma_ids: list[int] | None = None,
    encountered_lemma_ids: list[int] | None = None,
    client_review_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, int | bool]:
    """Advancing a page is a green comprehension review over every content word
    on it the user did NOT tap this session — the page-scale analogue of a
    sentence submit (Hard Invariant FOUNDATIONAL: every word in a reviewed unit
    is evaluated; reader and review count identically — Hard Invariant 6).

    **Self-contained + idempotent (Hard Invariant 11).** The submission fully
    describes the page outcome so it can be queued offline and replayed safely:

      - ``unknown_lemma_ids`` (red taps) and ``encountered_lemma_ids`` (yellow
        taps) are *applied here*, not relied upon to have arrived via per-tap
        ``mark_lemma`` calls. Online those calls already ran, so the red word is
        already ``acquiring`` and we skip re-recording the failure; offline they
        never reached the server, so we apply them now. (`start_acquisition` is
        idempotent and always lands a word in ``acquiring`` — that post-tap
        state is exactly the signal that tells "already applied live" apart from
        "needs applying".)
      - ``client_review_id`` makes the *whole page* idempotent: a re-flush of
        the same id observes the stored counts via ``PageReviewLog`` and applies
        nothing. Mirrors ``ReviewLog.client_review_id``.

    The untapped green sweep dispatches each word's current state to the SAME
    primitives a sentence submit uses (no duplicated FSRS / acquisition math):
        no ULK / encountered          -> presume known (reading-as-mapping) + confirm
        known + no card               -> record_scaffold_confirmation
        acquiring                     -> submit_acquisition_review(3)
        learning/known/lapsed + card  -> submit_review(3)
        suspended / ignore            -> skip (don't resurrect a user decision)

    Efficiency: ONE network request (the caller endpoint), batched ULK load,
    and a SINGLE commit for the whole page (~150 words) — the write lock is
    acquired once, briefly. The only slow step (LLM citation repair of ungated
    lemmas) runs up front, before the write transaction.

    ``tapped_lemma_ids`` is the legacy pre-offline field (the union of red +
    yellow). It is honoured for exclusion only — kept so an already-queued old
    client still works — but new clients send the split red/yellow lists above.
    """
    from app.services.canonical_resolution import resolve_canonical_lemma_id
    from app.services.fsrs_service import record_scaffold_confirmation, submit_review
    from app.services.acquisition_service import start_acquisition, submit_acquisition_review
    from app.services.cognate_detector import propagate_known_via_cognate
    from app.services.knowledge_lifecycle import (
        ORIGIN_MARKED_RECOGNIZED, ORIGIN_MARKED_UNKNOWN, ORIGIN_PRE_KNOWN,
        record_failure, set_origin_if_missing,
    )
    from app.services.lemma_quality import FUNCTION_WORD_SETS

    def _zero(duplicate: bool = False) -> dict[str, int | bool]:
        return {"newly_known": 0, "confirmed": 0, "reviewed": 0,
                "marked_unknown": 0, "marked_encountered": 0, "duplicate": duplicate}

    # Whole-page idempotency: a replayed offline submission must not double-apply.
    if client_review_id:
        prior = (
            db.query(PageReviewLog)
            .filter(PageReviewLog.client_review_id == client_review_id)
            .first()
        )
        if prior is not None:
            return {
                "newly_known": prior.newly_known, "confirmed": prior.confirmed,
                "reviewed": prior.reviewed, "marked_unknown": prior.marked_unknown,
                "marked_encountered": prior.marked_encountered, "duplicate": True,
            }

    unknown_ids = list(unknown_lemma_ids or [])
    encountered_ids = list(encountered_lemma_ids or [])
    legacy_tapped = list(tapped_lemma_ids or [])
    now = datetime.now(timezone.utc)

    page = (
        db.query(Page)
        .filter(Page.story_id == story_id, Page.page_number == page_number)
        .first()
    )
    if not page:
        return _zero()
    if page.processed_at is None:
        process_page(db, page)

    language_code = page.story.language_code
    function_word_bares = FUNCTION_WORD_SETS.get(language_code, set())

    page_lemma_ids = {
        w.lemma_id
        for w in db.query(PageWord).filter(PageWord.page_id == page.id).all()
        if w.lemma_id is not None
    }
    if not page_lemma_ids:
        return _zero()

    def _content() -> list[Lemma]:
        rows = db.query(Lemma).filter(Lemma.lemma_id.in_(page_lemma_ids)).all()
        return [
            l for l in rows
            if l.word_category not in ("function_word", "proper_name", "not_word")
            and l.lemma_bare not in function_word_bares
        ]

    content = _content()

    # Citation-repair ungated lemmas FIRST. repair_lemmas is LLM-backed and
    # commits its own work — doing it before the write transaction keeps the
    # SQLite write lock out of the slow path (lock discipline). After the
    # 150-word green sweep below there are NO slow calls, so a single commit
    # holds the lock only briefly.
    if LEMMA_REPAIR_ENABLED:
        repair_ids = [
            lid for (lid,) in db.query(Lemma.lemma_id)
            .filter(Lemma.lemma_id.in_([l.lemma_id for l in content]),
                    Lemma.gates_completed_at.is_(None))
            .all()
        ]
        if repair_ids:
            try:
                from app.services.lemma_integrity import repair_lemmas
                repair_lemmas(db, language_code, repair_ids)
            except Exception as e:
                log.warning("Page-review citation repair failed (page %d): %s", page.id, e)
            content = _content()

    # Resolve canonicals for content + restrict tap lists to content lemmas
    # (a function/proper-name tap is a no-op, mirroring mark_lemma's guard).
    canonical_of = {l.lemma_id: resolve_canonical_lemma_id(db, l.lemma_id) for l in content}
    content_canon: set[int] = set(canonical_of.values())

    def _content_canon(raw_id: int) -> int | None:
        c = resolve_canonical_lemma_id(db, raw_id)
        return c if c in content_canon else None

    red_canon = {c for c in (_content_canon(i) for i in unknown_ids) if c is not None}
    yellow_canon = {c for c in (_content_canon(i) for i in encountered_ids) if c is not None}
    yellow_canon -= red_canon  # a lemma can't be both; red wins

    exclude_canon = red_canon | yellow_canon | {
        resolve_canonical_lemma_id(db, i) for i in legacy_tapped
    }
    exclude_raw = set(unknown_ids) | set(encountered_ids) | set(legacy_tapped)

    # Batch-load every relevant ULK in ONE query (content + taps). The state
    # captured here is post-live-tap when online, pre-tap when offline.
    all_canon = content_canon | red_canon | yellow_canon
    ulks: dict[int, UserLemmaKnowledge] = {
        u.lemma_id: u for u in
        db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id.in_(all_canon)).all()
    } if all_canon else {}

    newly_known = confirmed = reviewed = 0
    marked_unknown = marked_encountered = 0
    propagate_ids: list[int] = []

    # Apply red taps (commit-free): enrol into acquisition + record the failure,
    # but only if the live per-tap call didn't already do it. A word that the
    # live tap enrolled is already ``acquiring`` here; re-recording would inflate
    # failure_count. Offline (no live tap) the pre-state is None/known/learning/
    # encountered/lapsed → genuine first failure.
    for canonical in red_canon:
        prev = ulks.get(canonical)
        if prev is not None and prev.knowledge_state in ("acquiring", "suspended"):
            continue
        ulk = start_acquisition(
            db, lemma_id=canonical, source="reading_intake",
            due_immediately=True, restart_known=True,
        )
        record_failure(ulk, now, origin=ORIGIN_MARKED_UNKNOWN)
        ulks[canonical] = ulk
        marked_unknown += 1

    # Apply yellow taps (commit-free): light "recognize" state, no SRS card.
    # Idempotent online (already encountered → skip); don't override a user
    # suspend/ignore decision.
    for canonical in yellow_canon:
        prev = ulks.get(canonical)
        if prev is not None and prev.knowledge_state in ("encountered", "suspended", "ignore"):
            continue
        if prev is None:
            ulk = UserLemmaKnowledge(
                lemma_id=canonical, knowledge_state="encountered", introduced_at=now,
                source="reading_intake", knowledge_origin=ORIGIN_MARKED_RECOGNIZED,
            )
            db.add(ulk)
            ulks[canonical] = ulk
        else:
            prev.knowledge_state = "encountered"
            set_origin_if_missing(prev, ORIGIN_MARKED_RECOGNIZED)
        marked_encountered += 1

    # Green sweep over untapped content.
    seen: set[int] = set(red_canon) | set(yellow_canon)
    for l in content:
        canonical = canonical_of[l.lemma_id]
        if l.lemma_id in exclude_raw or canonical in exclude_canon:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        ulk = ulks.get(canonical)

        if ulk is None:
            # Never seen → presume known (reading-as-mapping) + confirm.
            ulk = UserLemmaKnowledge(
                lemma_id=canonical, knowledge_state="known", introduced_at=now,
                source="reading_intake", knowledge_origin=ORIGIN_PRE_KNOWN,
                confirmed_at=now, clean_exposures=1,
            )
            db.add(ulk)
            ulks[canonical] = ulk
            propagate_ids.append(canonical)
            newly_known += 1
            continue

        state = ulk.knowledge_state
        if state in ("suspended", "ignore"):
            continue
        if state == "known" and ulk.fsrs_card_json is None:
            record_scaffold_confirmation(db, lemma_id=canonical, rating_int=3,
                                         review_mode="reading", credit_type="collateral")
            confirmed += 1
        elif state == "acquiring":
            submit_acquisition_review(db, lemma_id=canonical, rating_int=3,
                                      review_mode="reading", commit=False)
            reviewed += 1
        elif state in ("learning", "known", "lapsed") and ulk.fsrs_card_json is not None:
            submit_review(db, lemma_id=canonical, rating_int=3,
                          review_mode="reading", commit=False)
            reviewed += 1
        elif state == "encountered":
            ulk.knowledge_state = "known"
            if ulk.confirmed_at is None:
                ulk.confirmed_at = now
            ulk.clean_exposures = (ulk.clean_exposures or 0) + 1
            propagate_ids.append(canonical)
            confirmed += 1

    # Seed Modern↔Ancient cognates for everything newly known — commit-free.
    for cid in propagate_ids:
        propagate_known_via_cognate(db, cid, commit=False)

    if client_review_id:
        db.add(PageReviewLog(
            story_id=story_id, page_number=page_number,
            client_review_id=client_review_id, session_id=session_id,
            newly_known=newly_known, confirmed=confirmed, reviewed=reviewed,
            marked_unknown=marked_unknown, marked_encountered=marked_encountered,
        ))

    db.commit()  # single write-lock acquisition for the whole page
    log.info(
        "Page review story %d page %d: newly_known=%d confirmed=%d reviewed=%d "
        "marked_unknown=%d marked_encountered=%d (1 commit)",
        story_id, page_number, newly_known, confirmed, reviewed,
        marked_unknown, marked_encountered,
    )
    return {
        "newly_known": newly_known, "confirmed": confirmed, "reviewed": reviewed,
        "marked_unknown": marked_unknown, "marked_encountered": marked_encountered,
        "duplicate": False,
    }


def mark_lemma(db: Session, lemma_id: int, state: str, *, fetch_gloss: bool = True) -> UserLemmaKnowledge | None:
    """Set the user's knowledge state for a lemma. Creates ULK if missing.

    Behaviour by ``state``:
      - ``known``: ULK state set to ``known``; cognate propagation runs.
      - ``unknown``: enters the SRS engine immediately. The lemma is routed
        through ``start_acquisition`` with ``source='reading_intake'`` and
        ``due_immediately=True``, so it lands in Box 1 with the next review
        due now. Reading-screen unknown taps bypass the daily intro cap so
        explicit "I don't know this" data is never discarded.
        A tiny English gloss is also fetched if missing.
      - ``encountered``: lightweight state-only update; no SRS enrolment.
      - ``ignore``: mark as a proper name / out-of-band token and remove from SRS.
      - ``clear``: drop the ULK entirely so the lemma returns to its pre-tap
        "no state" — used to undo an accidental tap from the reading screen's
        tap-cycle (red → yellow → clear). Audit history in ReviewLog is left
        intact. Returns None when the ULK was deleted (or didn't exist).
    """
    valid = {"known", "unknown", "encountered", "ignore", "clear"}
    if state not in valid:
        raise ValueError(f"Invalid state {state!r}; expected one of {valid}")

    if state == "clear":
        from app.services.canonical_resolution import resolve_canonical_lemma_id
        canonical_id = resolve_canonical_lemma_id(db, lemma_id)
        existing = db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == canonical_id
        ).first()
        if existing is not None:
            db.delete(existing)
            db.commit()
        return None

    from app.services.canonical_resolution import resolve_canonical_lemma_id

    repaired_id = _repair_lemma_before_study(db, lemma_id)
    if repaired_id is None:
        return None
    lemma_id = repaired_id

    lemma_id = resolve_canonical_lemma_id(db, lemma_id)
    lemma = db.get(Lemma, lemma_id)
    if lemma is None:
        return None
    if state != "ignore" and lemma.word_category in ("function_word", "proper_name", "not_word"):
        db.commit()
        return None

    # 'unknown' has its own pipeline — enrol into acquisition, fetch gloss.
    if state == "unknown":
        from app.services.acquisition_service import start_acquisition
        from app.services.knowledge_lifecycle import (
            ORIGIN_MARKED_UNKNOWN,
            record_failure,
        )
        ulk = start_acquisition(
            db,
            lemma_id=lemma_id,
            source="reading_intake",
            due_immediately=True,
            restart_known=True,
        )
        record_failure(ulk, datetime.now(timezone.utc), origin=ORIGIN_MARKED_UNKNOWN)
        db.commit()
        db.refresh(ulk)
        if fetch_gloss:
            try:
                from app.services.lemma_gloss import ensure_gloss
                ensure_gloss(db, lemma_id)
            except Exception as e:
                log.warning("Gloss fetch failed for lemma_id=%d: %s", lemma_id, e)
        return ulk

    ulk = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == lemma_id
    ).first()
    now = datetime.now(timezone.utc)
    from app.services.knowledge_lifecycle import (
        ORIGIN_MARKED_RECOGNIZED,
        ORIGIN_PRE_KNOWN,
        set_origin_if_missing,
    )
    origin = None
    if state == "known":
        origin = ORIGIN_PRE_KNOWN
    elif state == "encountered":
        origin = ORIGIN_MARKED_RECOGNIZED

    if ulk is None:
        ulk = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state=state,
            introduced_at=now,
            source="reading_intake",
            knowledge_origin=origin,
        )
        db.add(ulk)
    else:
        ulk.knowledge_state = state
        set_origin_if_missing(ulk, origin)
    if state == "known":
        # Marking known in the reader is confirmation by exposure — equal to a
        # sentence-review confirmation (CLAUDE.md Hard Invariant 6). Stamp
        # confirmed_at so the gradient + conversion time-series are
        # surface-agnostic (reader and review count identically).
        if ulk.confirmed_at is None:
            ulk.confirmed_at = now
        ulk.clean_exposures = (ulk.clean_exposures or 0) + 1
    if state == "ignore":
        lemma = db.get(Lemma, lemma_id)
        if lemma is not None:
            lemma.word_category = "proper_name"
    db.commit()
    db.refresh(ulk)

    if state == "known":
        propagate_known_via_cognate(db, lemma_id)

    return ulk


# ─── Front-matter detection ───────────────────────────────────────────────


# Greek front-matter section titles. School textbooks reliably use these as
# section headers on the publishing/TOC/preface pages, so a page whose first
# few hundred characters contain any of them is almost certainly not
# reading content.
_GREEK_FRONT_MATTER_MARKERS = (
    "ΠΕΡΙΕΧΟΜΕΝΑ",          # Contents
    "ΠΡΟΛΟΓΟΣ",             # Preface
    "ΣΤΟΙΧΕΙΑ ΕΚ",          # Catches "ΣΤΟΙΧΕΙΑ ΕΚΔΟΣΗΣ" and "ΕΠΑΝΕΚΔΟΣΗΣ"
    "ΣΤΟΙΧΕΙΑ ΑΡΧΙΚΗΣ",
    "ΥΠΟΥΡΓΕΙΟ",            # Ministry — copyright stamp on Greek school books
    "ISBN",
)
# Real chapter content uses section numbering like "1.1" or "2.3". Both
# the body and the TOC contain these, but the TOC is also dot-heavy and is
# rejected separately.
import re as _re
_SECTION_NUMBER_PATTERN = _re.compile(r"\b\d+\.\d+\b")


def _is_content_page(body_src: str | None) -> bool:
    """True iff this page looks like actual reading material.

    Layered heuristic on raw extracted text (no NLP, no LLM):

    - At least 500 characters of body — skips blanks, dividers, chapter title
      pages (which are typically just an image caption).
    - At least 200 alphabetic characters — skips number-heavy index pages.
    - Less than 40% of letters are uppercase — skips all-caps copyright /
      title-page boilerplate.
    - First 300 characters contain none of the Greek front-matter markers
      (ΠΕΡΙΕΧΟΜΕΝΑ, ΠΡΟΛΟΓΟΣ, ΣΤΟΙΧΕΙΑ ΕΚΔΟΣΗΣ, ΥΠΟΥΡΓΕΙΟ, ISBN). Catches
      TOC, preface, and publishing-info pages.
    - Dots (".") make up < 8% of characters — TOC pages use dot-leader
      filler between entries and their page numbers; this is a strong TOC
      signature.
    - Contains a section-number pattern like "1.1" or "2.3" — positive
      signal that the page is chapter content rather than a continuation of
      a preface, an introduction without numbered sections, etc.

    Thresholds were calibrated against the Greek "Ιστορία του Αρχαίου
    Κόσμου" textbook (298 pages) where actual chapter content starts on
    page 11. Pages 1-10 are all front-matter and all get rejected; page 11
    passes.
    """
    if not body_src or len(body_src) < 500:
        return False
    letters = [c for c in body_src if c.isalpha()]
    if len(letters) < 200:
        return False
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    if upper_ratio >= 0.40:
        return False
    first_chunk = body_src[:300]
    if any(marker in first_chunk for marker in _GREEK_FRONT_MATTER_MARKERS):
        return False
    dot_ratio = body_src.count(".") / len(body_src)
    if dot_ratio > 0.08:
        return False
    if not _SECTION_NUMBER_PATTERN.search(body_src):
        return False
    return True


def first_content_page_number(db: Session, story_id: int) -> int:
    """Return the lowest page_number whose body_src passes ``_is_content_page``.

    Falls back to 1 when the heuristic rejects every page (e.g. a brand-new
    story where pages still haven't been imported, or a very short text where
    no page reaches the threshold). The frontend uses this so the user lands
    on actual reading material instead of a copyright page on first open.
    """
    rows = (
        db.query(Page.page_number, Page.body_src)
        .filter(Page.story_id == story_id)
        .order_by(Page.page_number.asc())
        .all()
    )
    for page_number, body_src in rows:
        if _is_content_page(body_src):
            return page_number
    return 1


# ─── Page warming (cron) ───────────────────────────────────────────────────


DEFAULT_PAGES_AHEAD_BUFFER = 5


def _last_viewed_page_number(db: Session, story_id: int) -> int:
    """Highest page_number the user has actually opened, or 0 if none yet.

    `Page.viewed_at` is stamped only by the GET /pages/{n} endpoint — cron-
    warmed pages do NOT stamp it, so this returns "how far the user has
    read," not "how far the cron has warmed."
    """
    from sqlalchemy import func as _func
    result = (
        db.query(_func.max(Page.page_number))
        .filter(Page.story_id == story_id, Page.viewed_at.isnot(None))
        .scalar()
    )
    return int(result or 0)


def _verified_pages_ahead(db: Session, story_id: int, last_viewed: int) -> int:
    """Count pages already through the quality gate beyond the last-viewed
    page. This is the buffer the cron tries to keep ≥ ``buffer`` deep.
    """
    return (
        db.query(Page)
        .filter(
            Page.story_id == story_id,
            Page.page_number > last_viewed,
            Page.mappings_verified_at.isnot(None),
        )
        .count()
    )


def warm_pages_ahead(
    db: Session,
    story_id: int,
    *,
    buffer: int = DEFAULT_PAGES_AHEAD_BUFFER,
    max_to_warm: int | None = None,
) -> dict:
    """Ensure ``buffer`` verified pages exist beyond the last-viewed page.

    Reads the last-viewed page number (highest ``page_number`` with non-null
    ``viewed_at``), counts how many later pages have already passed the
    quality gate, and processes additional pages forward until the buffer is
    full. Each page processed costs one Sonnet call (~$0.30-0.50, ~2-3 min)
    so a buffer of 5 has bounded daily cost: it only refills as the user
    reads, never further.

    Returns a summary dict for cron logging.
    Skipped silently if the story has no unverified pages remaining beyond
    the user's current position (whole book gated through, or buffer is
    already past the end).
    """
    from app.models import Story as _Story

    story = db.get(_Story, story_id)
    if story is None:
        return {"story_id": story_id, "error": "story not found"}

    last_viewed = _last_viewed_page_number(db, story_id)
    ahead_before = _verified_pages_ahead(db, story_id, last_viewed)

    summary: dict = {
        "story_id": story_id,
        "story_title": story.title,
        "last_viewed": last_viewed,
        "ahead_before": ahead_before,
        "pages_warmed": [],
        "errors": [],
    }

    if ahead_before >= buffer:
        summary["ahead_after"] = ahead_before
        return summary

    needed = buffer - ahead_before
    if max_to_warm is not None:
        needed = min(needed, max_to_warm)

    candidates: list[Page] = (
        db.query(Page)
        .filter(
            Page.story_id == story_id,
            Page.page_number > last_viewed,
            Page.mappings_verified_at.is_(None),
        )
        .order_by(Page.page_number.asc())
        .limit(needed)
        .all()
    )

    for page in candidates:
        try:
            process_page(db, page)
        except Exception as e:
            log.warning("warm_pages_ahead: page %d of story %d failed: %s",
                        page.page_number, story_id, e)
            summary["errors"].append((page.page_number, str(e)))
            continue
        # Only count it as warmed if the quality gate actually finished.
        db.refresh(page)
        if page.mappings_verified_at is not None:
            summary["pages_warmed"].append(page.page_number)

    summary["ahead_after"] = _verified_pages_ahead(db, story_id, last_viewed)
    return summary


def warm_all_active_stories(
    db: Session,
    language_code: str,
    *,
    buffer: int = DEFAULT_PAGES_AHEAD_BUFFER,
    max_to_warm_per_story: int | None = None,
) -> list[dict]:
    """Run warm_pages_ahead for every active story in ``language_code``.

    Used by the cron wrapper. Iterates stories oldest-first so newly-imported
    books don't starve out the one the user is actively reading.
    """
    from app.models import Story as _Story

    stories = (
        db.query(_Story)
        .filter(_Story.language_code == language_code, _Story.status == "active")
        .order_by(_Story.created_at.asc())
        .all()
    )
    summaries: list[dict] = []
    for story in stories:
        s = warm_pages_ahead(
            db,
            story_id=story.id,
            buffer=buffer,
            max_to_warm=max_to_warm_per_story,
        )
        summaries.append(s)
    return summaries
