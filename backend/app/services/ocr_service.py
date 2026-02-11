"""OCR service using Gemini Vision API for Arabic text extraction.

Used for two features:
1. Textbook page scanning: extract words, import new lemmas, mark existing as seen
2. Story image import: extract full Arabic text from an image

Pipeline for textbook scanning (3-step):
  Step 1 — OCR only: extract Arabic words from image (Gemini Vision)
  Step 2 — Morphology: CAMeL Tools for root/base lemma
  Step 3 — Translation: LLM translates Arabic words to English (no image)
"""

import base64
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Lemma, Root, Sentence, UserLemmaKnowledge, PageUpload
from app.services.fsrs_service import create_new_card, submit_review
from app.services.interaction_logger import log_interaction
from app.services.sentence_validator import (
    build_lemma_lookup,
    compute_bare_form,
    normalize_alef,
    sanitize_arabic_word,
    strip_diacritics,
    strip_tatweel,
    _is_function_word,
    _lookup_lemma,
)

logger = logging.getLogger(__name__)

MIN_SENTENCES_PER_WORD = 3
MAX_GLOSS_LENGTH = 50

# Common patterns indicating a wiktionary-style definition rather than a concise gloss
_VERBOSE_PATTERNS = [
    "verbal noun of",
    "active participle of",
    "passive participle of",
    "feminine singular of",
    "feminine equivalent of",
    "plural of",
    "alternative form of",
    "alternative spelling of",
    "singulative of",
    "elative degree of",
    "Judeo-Arabic spelling of",
]


def validate_gloss(gloss: str | None) -> str | None:
    """Validate and clean an English gloss. Returns cleaned gloss or None if invalid."""
    if not gloss:
        return None
    gloss = gloss.strip()
    if not gloss:
        return None

    # Reject if it looks like a verbose dictionary definition
    lower = gloss.lower()
    for pattern in _VERBOSE_PATTERNS:
        if lower.startswith(pattern):
            return None

    # Truncate at first semicolon (often separates primary from secondary meanings)
    if ";" in gloss and len(gloss) > MAX_GLOSS_LENGTH:
        gloss = gloss.split(";")[0].strip()

    # If still too long, truncate at last comma before limit
    if len(gloss) > MAX_GLOSS_LENGTH:
        truncated = gloss[:MAX_GLOSS_LENGTH]
        last_comma = truncated.rfind(",")
        if last_comma > 10:
            gloss = truncated[:last_comma].strip()
        else:
            gloss = truncated.strip()

    return gloss or None


def _call_gemini_vision(image_bytes: bytes, prompt: str, system_prompt: str = "") -> dict:
    """Call Gemini Vision API with an image and prompt.

    Uses litellm for the API call with base64-encoded image.
    Returns parsed JSON response.
    """
    import litellm
    import time

    api_key = settings.gemini_key
    if not api_key:
        raise ValueError("GEMINI_KEY not configured")

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    messages.append({
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            },
            {"type": "text", "text": prompt},
        ],
    })

    start = time.time()
    try:
        response = litellm.completion(
            model="gemini/gemini-3-flash-preview",
            messages=messages,
            temperature=0.1,
            timeout=120,
            api_key=api_key,
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - start
        content = response.choices[0].message.content.strip()

        # Strip markdown fences if present
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        # Log the call
        log_dir = settings.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"llm_calls_{datetime.now():%Y-%m-%d}.jsonl"
        entry = {
            "ts": datetime.now().isoformat(),
            "event": "llm_call",
            "model": "gemini/gemini-3-flash-preview",
            "task": "ocr",
            "success": True,
            "response_time_s": round(elapsed, 2),
        }
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        return json.loads(content)

    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"Gemini Vision call failed after {elapsed:.1f}s: {e}")
        raise


def _call_llm_text(prompt: str, system_prompt: str = "") -> dict:
    """Call LLM with text-only prompt. Returns parsed JSON."""
    import litellm
    import time

    api_key = settings.gemini_key
    if not api_key:
        raise ValueError("GEMINI_KEY not configured")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    start = time.time()
    try:
        response = litellm.completion(
            model="gemini/gemini-3-flash-preview",
            messages=messages,
            temperature=0.1,
            timeout=60,
            api_key=api_key,
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - start
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        log_dir = settings.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"llm_calls_{datetime.now():%Y-%m-%d}.jsonl"
        entry = {
            "ts": datetime.now().isoformat(),
            "event": "llm_call",
            "model": "gemini/gemini-3-flash-preview",
            "task": "ocr_translate",
            "success": True,
            "response_time_s": round(elapsed, 2),
        }
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        return json.loads(content)
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"LLM text call failed after {elapsed:.1f}s: {e}")
        raise


