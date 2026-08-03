"""Book import service: OCR children's books → reading goals with sentence extraction.

Pipeline:
  1. Cover metadata extraction (Gemini Vision)
  2. Multi-page OCR (parallel, reuses extract_text_from_image)
  3. LLM cleanup + diacritics + sentence segmentation
  4. LLM translation (separate call for better quality)
  5. Story creation (reuses story_service.import_story)
  6. Sentence record creation (Sentence + SentenceWord)
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Lemma, Sentence, SentenceWord, Story
from app.services.interaction_logger import log_interaction
from app.services.llm import AllProvidersFailed, generate_completion
from app.services.morphology import get_word_features
from app.services.ocr_service import _call_gemini_vision, extract_text_from_image
from app.services.sentence_validator import (
    build_lemma_lookup,
    is_function_word_lemma,
    map_tokens_to_lemmas,
    normalize_alef,
    requires_exact_running_text_alias,
    strip_diacritics,
    tokenize_display,
)
from app.services.story_service import (
    _create_story_words,
    _import_unknown_words,
    _recalculate_story_counts,
    _build_knowledge_map,
    _get_all_lemmas,
    _split_story_sentences,
    _story_word_bare,
)
from app.services.transliteration import transliterate_arabic

logger = logging.getLogger(__name__)


def extract_cover_metadata(cover_image: bytes) -> dict:
    """Extract book metadata from cover/title page image via Gemini Vision."""
    try:
        result = _call_gemini_vision(
            cover_image,
            prompt=(
                "This is the cover or title page of an Arabic children's book. "
                "Extract the following metadata:\n"
                "- title_ar: The Arabic title\n"
                "- title_en: English title if present (or a translation of the Arabic title)\n"
                "- author: Author name if visible\n"
                "- series: Series name if visible\n"
                "- level: Reading level if indicated\n\n"
                "Respond with JSON. Use null for fields not found.\n"
                '{"title_ar": "...", "title_en": "...", "author": "...", "series": "...", "level": "..."}'
            ),
            system_prompt="You extract metadata from book covers. Respond with JSON only.",
        )
        return result
    except Exception:
        logger.exception("Failed to extract cover metadata")
        return {}


def _enhance_image(image_bytes: bytes) -> bytes:
    """Auto-enhance dark/low-contrast images for better OCR."""
    from PIL import Image, ImageEnhance, ImageStat
    import io

    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")

    stat = ImageStat.Stat(img)
    mean_brightness = sum(stat.mean[:3]) / 3

    if mean_brightness >= 120:
        return image_bytes  # bright enough, return original

    brightness_factor = min(1.8, 140 / max(mean_brightness, 1))
    img = ImageEnhance.Brightness(img).enhance(brightness_factor)
    img = ImageEnhance.Contrast(img).enhance(1.3)
    logger.info(f"Enhanced dark image (brightness {mean_brightness:.0f} → factor {brightness_factor:.1f})")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _ocr_page_with_retry(image_bytes: bytes) -> str:
    """OCR a single page with enhancement and retry on failure."""
    enhanced = _enhance_image(image_bytes)
    text = extract_text_from_image(enhanced)
    if text.strip():
        return text

    # Retry with thinking model for difficult pages
    logger.info("Flash returned empty, retrying with thinking model...")
    try:
        result = _call_gemini_vision(
            enhanced,
            prompt=(
                "This is a page from a children's Arabic book. The image may be dark or low quality. "
                "Extract ALL Arabic text from this image carefully. "
                "Preserve the original text exactly as written. "
                "Preserve paragraph breaks with newlines. "
                "Do NOT translate. Do NOT add diacritics that aren't in the original. "
                'Respond with JSON: {"arabic_text": "the extracted Arabic text"}'
            ),
            system_prompt="You are an Arabic OCR system. Extract Arabic text accurately from images. Respond with JSON only.",
            model_override="gemini/gemini-2.5-flash-preview",
        )
        text = result.get("arabic_text", "")
        if text.strip():
            logger.info("Thinking model succeeded")
        return text
    except Exception as e:
        logger.warning(f"Thinking model retry failed: {e}")
        return ""


def ocr_pages_parallel(page_images: list[bytes], max_workers: int = 4) -> list[str]:
    """OCR each page in parallel, return list of per-page Arabic text."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_ocr_page_with_retry, page_images))
    return results


