"""Centralized lemma quality gate — run after every lemma creation path.

All import paths MUST call `run_quality_gates(db, lemma_ids)` after creating
Lemma records. This is the single post-creation pipeline that:
1. Cleans bare forms (punctuation artifacts)
2. Assigns frequency ranks
3. Runs variant detection (LLM + definite + mark)
4. Queues enrichment (forms, etymology, transliteration)
5. Stamps `gates_completed_at` — session builder rejects ungated lemmas

A cron in update_material.py catches any lemmas that slip through without gates.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Lemma
from app.services.sentence_validator import normalize_alef, strip_diacritics

logger = logging.getLogger(__name__)

# Lazy-loaded frequency rank map
_rank_map: Optional[dict[str, int]] = None
_CAMEL_CACHE = Path(__file__).resolve().parent.parent / "data" / "MSA_freq_lists.tsv"

ARABIC_PUNCT = re.compile(r'[،؟؛«»\u060C\u061B\u061F.,:;!?\"\'\-\(\)\[\]{}…]')
# Regex to strip ال (with optional diacritics) from start of word.
# Handles diacritized text like الْكتاب → skips combining marks on ل.
# Tashkeel range: U+064B-U+065F (fathatan..wavy hamza below) + U+0670 (superscript alef)
# NOTE: does NOT strip وال — too many false positives (والد, والي, والدة are root letters).
_AL_PREFIX = re.compile('^ال[\u064B-\u065F\u0670]*')


def _normalize(text: str) -> str:
    """Full normalization for frequency matching."""
    text = strip_diacritics(text)
    text = text.replace('\u0640', '')  # tatweel
    text = normalize_alef(text)
    return text


def strip_display_definite_article(lemma_ar: str, lemma_ar_bare: str) -> str:
    """Return the diacritized headword with a leading definite article removed.

    The lemma headword (`lemma_ar`, shown on intro cards) must be the citation
    form, not the in-text definite surface form. Scans (textbook_scan, OCR, book
    import) captured words like \u0627\u0644\u0652\u0643\u064e\u0647\u0652\u0641 / \u0627\u0644\u0633\u064e\u0651\u0645\u064e\u0627\u0648\u0650\u064a\u0651 verbatim while the bare form
    correctly stripped \u0627\u0644 \u2014 so display and bare diverged and the card showed
    "the cave" instead of "cave" (2026-06-13).

    Only strips when the bare form does NOT carry \u0627\u0644 (so genuinely article-initial
    lemmas like \u0627\u0644\u0644\u0647 / \u0627\u0644\u0630\u064a are left alone), and only accepts the result when its
    normalization still equals `lemma_ar_bare` \u2014 guaranteeing display and bare
    stay consistent. Also drops the sun-letter shadda left on the first root
    consonant (\u0627\u0644\u0633\u064e\u0651\u0645\u064e\u0627\u0648\u0650\u064a\u0651 \u2192 \u0633\u064e\u0645\u064e\u0627\u0648\u0650\u064a\u0651).
    """
    if not lemma_ar or not lemma_ar_bare or lemma_ar_bare.startswith("\u0627\u0644"):
        return lemma_ar
    stripped = _AL_PREFIX.sub("", lemma_ar)
    if stripped == lemma_ar:
        return lemma_ar  # no article present
    letters = [i for i, c in enumerate(stripped) if '\u0621' <= c <= '\u064a']
    if len(letters) >= 2:
        sh = stripped.find('\u0651')  # shadda
        if 0 <= sh < letters[1]:      # belongs to the now-leading consonant
            stripped = stripped[:sh] + stripped[sh + 1:]
    # Safety: never create a new display/bare divergence.
    if _normalize(stripped) == _normalize(lemma_ar_bare):
        return stripped
    return lemma_ar


def _load_rank_map() -> dict[str, int]:
    """Load CAMeL MSA frequency data. Cached after first call."""
    global _rank_map
    if _rank_map is not None:
        return _rank_map

    if not _CAMEL_CACHE.exists():
        logger.warning(f"CAMeL frequency file not found: {_CAMEL_CACHE}")
        _rank_map = {}
        return _rank_map

    logger.info(f"Loading CAMeL frequency data from {_CAMEL_CACHE}...")
    freq: dict[str, int] = {}
    with open(_CAMEL_CACHE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) != 2:
                continue
            word, count_str = parts
            try:
                count = int(count_str)
            except ValueError:
                continue
            normalized = _normalize(word)
            if normalized in freq:
                freq[normalized] += count
            else:
                freq[normalized] = count

    # Convert to rank map (sorted by count descending)
    sorted_forms = sorted(freq.items(), key=lambda x: -x[1])
    _rank_map = {form: rank for rank, (form, _) in enumerate(sorted_forms, 1)}
    logger.info(f"Loaded {len(_rank_map):,} frequency entries")
    return _rank_map


def clean_bare_form(bare: str) -> str:
    """Strip punctuation artifacts from bare form.

    NOTE: Does NOT strip ال-prefix automatically — too many false positives
    (الله, الذي, Form VIII verbs like التقى). Use the LLM-powered cleanup
    script (cleanup_dirty_bare_forms.py) for ال-prefix + ه→ة fixes.
    """
    bare = ARABIC_PUNCT.sub('', bare)
    bare = bare.replace('«', '').replace('»', '')
    return bare.strip()


def normalize_ta_marbuta(bare: str, had_al_prefix: bool = False) -> str:
    """Normalize final ه → ة when it's likely an OCR artifact.

    Only fires when the ال-prefix was also present (strong OCR signal).
    Protects legitimate ه-ending words like وجه, فقه, شبه.
    """
    if had_al_prefix and len(bare) > 1 and bare.endswith('ه'):
        return bare[:-1] + 'ة'
    return bare


def assign_frequency_rank(lemma: Lemma) -> bool:
    """Assign frequency_rank from CAMeL data. Returns True if assigned."""
    rank_map = _load_rank_map()
    if not rank_map:
        return False

    bare = _normalize(lemma.lemma_ar_bare) if lemma.lemma_ar_bare else None
    if not bare:
        return False

    rank = rank_map.get(bare)
    if rank is None and bare.startswith('ال'):
        rank = rank_map.get(bare[2:])
    elif rank is None:
        rank = rank_map.get('ال' + bare)

    if rank is not None:
        lemma.frequency_rank = rank
        return True
    return False


def find_duplicate_canonical(db: Session, bare: str, exclude_id: int | None = None) -> Lemma | None:
    """Find an existing canonical lemma with the same normalized bare form."""
    normalized = _normalize(bare)
    # Efficient query — search by bare form directly instead of loading all canonicals
    candidates = db.query(Lemma).filter(
        Lemma.canonical_lemma_id.is_(None),
        Lemma.word_category != "junk",
        Lemma.lemma_ar_bare == bare,
    ).all()
    # Also try normalized variants
    if not candidates:
        candidates = db.query(Lemma).filter(
            Lemma.canonical_lemma_id.is_(None),
            Lemma.word_category != "junk",
            Lemma.lemma_ar_bare.in_([normalized, "ال" + normalized] if not normalized.startswith("ال") else [normalized, normalized[2:]]),
        ).all()
    for c in candidates:
        if c.lemma_id == exclude_id:
            continue
        return c
    return None


def finalize_new_lemmas(db: Session, lemma_ids: list[int]) -> dict:
    """Run quality checks on newly created lemmas.

    Call this after any lemma creation path. It:
    1. Cleans bare forms (punctuation artifacts)
    2. Warns about empty glosses
    3. Assigns frequency ranks
    4. Flags potential duplicates (does NOT auto-merge — log only)

    Returns summary dict with counts.
    """
    if not lemma_ids:
        return {"cleaned": 0, "ranked": 0, "empty_gloss": 0, "potential_dupes": 0}

    # Phase 1: Read — collect data needed (no dirty state)
    lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
    rank_map = _load_rank_map()  # Slow I/O (~5s first call) — no DB dirty state

    cleaned = 0
    ranked = 0
    empty_gloss = 0
    potential_dupes = []

    # Phase 1b: Check for duplicates BEFORE dirtying the session
    for lemma in lemmas:
        if lemma.canonical_lemma_id is None and lemma.lemma_ar_bare:
            bare = clean_bare_form(lemma.lemma_ar_bare)
            dupe = find_duplicate_canonical(db, bare, exclude_id=lemma.lemma_id)
            if dupe:
                potential_dupes.append((lemma.lemma_id, dupe.lemma_id, bare))
                logger.warning(
                    f"Potential duplicate: new lemma {lemma.lemma_id} ({bare} = {lemma.gloss_en}) "
                    f"vs existing {dupe.lemma_id} ({dupe.lemma_ar_bare} = {dupe.gloss_en})"
                )

    # Phase 2: Write — fast attribute assignments (milliseconds)
    for lemma in lemmas:
        # 1. Clean bare form and diacritized form (punctuation only)
        if lemma.lemma_ar_bare:
            new_bare = clean_bare_form(lemma.lemma_ar_bare)
            if new_bare != lemma.lemma_ar_bare:
                lemma.lemma_ar_bare = new_bare
                cleaned += 1
        if lemma.lemma_ar:
            new_ar = clean_bare_form(lemma.lemma_ar)
            if new_ar != lemma.lemma_ar:
                lemma.lemma_ar = new_ar

        # 1b. Strip a definite article baked into the display headword by a scan
        #     (الْكَهْف → كَهْف). Deterministic + safety-checked against the bare form.
        if lemma.lemma_ar and lemma.lemma_ar_bare:
            de_al = strip_display_definite_article(lemma.lemma_ar, lemma.lemma_ar_bare)
            if de_al != lemma.lemma_ar:
                logger.info(
                    f"Lemma {lemma.lemma_id}: stripped definite article from display "
                    f"{lemma.lemma_ar!r} -> {de_al!r}"
                )
                lemma.lemma_ar = de_al
                cleaned += 1
                gf = lemma.grammar_features_json
                if isinstance(gf, list) and "definite_article" in gf:
                    lemma.grammar_features_json = [x for x in gf if x != "definite_article"]

        # 1c. Warn when the display headword is a plural/inflected form rather than
        #     the citation form (e.g. آثَار stored for أَثَر). Best-effort detection —
        #     can't auto-correct (needs re-vocalization), so surface it for review.
        if lemma.lemma_ar and lemma.lemma_ar_bare:
            disp = _normalize(lemma.lemma_ar)
            bare = _normalize(lemma.lemma_ar_bare)
            if disp.startswith("ال") and not bare.startswith("ال"):
                disp = disp[2:]
            loose_disp = disp.rstrip("ة").replace("ى", "ي")
            loose_bare = bare.rstrip("ة").replace("ى", "ي")
            if loose_disp and loose_disp != loose_bare:
                logger.warning(
                    f"Lemma {lemma.lemma_id} ({lemma.gloss_en!r}): display "
                    f"{lemma.lemma_ar!r} diverges from bare {lemma.lemma_ar_bare!r} "
                    f"— possible plural/inflected form stored as headword"
                )

        # 2. Check gloss
        if not lemma.gloss_en:
            empty_gloss += 1
            logger.warning(f"Lemma {lemma.lemma_id} ({lemma.lemma_ar_bare}) has no gloss")

        # 3. Assign frequency rank (uses pre-loaded rank_map, no I/O)
        if lemma.frequency_rank is None and rank_map:
            if assign_frequency_rank(lemma):
                ranked += 1

    summary = {
        "cleaned": cleaned,
        "ranked": ranked,
        "empty_gloss": empty_gloss,
        "potential_dupes": len(potential_dupes),
    }
    if any(v > 0 for v in summary.values()):
        logger.info(f"finalize_new_lemmas: {summary}")
    return summary


def run_quality_gates(
    db: Session,
    lemma_ids: list[int],
    *,
    skip_variants: bool = False,
    enrich: bool = True,
    background_enrich: bool = True,
) -> dict:
    """Single post-creation pipeline for new lemmas. ALL import paths must call this.

    Runs in order:
      1. finalize  — clean bare forms, assign frequency rank, flag dupes
      2. variants  — detect_variants_llm + detect_definite_variants + mark_variants
      3. enrich    — forms, etymology, transliteration (background thread by default)
      4. stamp     — set gates_completed_at on all lemmas

    The caller is responsible for committing after this returns (or this function
    commits internally between variant detection and finalize to release write locks).

    Args:
        db: SQLAlchemy session
        lemma_ids: IDs of newly created Lemma records
        skip_variants: Skip variant detection (e.g. function word backfill)
        enrich: Run enrichment at all
        background_enrich: If True, enrichment runs in a daemon thread (default).
                           If False, runs inline (for scripts/cron).
    Returns:
        Summary dict with gate results.
    """
    if not lemma_ids:
        return {"finalize": {}, "variants": 0, "enriched": False, "stamped": 0}

    # ── Gate 1: Finalize (clean, rank, dedup) ──────────────────────────────
    finalize_summary = finalize_new_lemmas(db, lemma_ids)
    db.commit()

    # ── Gate 1b: Bare-shape consistency (chimera prevention) ──────────────
    # Catches Form V/VI/VII/VIII/X verbs stored with the 3-letter root as bare,
    # defective participles whose bare lacks the implicit ya, and forms_json
    # values from a different root. Auto-corrects the first two; warns on the
    # third. Source: 2026-05-20 chimera audit, see bare_shape_check.py.
    shape_results = []
    try:
        from app.services.bare_shape_check import check_and_correct_bare_shape
        shape_results = check_and_correct_bare_shape(db, lemma_ids)
        if shape_results:
            db.commit()
            from app.services.activity_log import log_activity
            corrected = [r for r in shape_results if r.auto_corrected]
            warned = [r for r in shape_results if r.warnings]
            log_activity(
                db,
                event_type="import_chimera_warning",
                summary=(
                    f"Bare-shape check: {len(corrected)} auto-corrected, "
                    f"{len(warned)} warned across {len(lemma_ids)} new lemmas"
                ),
                detail={
                    "auto_corrected": [
                        {"lemma_id": r.lemma_id, "new_bare": r.new_bare}
                        for r in corrected
                    ],
                    "warnings": [
                        {"lemma_id": r.lemma_id, "warnings": r.warnings}
                        for r in warned
                    ],
                },
            )
    except Exception:
        logger.exception("bare_shape_check failed; continuing without")
        db.rollback()

    # ── Gate 2: Variant detection (LLM + deterministic) ────────────────────
    variants_marked = 0
    if not skip_variants:
        try:
            from app.services.variant_detection import (
                detect_variants_llm,
                detect_definite_variants,
                mark_variants,
            )
            camel_vars = detect_variants_llm(db, lemma_ids=lemma_ids)
            already = {v[0] for v in camel_vars}
            def_vars = detect_definite_variants(
                db, lemma_ids=lemma_ids, already_variant_ids=already,
            )
            all_vars = camel_vars + def_vars
            if all_vars:
                variants_marked = mark_variants(db, all_vars)
            db.commit()
        except Exception as e:
            logger.warning("Variant detection failed for lemmas %s: %s", lemma_ids[:5], e)
            db.rollback()

    # ── Gate 3: Enrichment (forms, etymology, transliteration) ─────────────
    enriched = False
    if enrich and lemma_ids:
        try:
            from app.services.lemma_enrichment import enrich_lemmas_batch
            if background_enrich:
                import threading
                threading.Thread(
                    target=enrich_lemmas_batch,
                    args=(lemma_ids,),
                    daemon=True,
                ).start()
            else:
                enrich_lemmas_batch(lemma_ids)
            enriched = True
        except Exception as e:
            logger.warning("Enrichment failed for lemmas %s: %s", lemma_ids[:5], e)

    # ── Gate 4: Stamp gates_completed_at ───────────────────────────────────
    now = datetime.now(timezone.utc)
    stamped = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.in_(lemma_ids))
        .update({Lemma.gates_completed_at: now}, synchronize_session="fetch")
    )
    db.commit()

    summary = {
        "finalize": finalize_summary,
        "shape_corrected": sum(1 for r in shape_results if r.auto_corrected),
        "shape_warned": sum(1 for r in shape_results if r.warnings),
        "variants": variants_marked,
        "enriched": enriched,
        "stamped": stamped,
    }
    logger.info("run_quality_gates(%d lemmas): %s", len(lemma_ids), summary)
    return summary