def extract_text_from_image(image_bytes: bytes) -> str:
    """Extract Arabic text from an image using Gemini Vision.

    Returns the extracted Arabic text as a single string.
    Used for story import from images.
    """
    result = _call_gemini_vision(
        image_bytes,
        prompt=(
            "Extract ALL Arabic text from this image. "
            "Preserve the original text exactly as written, including diacritics if present. "
            "Preserve paragraph breaks with newlines. "
            "Do NOT translate. Do NOT add diacritics that aren't in the original. "
            "Do NOT include any non-Arabic text (page numbers, English, etc). "
            'Respond with JSON: {"arabic_text": "the extracted Arabic text"}'
        ),
        system_prompt=(
            "You are an Arabic OCR system. Extract Arabic text accurately from images. "
            "Respond with JSON only."
        ),
    )
    return result.get("arabic_text", "")


def _step1_extract_words(image_bytes: bytes) -> list[str]:
    """Step 1: OCR only — extract Arabic words from image.

    Simple prompt that only asks for Arabic words, no translation or analysis.
    """
    result = _call_gemini_vision(
        image_bytes,
        prompt=(
            "This is a page from an Arabic language textbook. "
            "Extract ALL Arabic vocabulary words visible on this page. "
            "Return ONLY individual Arabic words as a list. "
            "Include words from vocabulary lists, example sentences, and exercises. "
            "Skip proper nouns, names, and page numbers. "
            "Do NOT include punctuation marks with the words (no periods, question marks, commas). "
            "Do NOT include multi-word phrases — extract each word separately. "
            "Do NOT include slash-separated alternatives — pick the first word only. "
            "Include diacritics if they are visible on the word.\n\n"
            'Respond with JSON: {"words": ["word1", "word2", ...]}'
        ),
        system_prompt=(
            "You are an Arabic OCR system. Extract Arabic words from textbook pages. "
            "Return only Arabic words, nothing else. Respond with JSON only."
        ),
    )
    words = result.get("words", [])
    if not isinstance(words, list):
        return []
    from app.services.sentence_validator import sanitize_arabic_word

    raw = [w.strip() for w in words if isinstance(w, str) and w.strip()]
    cleaned = []
    for w in raw:
        sanitized, warnings = sanitize_arabic_word(w)
        if sanitized and "multi_word" not in warnings:
            cleaned.append(sanitized)
    return cleaned


def _step2_morphology(words: list[str]) -> list[dict]:
    """Step 2: Use CAMeL Tools for morphological analysis of each word.

    Returns list of dicts with: arabic, bare, root, base_lemma, pos.
    Falls back to basic normalization if CAMeL Tools unavailable.
    """
    from app.services.morphology import (
        CAMEL_AVAILABLE,
        analyze_word_camel,
        get_base_lemma,
    )

    results = []
    for word in words:
        bare = normalize_alef(strip_tatweel(strip_diacritics(word)))

        if _is_function_word(bare):
            continue

        entry = {
            "arabic": word,
            "bare": bare,
            "root": None,
            "base_lemma": bare,
            "pos": None,
        }

        if CAMEL_AVAILABLE:
            analyses = analyze_word_camel(word)
            if analyses:
                top = analyses[0]
                entry["root"] = top.get("root")
                entry["pos"] = top.get("pos")
                lex = top.get("lex")
                if lex:
                    entry["base_lemma"] = normalize_alef(strip_diacritics(lex))

        results.append(entry)
    return results


def _step3_translate(word_entries: list[dict]) -> list[dict]:
    """Step 3: Use LLM to translate Arabic words to English.

    Sends a text-only prompt (no image) with the Arabic words for clean context.
    Returns the word entries with english and pos fields populated.
    """
    if not word_entries:
        return word_entries

    # Batch in groups of 30
    batch_size = 30
    all_results = []

    for i in range(0, len(word_entries), batch_size):
        batch = word_entries[i:i + batch_size]
        word_list = [
            {"arabic": e["arabic"], "bare": e["bare"], "pos": e.get("pos")}
            for e in batch
        ]

        prompt = (
            "Translate these Arabic words to English. For each word, provide:\n"
            "- english: a concise English gloss (1-3 words, e.g. 'book', 'to write', 'beautiful')\n"
            "- pos: part of speech (noun/verb/adj/adv/prep/particle)\n\n"
            "Words:\n"
            + json.dumps(word_list, ensure_ascii=False)
            + "\n\n"
            'Respond with JSON: {"translations": [{"bare": "...", "english": "...", "pos": "..."}]}'
        )

        try:
            result = _call_llm_text(
                prompt,
                system_prompt=(
                    "You are an Arabic-English translator. "
                    "Provide concise, accurate English glosses for Arabic vocabulary words. "
                    "Respond with JSON only."
                ),
            )
            translations = result.get("translations", [])
            if isinstance(translations, list):
                trans_by_bare = {
                    t.get("bare", ""): t
                    for t in translations
                    if isinstance(t, dict)
                }
                for entry in batch:
                    t = trans_by_bare.get(entry["bare"], {})
                    raw_gloss = t.get("english")
                    entry["english"] = validate_gloss(raw_gloss) or raw_gloss
                    if t.get("pos") and not entry.get("pos"):
                        entry["pos"] = t["pos"]
            all_results.extend(batch)
        except Exception:
            logger.warning("Translation step failed for batch, using entries without English")
            all_results.extend(batch)

    return all_results