def cleanup_and_segment(raw_text: str, max_retries: int = 2) -> list[dict]:
    """LLM cleanup: fix OCR errors, add full diacritics, segment into sentences.

    Returns list of dicts with 'arabic' key (cleaned, diacritized sentence).
    Retries on empty results (Gemini can return empty on rate-limit).
    """
    for attempt in range(max_retries + 1):
        try:
            logger.info(f"cleanup_and_segment: raw_text length={len(raw_text)}, first 200 chars: {raw_text[:200]!r}")
            result = generate_completion(
                prompt=(
                    "Below is OCR-extracted Arabic text from a children's book. "
                    "The text may have OCR errors, missing or garbled diacritics, "
                    "and incorrect sentence boundaries.\n\n"
                    "Your tasks:\n"
                    "1. Fix OCR errors (garbled characters, missing spaces, merged words)\n"
                    "2. Add FULL diacritics (tashkeel) to every word — this is for language learners\n"
                    "3. Segment into proper sentences\n"
                    "4. Merge any sentences split across page breaks\n"
                    "5. Remove page numbers, headers, footers\n"
                    "6. Skip any non-Arabic text\n\n"
                    "Return a JSON object with a 'sentences' array. Each element has an 'arabic' field "
                    "with the cleaned, fully diacritized Arabic sentence.\n\n"
                    f"OCR text:\n{raw_text}"
                ),
                system_prompt=(
                    "You are an Arabic language expert specializing in children's literature. "
                    "Clean up OCR text and add full diacritics (tashkeel). "
                    "Respond with JSON only."
                ),
                model_override="claude_sonnet",
                temperature=0.2,
                timeout=120,
                task_type="book_import",
            )
            logger.info(f"cleanup_and_segment: LLM result type={type(result).__name__}, keys={list(result.keys()) if isinstance(result, dict) else 'N/A'}, result={str(result)[:500]!r}")
            sentences = result.get("sentences", [])
            if not sentences and isinstance(result, list):
                sentences = result
            filtered = [s for s in sentences if isinstance(s, dict) and s.get("arabic")]
            logger.info(f"cleanup_and_segment: {len(sentences)} raw sentences, {len(filtered)} after filtering")
            if filtered:
                return filtered
            if attempt < max_retries:
                logger.warning(f"cleanup_and_segment returned 0 sentences, retrying ({attempt + 1}/{max_retries})...")
                time.sleep(2)
        except (AllProvidersFailed, Exception):
            if attempt < max_retries:
                logger.warning(f"cleanup_and_segment failed, retrying ({attempt + 1}/{max_retries})...")
                time.sleep(2)
            else:
                logger.exception("Failed to clean up and segment text after retries")
    return []


def translate_sentences(sentences: list[dict]) -> list[dict]:
    """LLM translate: add English translations to cleaned sentences.

    Takes list of dicts with 'arabic' key, returns same list with 'english' added.
    """
    if not sentences:
        return sentences

    arabic_list = [s["arabic"] for s in sentences]
    numbered = "\n".join(f"{i+1}. {a}" for i, a in enumerate(arabic_list))

    try:
        result = generate_completion(
            prompt=(
                "Translate each Arabic sentence to natural English. "
                "These are from a children's book, so use simple, clear language.\n\n"
                "Return a JSON object with a 'translations' array of objects, "
                "each with 'index' (1-based) and 'english' fields.\n\n"
                f"Sentences:\n{numbered}"
            ),
            system_prompt=(
                "You are a professional Arabic-English translator. "
                "Translate children's book sentences clearly and naturally. "
                "Respond with JSON only."
            ),
            model_override="claude_haiku",
            temperature=0.2,
            timeout=120,
            task_type="book_import",
        )
        translations = result.get("translations", [])

        # Build index map
        trans_map = {}
        for t in translations:
            idx = t.get("index")
            if idx is not None:
                trans_map[int(idx)] = t.get("english", "")

        # Merge translations back
        for i, s in enumerate(sentences):
            s["english"] = trans_map.get(i + 1, "")

        return sentences
    except (AllProvidersFailed, Exception):
        logger.exception("Failed to translate sentences")
        return sentences


def _add_transliterations(sentences: list[dict]) -> list[dict]:
    """Add ALA-LC transliteration to each sentence using deterministic transliterator."""
    for s in sentences:
        arabic = s.get("arabic", "")
        if arabic:
            s["transliteration"] = transliterate_arabic(arabic)
    return sentences


