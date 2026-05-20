"""Reading-as-mapping intake — page-based, lazy.

Flow:
1. **Import** (POST /api/texts) creates a Story and Page rows with raw text
   only — no tokenization. PDFs are extracted page-by-page; pastes become a
   single Page.
2. **View page** (GET /api/texts/{story_id}/pages/{n}) tokenizes that page
   on first request, creates Lemma rows for new lemmas (with NULL gloss),
   creates PageWord rows, stamps `processed_at`. Subsequent views are cached.
3. **Mark** (PATCH .../mark) updates UserLemmaKnowledge. When a lemma is
   first marked `unknown`, we fetch a tiny gloss (deferred to a separate
   service — for now the Lemma keeps NULL gloss).
4. **Expand** (deferred) fetches etymology, examples, conjugations for a
   lemma on explicit request.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Lemma, Story, Page, PageWord, UserLemmaKnowledge
from app.services import pdf_extract
from app.services.cognate_detector import link_intra_greek_cognates, propagate_known_via_cognate
from app.services import lemma_quality
from app.services.languages import (
    NLPProvider, ProviderUnavailable, Token, get_provider,
)

log = logging.getLogger(__name__)


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

    # Phase 1: pure compute
    sentences = _split_into_sentences(page.body_src)
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
    for surface, (lemma_form, lemma_bare, pos) in surface_to_lemma.items():
        if lemma_bare in bare_to_lemma_id:
            continue
        existing = _lookup_lemma(db, language_code, lemma_bare)
        if existing:
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

    tokens = []
    for w in words:
        lemma = lemmas_by_id.get(w.lemma_id) if w.lemma_id else None
        ulk = knowledge_by_lemma.get(w.lemma_id) if w.lemma_id else None
        state = ulk.knowledge_state if ulk else None
        is_punct = lemma is None and not any(c.isalpha() for c in w.surface_form)
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
            "is_function_word": lemma is not None and lemma.word_category == "function_word",
            "is_heading": w.quality_note == "heading",
            "is_known": state == "known",
            "is_acquiring": state in ("acquiring", "learning"),
            "is_encountered": state == "encountered",
            "is_unknown": state == "unknown",
            "is_ignored": state == "ignore",
            "is_new": ulk is None and lemma is not None and lemma.word_category != "function_word",
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

    # Collect distinct lemma_ids on the page
    lemma_ids = {
        w.lemma_id
        for w in db.query(PageWord).filter(PageWord.page_id == page.id).all()
        if w.lemma_id is not None
    }
    if not lemma_ids:
        return 0

    # Filter out lemmas that already have any ULK
    already_known_ids = {
        ulk.lemma_id
        for ulk in db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id.in_(lemma_ids)
        ).all()
    }
    pending_ids = lemma_ids - already_known_ids

    # Filter out function words (don't enrol them in scheduling)
    lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(pending_ids)).all()
    eligible_ids = [
        l.lemma_id for l in lemmas
        if l.word_category != "function_word"
        and l.lemma_bare not in function_word_bares
    ]
    if not eligible_ids:
        return 0

    # Bulk-mark via mark_lemma so cognate propagation runs uniformly
    count = 0
    for lid in eligible_ids:
        mark_lemma(db, lemma_id=lid, state="known", fetch_gloss=False)
        count += 1
    log.info("Bulk-marked %d lemmas as known on page %d of story %d",
             count, page_number, story_id)
    return count


def mark_lemma(db: Session, lemma_id: int, state: str, *, fetch_gloss: bool = True) -> UserLemmaKnowledge:
    """Set the user's knowledge state for a lemma. Creates ULK if missing.

    Behaviour by ``state``:
      - ``known``: ULK state set to ``known``; cognate propagation runs.
      - ``unknown``: enters the SRS engine immediately. The lemma is routed
        through ``start_acquisition`` with ``source='reading_intake'`` and
        ``due_immediately=True``, so it lands in Box 1 with the next review
        due now. The daily intro cap still applies: cap-exceeded marks
        remain in ``encountered`` state and promote on a future day.
        A tiny English gloss is also fetched if missing.
      - ``encountered``: lightweight state-only update; no SRS enrolment.
      - ``ignore``: mark as a proper name / out-of-band token and remove from SRS.
    """
    valid = {"known", "unknown", "encountered", "ignore"}
    if state not in valid:
        raise ValueError(f"Invalid state {state!r}; expected one of {valid}")

    from app.services.canonical_resolution import resolve_canonical_lemma_id

    lemma_id = resolve_canonical_lemma_id(db, lemma_id)

    # 'unknown' has its own pipeline — enrol into acquisition, fetch gloss.
    if state == "unknown":
        from app.services.acquisition_service import start_acquisition
        ulk = start_acquisition(
            db,
            lemma_id=lemma_id,
            source="reading_intake",
            due_immediately=True,
        )
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
    if ulk is None:
        ulk = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state=state,
            introduced_at=now,
            source="reading_intake",
        )
        db.add(ulk)
    else:
        ulk.knowledge_state = state
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