def extract_words_from_image(image_bytes: bytes) -> list[dict]:
    """Extract individual Arabic words/vocabulary from a textbook page image.

    Uses the 3-step pipeline:
    1. OCR only (Gemini Vision) — extract Arabic words
    2. Morphology (CAMeL Tools) — root, base lemma, POS
    3. Translation (LLM text) — English glosses

    Returns list of dicts with: arabic, bare (as arabic_bare), english, pos, root.
    """
    # Step 1: OCR
    raw_words = _step1_extract_words(image_bytes)
    if not raw_words:
        return []

    # Step 2: Morphology
    analyzed = _step2_morphology(raw_words)

    # Step 3: Translation
    translated = _step3_translate(analyzed)

    # Normalize output format to match what process_textbook_page expects
    results = []
    seen_bares: set[str] = set()
    for entry in translated:
        bare = entry.get("bare", "")
        if not bare or bare in seen_bares:
            continue
        seen_bares.add(bare)

        root_str = entry.get("root")
        if root_str:
            root_str = ".".join(root_str) if "." not in root_str and len(root_str) <= 4 else root_str

        results.append({
            "arabic": entry.get("arabic", ""),
            "arabic_bare": bare,
            "english": entry.get("english"),
            "pos": entry.get("pos"),
            "root": root_str,
        })

    return results