def _pick_primary_target(
    mappings: list, db: Session
) -> int | None:
    """Pick the lowest-frequency non-function-word lemma as primary target."""
    lemma_ids = [m.lemma_id for m in mappings if m.lemma_id]
    if not lemma_ids:
        return None

    # Get frequency ranks for these lemmas
    lemmas = (
        db.query(Lemma.lemma_id, Lemma.frequency_rank)
        .filter(Lemma.lemma_id.in_(lemma_ids))
        .all()
    )
    # Higher frequency_rank = rarer word = better target
    # None rank = unknown frequency, treat as very rare
    ranked = sorted(lemmas, key=lambda l: l.frequency_rank or 999999, reverse=True)
    return ranked[0].lemma_id if ranked else lemma_ids[0]


def _resolve_unmapped_via_camel(
    mappings: list,
    lemma_lookup: dict[str, int],
    db: Session,
) -> dict[int, int | None]:
    """Try to resolve unmapped tokens via CAMeL morphological analysis.

    Returns dict of {position: lemma_id} for tokens that were resolved.
    """
    resolved = {}
    for m in mappings:
        if m.lemma_id is not None:
            continue
        if requires_exact_running_text_alias(m.surface_form):
            # A declared exact alias with no unique gated destination must
            # remain unmapped. CAMeL/bare fallback would defeat fail-closed
            # identity and can select the exact collision this layer guards.
            continue
        # Normalize Quranic orthography (alef wasla → regular alef) before CAMeL
        surface_normalized = m.surface_form.replace("\u0671", "\u0627")
        features = get_word_features(surface_normalized)
        lex = features.get("lex", surface_normalized)
        lex_bare = strip_diacritics(lex)
        lex_norm = normalize_alef(lex_bare)
        existing_id = lemma_lookup.get(lex_norm)
        if not existing_id:
            # Try without al-prefix
            if lex_norm.startswith("ال") and len(lex_norm) > 2:
                existing_id = lemma_lookup.get(lex_norm[2:])
            elif not lex_norm.startswith("ال"):
                existing_id = lemma_lookup.get("ال" + lex_norm)
        if existing_id:
            resolved[m.position] = existing_id
    return resolved


def create_book_sentences(
    db: Session,
    story: Story,
    extracted_sentences: list[dict],
    story_word_lookup: dict[str, int] | None = None,
) -> list[Sentence]:
    """Create Sentence + SentenceWord records from extracted book sentences.

    For unmapped tokens, uses CAMeL morphology to resolve to existing lemmas,
    then falls back to story_word_lookup (surface→lemma from StoryWords).
    Tokens that still can't be mapped get lemma_id=None in SentenceWord.
    """
    all_lemmas = _get_all_lemmas(db)
    lemma_lookup = build_lemma_lookup(all_lemmas)

    created = []
    for sent_data in extracted_sentences:
        arabic = sent_data.get("arabic", "")
        english = sent_data.get("english", "")
        transliteration = sent_data.get("transliteration", "")

        tokens = tokenize_display(arabic)
        if len(tokens) < 2:
            continue

        mappings = map_tokens_to_lemmas(
            tokens=tokens,
            lemma_lookup=lemma_lookup,
            target_lemma_id=0,  # no single target for book sentences
            target_bare="",
        )

        # Resolve unmapped tokens via CAMeL morphology
        if any(m.lemma_id is None for m in mappings):
            camel_resolved = _resolve_unmapped_via_camel(mappings, lemma_lookup, db)
            for m in mappings:
                if m.lemma_id is None and m.position in camel_resolved:
                    m.lemma_id = camel_resolved[m.position]

        # Fallback: use StoryWord surface→lemma mappings
        if story_word_lookup and any(m.lemma_id is None for m in mappings):
            for m in mappings:
                if (
                    m.lemma_id is None
                    and not requires_exact_running_text_alias(m.surface_form)
                ):
                    bare = normalize_alef(strip_diacritics(m.surface_form))
                    if bare in story_word_lookup:
                        m.lemma_id = story_word_lookup[bare]

        still_unmapped = [m.surface_form for m in mappings if m.lemma_id is None]
        if still_unmapped:
            logger.info(f"Book sentence has {len(still_unmapped)} unmapped words (kept): {still_unmapped[:5]}")

        # LLM verification of word-lemma mappings — correct or null out bad ones
        mapped = [m for m in mappings if m.lemma_id is not None]
        if mapped:
            from app.services.sentence_validator import (
                apply_corrections,
                verify_and_correct_mappings_llm,
            )
            lemma_map_for_verify = {
                l.lemma_id: l for l in db.query(Lemma).filter(
                    Lemma.lemma_id.in_([m.lemma_id for m in mapped])
                ).all()
            }
            corrections = verify_and_correct_mappings_llm(
                arabic, english, mapped, lemma_map_for_verify,
            )
            verification_ran = corrections is not None
            if corrections is None:
                corrections = []  # LLM unavailable — store without verification
            failed_positions = apply_corrections(
                corrections, mappings, db, arabic_text=arabic,
            )
            # Book import: null out mappings that couldn't be corrected
            pos_to_mapping = {m.position: m for m in mappings}
            for pos in failed_positions:
                m = pos_to_mapping.get(pos)
                if m:
                    m.lemma_id = None
        else:
            verification_ran = False

        target_lid = _pick_primary_target(mappings, db)

        sent = Sentence(
            arabic_text=arabic,
            english_translation=english,
            transliteration=transliteration,
            source="book",
            target_lemma_id=target_lid,
            story_id=story.id,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            mappings_verified_at=datetime.now(timezone.utc) if verification_ran else None,
            max_word_count=len(tokens),
            page_number=sent_data.get("page_number"),
        )
        db.add(sent)
        db.flush()

        for m in mappings:
            sw = SentenceWord(
                sentence_id=sent.id,
                position=m.position,
                surface_form=m.surface_form,
                lemma_id=m.lemma_id,
                is_target_word=(m.lemma_id == target_lid) if target_lid else False,
            )
            db.add(sw)

        # Commit per-sentence so the SQLite write lock is released before
        # the next iteration's Lemma query autoflushes and holds it through
        # the next verify_and_correct_mappings_llm call.
        db.commit()

        created.append(sent)

    return created


