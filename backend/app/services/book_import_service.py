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

from app.models import Lemma, Sentence, SentenceWord, Story, UserLemmaKnowledge
from app.services.interaction_logger import log_interaction
from app.services.llm import AllProvidersFailed, generate_completion
from app.services.morphology import get_word_features
from app.services.ocr_service import _call_gemini_vision, extract_text_from_image
from app.services.sentence_validator import (
    build_lemma_lookup,
    map_tokens_to_lemmas,
    normalize_alef,
    strip_diacritics,
    tokenize_display,
)
from app.services.story_service import (
    _create_story_words,
    _import_unknown_words,
    _recalculate_story_counts,
    _build_knowledge_map,
    _get_all_lemmas,
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
                model_override="gemini",
                temperature=0.2,
                timeout=120,
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
            model_override="gemini",
            temperature=0.2,
            timeout=120,
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
        features = get_word_features(m.surface_form)
        lex = features.get("lex", m.surface_form)
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
                if m.lemma_id is None:
                    bare = normalize_alef(strip_diacritics(m.surface_form))
                    if bare in story_word_lookup:
                        m.lemma_id = story_word_lookup[bare]

        still_unmapped = [m.surface_form for m in mappings if m.lemma_id is None]
        if still_unmapped:
            logger.info(f"Book sentence has {len(still_unmapped)} unmapped words (kept): {still_unmapped[:5]}")

        target_lid = _pick_primary_target(mappings, db)

        sent = Sentence(
            arabic_text=strip_diacritics(arabic),
            arabic_diacritized=arabic,
            english_translation=english,
            transliteration=transliteration,
            source="book",
            target_lemma_id=target_lid,
            story_id=story.id,
            is_active=True,
            created_at=datetime.now(timezone.utc),
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

    # Import unknown words (creates Lemma entries, no ULK)
    new_ids = _import_unknown_words(db, story, lemma_lookup)

    # Create encountered ULK records for book words that don't have one yet
    book_lemma_ids = {sw.lemma_id for sw in story.words if sw.lemma_id and not sw.is_function_word}
    existing_ulk_ids = set()
    if book_lemma_ids:
        existing_ulk_ids = {
            r[0] for r in db.query(UserLemmaKnowledge.lemma_id)
            .filter(UserLemmaKnowledge.lemma_id.in_(book_lemma_ids))
            .all()
        }
    encountered_count = 0
    for lid in book_lemma_ids - existing_ulk_ids:
        db.add(UserLemmaKnowledge(
            lemma_id=lid,
            knowledge_state="encountered",
            source="book",
            total_encounters=1,
        ))
        encountered_count += 1
    if encountered_count:
        db.flush()
        logger.info(f"Created {encountered_count} encountered ULK records (source=book)")

    # Update Lemma.source and source_story_id for pre-existing lemmas now in the book.
    # Book wins over lower-priority sources (wiktionary, avp_a1, story_import, etc.)
    _BOOK_OVERRIDES = {None, "wiktionary", "avp_a1", "story_import", "auto_intro"}
    source_updated = 0
    if book_lemma_ids:
        for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(book_lemma_ids)).all():
            changed = False
            if lemma.source in _BOOK_OVERRIDES:
                lemma.source = "book"
                changed = True
            if not lemma.source_story_id:
                lemma.source_story_id = story.id
                changed = True
            if changed:
                source_updated += 1
    if source_updated:
        db.flush()
        logger.info(f"Updated {source_updated} lemmas: source→book, source_story_id→{story.id}")

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