def process_textbook_page(
    db: Session,
    upload: PageUpload,
    image_bytes: bytes,
) -> None:
    """Process a single textbook page image: OCR, match words, import new ones.

    This runs as a background task. Updates the PageUpload record with results.
    Triggers sentence generation for newly imported words.
    """
    try:
        upload.status = "processing"
        db.commit()

        # Extract words from image (3-step pipeline)
        extracted = extract_words_from_image(image_bytes)
        if not extracted:
            upload.status = "completed"
            upload.extracted_words_json = []
            upload.new_words = 0
            upload.existing_words = 0
            upload.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        # Build lookup for existing lemmas
        all_lemmas = db.query(Lemma).all()
        lemma_lookup = build_lemma_lookup(all_lemmas)

        # Also build a bare→lemma map for quick dedup
        bare_to_lemma: dict[str, Lemma] = {}
        for lemma in all_lemmas:
            bare_to_lemma[lemma.lemma_ar_bare] = lemma

        knowledge_map: dict[int, UserLemmaKnowledge] = {}
        for ulk in db.query(UserLemmaKnowledge).all():
            knowledge_map[ulk.lemma_id] = ulk

        results = []
        new_count = 0
        existing_count = 0
        new_lemma_ids: list[int] = []

        seen_bares: set[str] = set()  # dedup within this page

        for word_data in extracted:
            arabic = word_data.get("arabic", "").strip()
            if not arabic:
                continue

            # Sanitize: strip punctuation, reject multi-word
            arabic, san_warnings = sanitize_arabic_word(arabic)
            if not arabic or "multi_word" in san_warnings:
                continue

            # Compute bare form
            bare = compute_bare_form(arabic)

            # Skip function words
            if _is_function_word(bare):
                continue

            # Skip if already processed in this batch
            if bare in seen_bares:
                continue
            seen_bares.add(bare)

            # Try to find existing lemma
            lemma_id = _lookup_lemma(bare, lemma_lookup)

            if lemma_id:
                # Existing word — increment encounter count
                lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
                ulk = knowledge_map.get(lemma_id)

                if ulk:
                    ulk.total_encounters = (ulk.total_encounters or 0) + 1
                    submit_review(
                        db,
                        lemma_id=lemma_id,
                        rating_int=3,
                        review_mode="textbook_scan",
                    )
                    ulk = knowledge_map.get(lemma_id)
                    existing_count += 1
                    results.append({
                        "arabic": lemma.lemma_ar if lemma else arabic,
                        "arabic_bare": bare,
                        "english": lemma.gloss_en if lemma else word_data.get("english"),
                        "status": "existing",
                        "lemma_id": lemma_id,
                        "knowledge_state": ulk.knowledge_state if ulk else "learning",
                    })
                else:
                    # Lemma exists but no knowledge record — create one
                    new_ulk = UserLemmaKnowledge(
                        lemma_id=lemma_id,
                        knowledge_state="learning",
                        fsrs_card_json=create_new_card(),
                        introduced_at=datetime.now(timezone.utc),
                        source="textbook_scan",
                        total_encounters=1,
                    )
                    db.add(new_ulk)
                    db.flush()
                    knowledge_map[lemma_id] = new_ulk
                    submit_review(
                        db,
                        lemma_id=lemma_id,
                        rating_int=3,
                        review_mode="textbook_scan",
                    )
                    new_ulk = knowledge_map.get(lemma_id)
                    existing_count += 1
                    results.append({
                        "arabic": lemma.lemma_ar if lemma else arabic,
                        "arabic_bare": bare,
                        "english": lemma.gloss_en if lemma else word_data.get("english"),
                        "status": "existing_new_card",
                        "lemma_id": lemma_id,
                        "knowledge_state": new_ulk.knowledge_state if new_ulk else "learning",
                    })
            else:
                # New word — create lemma + knowledge record
                english = word_data.get("english")
                pos = word_data.get("pos")
                root_str = word_data.get("root")

                # Find or create root
                root_id = None
                if root_str:
                    existing_root = db.query(Root).filter(Root.root == root_str).first()
                    if existing_root:
                        root_id = existing_root.root_id
                    else:
                        new_root = Root(root=root_str, core_meaning_en=english or "")
                        db.add(new_root)
                        db.flush()
                        root_id = new_root.root_id

                new_lemma = Lemma(
                    lemma_ar=arabic,
                    lemma_ar_bare=bare,
                    root_id=root_id,
                    pos=pos,
                    gloss_en=english,
                    source="textbook_scan",
                )
                db.add(new_lemma)
                db.flush()

                new_ulk = UserLemmaKnowledge(
                    lemma_id=new_lemma.lemma_id,
                    knowledge_state="learning",
                    fsrs_card_json=create_new_card(),
                    introduced_at=datetime.now(timezone.utc),
                    source="textbook_scan",
                    total_encounters=1,
                )
                db.add(new_ulk)
                db.flush()
                submit_review(
                    db,
                    lemma_id=new_lemma.lemma_id,
                    rating_int=3,
                    review_mode="textbook_scan",
                )

                # Update lookup for subsequent words in same batch
                lemma_lookup[bare] = new_lemma.lemma_id
                if bare.startswith("ال") and len(bare) > 2:
                    lemma_lookup[bare[2:]] = new_lemma.lemma_id
                else:
                    lemma_lookup["ال" + bare] = new_lemma.lemma_id
                knowledge_map[new_lemma.lemma_id] = new_ulk
                bare_to_lemma[bare] = new_lemma

                new_count += 1
                new_lemma_ids.append(new_lemma.lemma_id)
                results.append({
                    "arabic": arabic,
                    "arabic_bare": bare,
                    "english": english,
                    "status": "new",
                    "lemma_id": new_lemma.lemma_id,
                    "knowledge_state": "learning",
                    "root": root_str,
                    "pos": pos,
                })

        # Update the upload record
        upload.status = "completed"
        upload.extracted_words_json = results
        upload.new_words = new_count
        upload.existing_words = existing_count
        upload.completed_at = datetime.now(timezone.utc)
        db.commit()

        log_interaction(
            event="textbook_page_processed",
            upload_id=upload.id,
            new_words=new_count,
            existing_words=existing_count,
            total_extracted=len(extracted),
        )

        # Trigger sentence generation for new words
        _schedule_material_generation(db, new_lemma_ids)

    except Exception as e:
        logger.exception(f"Failed to process textbook page upload {upload.id}: {e}")
        upload.status = "failed"
        upload.error_message = str(e)[:500]
        db.commit()


def _schedule_material_generation(db: Session, lemma_ids: list[int]) -> None:
    """Schedule sentence + audio generation for newly imported words."""
    from sqlalchemy import func

    from app.services.material_generator import generate_material_for_word

    for lemma_id in lemma_ids:
        existing_count = (
            db.query(func.count(Sentence.id))
            .filter(Sentence.target_lemma_id == lemma_id)
            .scalar() or 0
        )
        if existing_count < MIN_SENTENCES_PER_WORD:
            needed = MIN_SENTENCES_PER_WORD - existing_count
            try:
                generate_material_for_word(lemma_id, needed)
            except Exception:
                logger.warning(f"Material generation failed for OCR word {lemma_id}")