def import_book(
    db: Session,
    cover_image: bytes | None,
    page_images: list[bytes],
    title_override: str | None = None,
) -> tuple[Story, list[int]]:
    """Full book import pipeline.

    Args:
        db: Database session.
        cover_image: Cover/title page image bytes (for metadata extraction).
        page_images: Content page images in reading order.
        title_override: Optional title override (skips cover extraction).

    Returns:
        (story, new_lemma_ids) — the created Story and IDs of newly created Lemmas.
    """
    # Step 1: Cover metadata
    metadata = {}
    if cover_image and not title_override:
        metadata = extract_cover_metadata(cover_image)
        logger.info(f"Cover metadata: {metadata}")

    title_ar = title_override or metadata.get("title_ar")
    title_en = metadata.get("title_en")

    # Step 2: OCR all content pages in parallel
    logger.info(f"OCR-ing {len(page_images)} pages...")
    page_texts = ocr_pages_parallel(page_images)

    if not any(t.strip() for t in page_texts):
        raise ValueError("No text extracted from any page")

    logger.info(f"Extracted text from {len(page_images)} pages")

    # Step 3: LLM cleanup + diacritics + segmentation — per page
    logger.info("Cleaning up and segmenting text per page...")
    all_sentences: list[dict] = []
    for page_idx, page_text in enumerate(page_texts):
        if not page_text.strip():
            continue
        page_num = page_idx + 1
        page_sents = cleanup_and_segment(page_text)
        for s in page_sents:
            s["page_number"] = page_num
        all_sentences.extend(page_sents)
        logger.info(f"Page {page_num}: {len(page_sents)} sentences")

    sentences = all_sentences
    logger.info(f"Extracted {len(sentences)} total sentences")

    if not sentences:
        raise ValueError("No sentences could be extracted from the text")

    # Step 4: LLM translate
    logger.info("Translating sentences...")
    sentences = translate_sentences(sentences)

    # Step 5: Deterministic transliteration
    sentences = _add_transliterations(sentences)

    # Build cleaned body — join with "." so _create_story_words splits correctly
    cleaned_body = ". ".join(s["arabic"] for s in sentences)

    # Build sentence_index → page_number mapping
    sent_page_map: dict[int, int] = {}
    for i, s in enumerate(sentences):
        sent_page_map[i] = s.get("page_number", 1)

    # Step 6: Create story via existing story_service logic
    all_lemmas = _get_all_lemmas(db)
    lemma_lookup = build_lemma_lookup(all_lemmas)
    knowledge_map = _build_knowledge_map(db)

    body_en = " ".join(s.get("english", "") for s in sentences)

    story = Story(
        title_ar=title_ar,
        title_en=title_en,
        body_ar=cleaned_body,
        body_en=body_en.strip() or None,
        transliteration=" ".join(s.get("transliteration", "") for s in sentences) or None,
        source="book_ocr",
        status="active",
        page_count=len(page_images),
    )
    db.add(story)
    db.flush()

    # Create StoryWords (reuse story_service helpers)
    total, known, func = _create_story_words(
        db, story, cleaned_body, lemma_lookup, knowledge_map
    )

    # Tag StoryWords with page_number based on sentence_index → page mapping
    for sw in story.words:
        page = sent_page_map.get(sw.sentence_index)
        if page is not None:
            sw.page_number = page

    # Import unknown words (creates Lemma entries + runs quality gates internally)
    new_ids = _import_unknown_words(db, story, lemma_lookup)

    # Deliberately do not create UserLemmaKnowledge rows here. A book import
    # populates the library and lexicon only; learner state starts changing
    # only when the reader actually completes a page. This boundary matters
    # for large novels: merely making
    # a text available must never enqueue hundreds of words for study.
    # Only lemmas created by this import receive book provenance. Rewriting
    # provenance on existing vocabulary makes importing a book silently change
    # the global curriculum; existing lexical rows must remain exactly as they
    # were before the book was added.
    source_updated = 0
    if new_ids:
        for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(new_ids)).all():
            lemma.source = "book"
            lemma.source_story_id = story.id
            source_updated += 1
    if source_updated:
        db.flush()
        logger.info(f"Tagged {source_updated} new book lemmas for story {story.id}")

    # Recalculate readiness
    _recalculate_story_counts(db, story)

    # Build surface→lemma fallback from StoryWords (which _import_unknown_words resolved)
    story_word_lookup: dict[str, int] = {}
    for sw in story.words:
        if sw.lemma_id is not None:
            bare = normalize_alef(strip_diacritics(sw.surface_form))
            story_word_lookup[bare] = sw.lemma_id

    # Step 7: Create Sentence + SentenceWord records
    logger.info("Creating sentence records...")
    created_sentences = create_book_sentences(db, story, sentences, story_word_lookup)
    logger.info(f"Created {len(created_sentences)} sentence records")

    db.commit()
    db.refresh(story)

    log_interaction(
        event="book_imported",
        story_id=story.id,
        total_words=story.total_words,
        known_count=story.known_count,
        readiness_pct=story.readiness_pct,
        new_words_imported=len(new_ids),
        page_count=len(page_images),
        sentence_count=len(created_sentences),
        metadata=metadata,
    )

    return story, new_ids


