"""OCR service using Gemini Vision API for Arabic text extraction.

Used for two features:
1. Textbook page scanning: extract words, import new lemmas, mark existing as seen
2. Story image import: extract full Arabic text from an image
"""

import base64
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Lemma, Root, UserLemmaKnowledge, PageUpload
from app.services.fsrs_service import create_new_card
from app.services.interaction_logger import log_interaction
from app.services.sentence_validator import (
    build_lemma_lookup,
    normalize_alef,
    strip_diacritics,
    strip_tatweel,
    _is_function_word,
    _lookup_lemma,
)

logger = logging.getLogger(__name__)


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


def extract_words_from_image(image_bytes: bytes) -> list[dict]:
    """Extract individual Arabic words/vocabulary from a textbook page image.

    Returns a list of word entries with arabic, english (if visible), and pos.
    Uses Gemini to understand textbook layout and extract vocabulary items.
    """
    result = _call_gemini_vision(
        image_bytes,
        prompt=(
            "This is a page from an Arabic language textbook. "
            "Extract ALL Arabic vocabulary words you can see on this page. "
            "For each word, extract:\n"
            "- arabic: the Arabic word (with diacritics if shown)\n"
            "- arabic_bare: the word without diacritics\n"
            "- english: the English translation if shown next to it, or null\n"
            "- pos: part of speech if identifiable (noun/verb/adj/adv/prep/particle), or null\n"
            "- root: the Arabic root if identifiable (in dotted form like ك.ت.ب), or null\n\n"
            "Include ALL content words you see — vocabulary lists, words in example sentences, "
            "words in exercises. Skip function words (في، من، على، إلى، و، ب، ل، هذا، هذه، etc). "
            "Skip proper nouns and names.\n\n"
            'Respond with JSON: {"words": [{"arabic": "...", "arabic_bare": "...", "english": "...", "pos": "...", "root": "..."}]}'
        ),
        system_prompt=(
            "You are an Arabic textbook vocabulary extractor. "
            "Accurately identify and extract vocabulary from textbook pages. "
            "Be thorough — extract every content word visible on the page. "
            "Respond with JSON only."
        ),
    )
    words = result.get("words", [])
    if not isinstance(words, list):
        return []
    return [w for w in words if isinstance(w, dict) and w.get("arabic")]


def process_textbook_page(
    db: Session,
    upload: PageUpload,
    image_bytes: bytes,
) -> None:
    """Process a single textbook page image: OCR, match words, import new ones.

    This runs as a background task. Updates the PageUpload record with results.
    """
    try:
        upload.status = "processing"
        db.commit()

        # Extract words from image
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

        seen_bares: set[str] = set()  # dedup within this page

        for word_data in extracted:
            arabic = word_data.get("arabic", "").strip()
            if not arabic:
                continue

            # Compute bare form
            bare = word_data.get("arabic_bare", "").strip()
            if not bare:
                bare = strip_diacritics(arabic)
            bare = strip_tatweel(bare)
            bare = normalize_alef(bare)

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
                    existing_count += 1
                    results.append({
                        "arabic": lemma.lemma_ar if lemma else arabic,
                        "arabic_bare": bare,
                        "english": lemma.gloss_en if lemma else word_data.get("english"),
                        "status": "existing",
                        "lemma_id": lemma_id,
                        "knowledge_state": ulk.knowledge_state,
                    })
                else:
                    # Lemma exists but no knowledge record — create one as "encountered"
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
                    existing_count += 1
                    results.append({
                        "arabic": lemma.lemma_ar if lemma else arabic,
                        "arabic_bare": bare,
                        "english": lemma.gloss_en if lemma else word_data.get("english"),
                        "status": "existing_new_card",
                        "lemma_id": lemma_id,
                        "knowledge_state": "learning",
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

                # Update lookup for subsequent words in same batch
                lemma_lookup[bare] = new_lemma.lemma_id
                if bare.startswith("ال") and len(bare) > 2:
                    lemma_lookup[bare[2:]] = new_lemma.lemma_id
                else:
                    lemma_lookup["ال" + bare] = new_lemma.lemma_id
                knowledge_map[new_lemma.lemma_id] = new_ulk
                bare_to_lemma[bare] = new_lemma

                new_count += 1
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

    except Exception as e:
        logger.exception(f"Failed to process textbook page upload {upload.id}: {e}")
        upload.status = "failed"
        upload.error_message = str(e)[:500]
        db.commit()