def import_processed_book(
    db: Session,
    *,
    title_ar: str,
    title_en: str | None,
    author: str | None,
    pages: list[dict],
    book_metadata: dict | None = None,
    curated_lexicon: list[dict] | None = None,
    strict_lexicon: bool = False,
) -> tuple[Story, list[int]]:
    """Import an already cleaned bilingual artifact without OCR/translation.

    This is the bridge for Bookifier outputs.  ``pages`` is deliberately a
    small stable interchange shape (Arabic plus optional English), so each
    source pipeline can adapt its own cache/manifests without teaching Alif
    about every historical artifact format.  Like photo import, this creates
    no UserLemmaKnowledge rows.
    """
    normalized_pages = [
        {
            "arabic": (page.get("arabic") or "").strip(),
            "english": (page.get("english") or "").strip() or None,
            "source_page_number": page.get("source_page_number"),
            "pdf_page_number": page.get("pdf_page_number"),
        }
        for page in pages
        if (page.get("arabic") or "").strip()
    ]
    if not normalized_pages:
        raise ValueError("At least one non-empty Arabic page is required")

    body_ar = "\n".join(page["arabic"] for page in normalized_pages)
    body_en = "\n\n".join(
        page["english"] for page in normalized_pages if page["english"]
    ) or None
    story = Story(
        title_ar=title_ar,
        title_en=title_en,
        body_ar=body_ar,
        body_en=body_en,
        source="book_ocr",
        # Keep a partially staged import out of the library while lexical
        # analysis runs without a write lock.
        status="generating",
        page_count=len(normalized_pages),
        metadata_json={
            "author": author,
            **(book_metadata or {}),
            "processed_pages": {
                str(index): {
                    "english": page["english"],
                    "source_page_number": page["source_page_number"],
                    "pdf_page_number": page["pdf_page_number"],
                }
                for index, page in enumerate(normalized_pages, start=1)
            },
        },
    )
    db.add(story)
    db.flush()

    all_lemmas = _get_all_lemmas(db)
    lemma_lookup = build_lemma_lookup(all_lemmas)
    curated_ids: list[int] = []
    curated_surface_map: dict[str, tuple[int, str, str | None]] = {}
    for entry in curated_lexicon or []:
        lemma_ar = (entry.get("lemma_ar") or "").strip()
        gloss_en = (entry.get("gloss_en") or "").strip()
        surfaces = [
            surface.strip()
            for surface in (entry.get("surfaces") or [])
            if isinstance(surface, str) and surface.strip()
        ]
        if not lemma_ar or not gloss_en or not surfaces:
            raise ValueError(
                "Every curated lexicon entry needs lemma_ar, gloss_en, and surfaces"
            )
        lemma_bare = strip_diacritics(lemma_ar)
        lemma_norm = normalize_alef(lemma_bare)
        # A normalized-bare lookup is not enough for curated data: it chooses
        # whichever homograph was inserted first (مَلَك "angel" can steal
        # مَلِك "king"). Resolve the explicit Arabic/gloss/POS identity with
        # the same collision-aware correction machinery used by validators.
        from app.services.sentence_validator import correct_mapping
        lemma_id = correct_mapping(
            db,
            lemma_ar,
            gloss_en,
            entry.get("pos") or "",
            current_lemma_id=None,
            lemma_lookup=lemma_lookup,
        )
        if lemma_id is None:
            lemma = Lemma(
                lemma_ar=lemma_ar,
                lemma_ar_bare=lemma_bare,
                pos=entry.get("pos"),
                gloss_en=gloss_en,
                source="book",
                source_story_id=story.id,
                word_category=(
                    "proper_name" if entry.get("name_type") else None
                ),
            )
            db.add(lemma)
            db.flush()
            lemma_id = lemma.lemma_id
            curated_ids.append(lemma_id)
            if lemma_norm not in lemma_lookup:
                lemma_lookup[lemma_norm] = lemma_id
        for surface in surfaces:
            surface_norm = _story_word_bare(surface)
            if surface_norm:
                lemma_lookup[surface_norm] = lemma_id
                curated_surface_map[surface_norm] = (
                    lemma_id,
                    gloss_en,
                    entry.get("name_type"),
                )

    knowledge_map = _build_knowledge_map(db)
    _create_story_words(db, story, body_ar, lemma_lookup, knowledge_map)
    # A curated surface is an explicit editorial decision and therefore owns
    # the mapping even when the general lookup produced *some* lemma. Applying
    # it only to unmapped rows allowed existing homographs to defeat curation.
    curated_positions: set[int] = set()
    for word in story.words:
        curated = curated_surface_map.get(_story_word_bare(word.surface_form))
        if curated is None:
            continue
        lemma_id, gloss_en, name_type = curated
        word.lemma_id = lemma_id
        word.gloss_en = gloss_en
        word.is_known_at_creation = bool(
            name_type or knowledge_map.get(lemma_id) in ("learning", "known")
        )
        curated_positions.add(word.position)
        if name_type:
            word.is_function_word = True
            word.name_type = name_type

    # _create_story_words uses exactly this punctuation-aware segmentation. Build
    # a matching sentence-index → page map, retaining multiple sentences per
    # reader page.
    sentence_pages: list[int] = []
    for page_number, page in enumerate(normalized_pages, start=1):
        parts = _split_story_sentences(page["arabic"])
        sentence_pages.extend([page_number] * len(parts))
    for word in story.words:
        if 0 <= (word.sentence_index or 0) < len(sentence_pages):
            word.page_number = sentence_pages[word.sentence_index or 0]

    # Unlike ordinary story import, a processed bilingual book already gives
    # us unusually strong evidence: stable page boundaries and the published
    # English translation. Verify every mapped token, not only newly created
    # lemmas, before the hidden staging row can become reader-visible.
    # Collision alternatives are reconstructed from the full gated inventory,
    # so homographs such as مَلِك/مَلَك and أَحَد/أَحَد require an explicit
    # contextual choice instead of inheriting lookup insertion order.
    db.commit()
    db.refresh(story)

    try:
        # Curated rows were committed with the hidden staging Story so external
        # calls do not hold SQLite's writer lock. Gate them immediately: if
        # later contextual verification blocks the book, no minimal/ungated
        # lemma is left behind in the shared lexicon.
        if curated_ids:
            from app.services.lemma_quality import run_quality_gates
            run_quality_gates(
                db,
                curated_ids,
                background_enrich=False,
            )
            db.refresh(story)

        _verify_processed_book_story_mappings(
            db,
            story,
            normalized_pages,
            trusted_positions=curated_positions,
        )

        if strict_lexicon:
            unmapped = sorted({
                _story_word_bare(word.surface_form)
                for word in story.words
                if word.lemma_id is None
                and not word.is_function_word
                and _story_word_bare(word.surface_form)
            })
            if unmapped:
                raise ValueError(
                    "Strict processed-book import has unmapped Arabic forms: "
                    + ", ".join(unmapped)
                )
            new_ids = curated_ids
        else:
            new_ids = curated_ids + _import_unknown_words(db, story, lemma_lookup)

        _recalculate_story_counts(db, story)
        story.status = "active"
        db.commit()
        db.refresh(story)
    except Exception:
        db.rollback()
        failed_story = db.get(Story, story.id)
        if failed_story is not None:
            failed_story.status = "failed"
            db.commit()
        raise

    log_interaction(
        event="processed_book_imported",
        story_id=story.id,
        page_count=story.page_count,
        total_words=story.total_words,
        new_lemmas=len(new_ids),
    )
    return story, new_ids


def _verify_processed_book_story_mappings(
    db: Session,
    story: Story,
    normalized_pages: list[dict],
    *,
    batch_size: int = 1,
    trusted_positions: set[int] | None = None,
    apply_repairs: bool = False,
) -> dict[str, int]:
    """Contextually verify every mapped StoryWord in a processed book.

    The function is intentionally fail-closed. A missing provider verdict or
    one malformed row aborts the import while its Story remains hidden. By
    default, even a structurally valid proposed change is a curation blocker,
    not an automatic write: model output is evidence, not lexical authority.
    The explicit ``apply_repairs`` mode exists for reviewed maintenance runs;
    it may use only fully gated canonical destinations and clears unresolved
    bad mappings for the ordinary full-enrichment pipeline.

    Page-level English is supplied to every Arabic sentence on that page. A
    processed artifact's English often spans clauses differently from Arabic,
    so trying to align fragments positionally can attach the wrong translation
    and manufacture false mapping verdicts.
    """
    from app.services.sentence_validator import (
        TokenMapping,
        apply_corrections,
        batch_verify_sentences,
        build_comprehensive_lemma_lookup,
    )

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    trusted_positions = trusted_positions or set()

    # The caller commits the staging Story first. Keep every external verifier
    # call outside a SQLite write transaction, and commit only after a complete
    # batch has trustworthy verdicts.
    db.expire_all()
    story = db.get(Story, story.id)
    if story is None:
        raise ValueError("Processed-book story no longer exists")

    gated_lookup = build_comprehensive_lemma_lookup(db, require_gated=True)
    words = sorted(story.words, key=lambda word: word.position)
    story_word_by_position = {word.position: word for word in words}

    page_english = {
        page_number: (page.get("english") or "").strip()
        for page_number, page in enumerate(normalized_pages, start=1)
    }
    grouped: dict[tuple[int, int], list] = {}
    for word in words:
        if word.lemma_id is None or word.position in trusted_positions:
            continue
        page_number = word.page_number or 1
        sentence_index = word.sentence_index or 0
        grouped.setdefault((page_number, sentence_index), []).append(word)

    units: list[dict] = []
    lemma_ids: set[int] = set()
    for (page_number, _sentence_index), unit_words in sorted(grouped.items()):
        mappings = []
        for word in unit_words:
            # Re-run the surface through the current gated inventory solely to
            # recover every collision candidate. Preserve the actual stored ID
            # as option A so the verifier audits what would reach the reader.
            remapped = map_tokens_to_lemmas(
                [word.surface_form],
                gated_lookup,
                target_lemma_id=0,
                target_bare="",
            )
            candidate_ids: list[int] = []
            via_clitic = False
            if remapped:
                candidate_ids = [
                    candidate
                    for candidate in [
                        remapped[0].lemma_id,
                        *(remapped[0].alternative_lemma_ids or []),
                    ]
                    if candidate is not None and candidate != word.lemma_id
                ]
                via_clitic = remapped[0].via_clitic
            alternatives = list(dict.fromkeys(candidate_ids))
            mapping = TokenMapping(
                position=word.position,
                surface_form=word.surface_form,
                lemma_id=word.lemma_id,
                is_target=False,
                is_function_word=bool(word.is_function_word),
                alternative_lemma_ids=alternatives or None,
                via_clitic=via_clitic,
            )
            mappings.append(mapping)
            lemma_ids.add(word.lemma_id)
            lemma_ids.update(alternatives)

        units.append({
            "arabic": " ".join(word.surface_form for word in unit_words),
            "english": page_english.get(page_number, ""),
            "mappings": mappings,
            "has_ambiguous": any(
                mapping.alternative_lemma_ids for mapping in mappings
            ),
        })

    lemma_map = {
        lemma.lemma_id: lemma
        for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
    } if lemma_ids else {}
    knowledge_map = _build_knowledge_map(db, lemma_ids=lemma_ids or None)

    corrected = 0
    nulled = 0
    verified_units = 0
    for start in range(0, len(units), batch_size):
        batch = units[start:start + batch_size]
        verdicts = batch_verify_sentences(
            batch,
            lemma_map,
            return_invalid_rows=True,
        )
        if verdicts is None:
            raise RuntimeError(
                "Processed-book mapping verification unavailable; import remains hidden"
            )
        if len(verdicts) != len(batch):
            raise RuntimeError(
                "Processed-book mapping verification returned incomplete verdicts"
            )

        for unit, verdict in zip(batch, verdicts, strict=True):
            invalid_reason = verdict.get("invalid_reason")
            if invalid_reason:
                raise RuntimeError(
                    "Processed-book mapping verification returned an invalid row: "
                    f"{invalid_reason}"
                )

            mappings = unit["mappings"]
            mapping_by_position = {
                mapping.position: mapping for mapping in mappings
            }
            proposed_positions: set[int] = set()
            for choice in verdict.get("disambiguation", []):
                mapping = mapping_by_position[choice["position"]]
                if mapping.lemma_id != choice["lemma_id"]:
                    proposed_positions.add(mapping.position)

            issues = verdict.get("issues", [])
            proposed_positions.update(
                issue["position"] for issue in issues
            )
            if proposed_positions and not apply_repairs:
                raise RuntimeError(
                    "Processed-book mapping verification requires explicit "
                    "curation at token positions: "
                    + ", ".join(str(position) for position in sorted(proposed_positions))
                )

            for choice in verdict.get("disambiguation", []):
                mapping = mapping_by_position[choice["position"]]
                mapping.lemma_id = choice["lemma_id"]

            failed_positions = set(apply_corrections(
                issues,
                mappings,
                db,
                lemma_lookup=gated_lookup,
                arabic_text=unit["arabic"],
                require_gated_lemmas=True,
            ))
            issue_by_position = {
                issue["position"]: issue for issue in issues
            }

            for mapping in mappings:
                story_word = story_word_by_position[mapping.position]
                if mapping.position in failed_positions:
                    story_word.lemma_id = None
                    story_word.gloss_en = (
                        issue_by_position[mapping.position].get("correct_gloss")
                        or None
                    )
                    story_word.is_known_at_creation = False
                    nulled += 1
                    continue
                if story_word.lemma_id == mapping.lemma_id:
                    continue

                story_word.lemma_id = mapping.lemma_id
                destination = lemma_map.get(mapping.lemma_id)
                if destination is None and mapping.lemma_id is not None:
                    destination = db.get(Lemma, mapping.lemma_id)
                    if destination is not None:
                        lemma_map[destination.lemma_id] = destination
                story_word.gloss_en = (
                    destination.gloss_en if destination is not None else None
                )
                story_word.is_known_at_creation = bool(
                    mapping.lemma_id
                    and knowledge_map.get(mapping.lemma_id) in ("learning", "known")
                )
                if destination is not None:
                    story_word.is_function_word = is_function_word_lemma(
                        destination.lemma_ar_bare,
                        destination.function_word_override,
                    )
                corrected += 1

            verified_units += 1

        db.commit()

    logger.info(
        "Processed book story %d mapping verification: %d units, %d corrected, %d nulled",
        story.id,
        verified_units,
        corrected,
        nulled,
    )
    return {
        "verified_units": verified_units,
        "corrected": corrected,
        "nulled": nulled,
    }
