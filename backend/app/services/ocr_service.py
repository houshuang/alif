"""OCR service using Gemini Vision API for Arabic text extraction.

Used for two features:
1. Textbook page scanning: extract words, import new lemmas, mark existing as seen
2. Story image import: extract full Arabic text from an image

Pipeline for textbook scanning (3-step):
  Step 1 — OCR only: extract Arabic words from image (Gemini Vision, only paid API use)
  Step 2 — Morphology: CAMeL Tools for root/base lemma
  Step 3 — Translation: Claude Haiku via CLI (free) translates Arabic words to English
"""

import base64
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Lemma, Root, Sentence, UserLemmaKnowledge, PageUpload

from app.services.interaction_logger import log_interaction
from app.services.sentence_eligibility import reviewable_sentence_clauses
from app.services.sentence_validator import (
    build_lemma_lookup,
    compute_bare_form,
    normalize_alef,
    sanitize_arabic_word,
    strip_diacritics,
    strip_tatweel,
    _is_function_word,
    lookup_lemma,
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


def _record_textbook_encounter(db: Session, lemma_id: int) -> UserLemmaKnowledge:
    """Record textbook provenance without treating the scan as proof of knowledge.

    Textbook scans are vocabulary/source intake. They should create encountered
    rows for unknown words, not known FSRS cards or card-only intro exceptions.
    Promotion to acquiring happens later through start_acquisition(), where the
    normal daily/recovery new-word budget applies.
    """
    from app.services.canonical_resolution import resolve_canonical_lemma_id

    lemma_id = resolve_canonical_lemma_id(db, lemma_id)
    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )

    if not ulk:
        ulk = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state="encountered",
            fsrs_card_json=None,
            times_seen=0,
            times_correct=0,
            total_encounters=1,
            source="textbook_scan",
        )
        db.add(ulk)
        db.flush()
        return ulk

    _OVERRIDABLE_SOURCES = {
        None,
        "study",
        "encountered",
        "auto_intro",
        "collateral",
        "leech_reintro",
        "wiktionary",
    }
    if ulk.knowledge_state == "encountered" or ulk.source in _OVERRIDABLE_SOURCES:
        ulk.source = "textbook_scan"

    # An explicit suspension should continue to win over passive import.
    if ulk.knowledge_state == "suspended":
        return ulk

    ulk.total_encounters = (ulk.total_encounters or 0) + 1
    db.flush()
    return ulk


from app.services.morphology import is_valid_root as _is_valid_root, backfill_root_meanings


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


def _call_gemini_vision(
    image_bytes: bytes,
    prompt: str,
    system_prompt: str = "",
    model_override: str | None = None,
    timeout_seconds: int = 300,
) -> dict:
    """Call Gemini Vision API with an image and prompt.

    Uses litellm for the API call with base64-encoded image.
    Returns parsed JSON response.
    """
    import litellm
    import time

    api_key = settings.gemini_key
    if not api_key:
        raise ValueError("GEMINI_KEY not configured")

    model = model_override or "gemini/gemini-3-flash-preview"
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
            model=model,
            messages=messages,
            temperature=0.1,
            timeout=timeout_seconds,
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
            "model": model,
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
    """Call LLM with text-only prompt. Returns parsed JSON.

    Routes through generate_completion (Claude CLI, free) instead of Gemini API.
    """
    from app.services.llm import generate_completion

    return generate_completion(
        prompt=prompt,
        system_prompt=system_prompt,
        json_mode=True,
        temperature=0.1,
        timeout=60,
        model_override="claude_haiku",
        task_type="ocr_translate",
    )


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
    if not isinstance(result, dict):
        return ""
    return result.get("arabic_text", "")


def extract_text_and_translation(image_bytes: bytes) -> dict:
    """OCR an Arabic page AND translate it to English in a single Gemini Vision call.

    Synchronous (~3-5s) — powers the interactive snap-to-read feature, where the
    reader photographs a page and immediately wants both the Arabic text and a
    faithful English rendering. One call (vs. OCR-then-separate-translate) keeps
    latency low enough for in-the-moment reading help.

    Returns {"arabic_text": str, "translation_en": str}.
    """
    prompt = (
            "This image is a page of Arabic text that a reader is studying.\n"
            "1. Extract ALL the Arabic text exactly as written: preserve any diacritics "
            "that are present, preserve paragraph breaks as newlines, do NOT add "
            "diacritics that aren't there, and exclude page numbers / non-Arabic "
            "marginalia.\n"
            "2. Provide a faithful, fluent English translation of that text — accurate "
            "to the meaning and natural in English (not a word-for-word gloss).\n"
            'Respond with JSON: {"arabic_text": "the Arabic", "translation_en": "the English"}'
        )
    system_prompt = (
            "You are an expert Arabic reader and translator. Extract the Arabic text "
            "accurately and translate it faithfully. Respond with JSON only."
        )

    # Interactive snaps used to inherit the general OCR call's five-minute timeout
    # while the app abandoned the request after one minute. Bound each attempt and
    # retry once: transient provider/JSON failures are common enough that a single
    # attempt made the feature feel random, but the total still stays within the
    # client's request window.
    first_error: Exception | None = None
    try:
        result = _call_gemini_vision(
            image_bytes, prompt=prompt, system_prompt=system_prompt,
            timeout_seconds=90,
        )
    except Exception as e:
        first_error = e
        result = {}

    if not isinstance(result, dict) or not (result.get("arabic_text") or "").strip():
        if first_error:
            logger.warning("snap vision attempt failed; retrying once: %s", first_error)
        else:
            logger.warning("snap vision returned no Arabic; retrying once")
        result = _call_gemini_vision(
            image_bytes, prompt=prompt, system_prompt=system_prompt,
            timeout_seconds=90,
        )

    if not isinstance(result, dict):
        return {"arabic_text": "", "translation_en": ""}
    arabic_text = result.get("arabic_text") or ""
    translation = result.get("translation_en") or ""

    # A valid OCR result with a missing translation is salvageable without asking
    # the user to photograph the page again.
    if arabic_text.strip() and not translation.strip():
        try:
            translated = _call_llm_text(
                "Translate this Arabic text faithfully and fluently into English. "
                "Return JSON only.\n\n"
                f"Arabic:\n{arabic_text}\n\n"
                'Schema: {"translation_en": "the English translation"}',
                system_prompt="You are an expert Arabic-English translator.",
            )
            translation = translated.get("translation_en") or ""
        except Exception as e:
            logger.warning("snap translation fallback failed: %s", e)

    return {"arabic_text": arabic_text, "translation_en": translation}


def _step1_extract_words(image_bytes: bytes) -> list[str]:
    """Step 1: OCR only — extract Arabic words from image.

    Simple prompt that only asks for Arabic words, no translation or analysis.
    Also extracts the printed page number if visible.

    Returns (words, page_number) tuple where page_number may be None.
    """
    result = _call_gemini_vision(
        image_bytes,
        prompt=(
            "This is a page from an Arabic language textbook. "
            "Extract Arabic vocabulary words visible on this page.\n\n"
            "IMPORTANT — return DICTIONARY BASE FORMS, not conjugated or inflected forms:\n"
            "- Remove possessive suffixes: كتابك → كتاب, بيتي → بيت, سيارتها → سيارة\n"
            "- Remove verb conjugation: يكتبون → كتب, تذهبين → ذهب, يدرسون → درس\n"
            "- Remove dual/plural suffixes: معلمون → معلم, طالبات → طالبة\n"
            "- Keep the definite article ال if the word is listed that way\n\n"
            "Other rules:\n"
            "- Return ONLY individual Arabic words as a list\n"
            "- Skip proper nouns and names\n"
            "- Do NOT include punctuation marks with the words\n"
            "- Do NOT include multi-word phrases — extract each word separately\n"
            "- Do NOT include slash-separated alternatives — pick the first word only\n"
            "- Include diacritics if they are visible on the word\n\n"
            "Also identify the printed page number if visible on the page "
            "(usually at the top or bottom corner). Use null if no page number is visible.\n\n"
            'Respond with JSON: {"words": ["word1", "word2", ...], "page_number": 5}'
        ),
        system_prompt=(
            "You are an Arabic OCR system specialized in textbook vocabulary extraction. "
            "Always return dictionary base forms, not inflected/conjugated forms. "
            "Respond with JSON only."
        ),
    )
    if isinstance(result, list):
        # Two observed shapes:
        # 1. bare list of word strings (model ignored the JSON envelope)
        # 2. list of per-page dicts when a camera image shows a textbook spread:
        #    [{"words": [...], "page_number": 182}, {"words": [...], "page_number": 183}]
        # Flatten both into a single (words, page_number) — preserve the first
        # non-null page_number. All words attribute to the first PageUpload row;
        # imperfect for spreads but vastly better than dropping them.
        words = []
        page_number = None
        for item in result:
            if isinstance(item, dict):
                page_words = item.get("words")
                if isinstance(page_words, list):
                    words.extend(page_words)
                if page_number is None:
                    page_number = item.get("page_number")
            elif isinstance(item, str):
                words.append(item)
    elif isinstance(result, dict):
        words = result.get("words", [])
        page_number = result.get("page_number")
    else:
        words = []
        page_number = None
    if isinstance(page_number, str):
        try:
            page_number = int(page_number)
        except (ValueError, TypeError):
            page_number = None
    if not isinstance(page_number, int):
        page_number = None

    if not isinstance(words, list):
        return [], page_number
    from app.services.sentence_validator import sanitize_arabic_word

    raw = [w.strip() for w in words if isinstance(w, str) and w.strip()]
    cleaned = []
    for w in raw:
        sanitized, warnings = sanitize_arabic_word(w)
        if (
            sanitized
            and "multi_word" not in warnings
            and "too_short" not in warnings
            and "no_letters" not in warnings
        ):
            cleaned.append(sanitized)
    return cleaned, page_number


def _step2_morphology(words: list[str]) -> list[dict]:
    """Step 2: Use CAMeL Tools for morphological analysis of each word.

    Returns list of dicts with: arabic, bare, root, base_lemma,
    base_lemma_vocalized, pos.
    `base_lemma_vocalized` is the CAMeL `lex` with diacritics — used by
    process_textbook_page as the canonical dictionary form for lemma_ar
    so the al- prefix and other clitics don't leak into the stored
    headword (e.g. surface "الْمَاشِي" → base_lemma_vocalized "ماشِي").
    Falls back to basic normalization if CAMeL Tools unavailable.
    """
    from app.services.morphology import (
        CAMEL_AVAILABLE,
        analyze_word_camel,
        get_best_lemma_mle,
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
            "base_lemma_vocalized": None,
            "pos": None,
        }

        if CAMEL_AVAILABLE:
            mle_result = get_best_lemma_mle(word)
            if mle_result:
                entry["root"] = mle_result.get("root")
                entry["pos"] = mle_result.get("pos")
                lex = mle_result.get("lex")
                if lex:
                    entry["base_lemma"] = normalize_alef(strip_diacritics(lex))
                    entry["base_lemma_vocalized"] = lex
            else:
                analyses = analyze_word_camel(word)
                if analyses:
                    top = analyses[0]
                    entry["root"] = top.get("root")
                    entry["pos"] = top.get("pos")
                    lex = top.get("lex")
                    if lex:
                        entry["base_lemma"] = normalize_alef(strip_diacritics(lex))
                        entry["base_lemma_vocalized"] = lex

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
            {"arabic": e["arabic"], "bare": e.get("base_lemma") or e["bare"], "pos": e.get("pos")}
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
                    lookup_key = entry.get("base_lemma") or entry["bare"]
                    t = trans_by_bare.get(lookup_key, {})
                    if not t:
                        t = trans_by_bare.get(entry["bare"], {})
                    raw_gloss = t.get("english")
                    entry["english"] = validate_gloss(raw_gloss) or raw_gloss
                    if t.get("pos"):
                        entry["pos"] = t["pos"]
            all_results.extend(batch)
        except Exception:
            logger.exception("Translation step failed for batch, using entries without English")
            all_results.extend(batch)

    return all_results


def _build_import_category_maps(
    useful: list[dict],
) -> tuple[dict[str, str], dict[str, str]]:
    """Return category and cleaned-form lookups from import-quality output."""
    category_by_bare: dict[str, str] = {}
    cleaned_by_bare: dict[str, str] = {}
    for u in useful:
        bare = u.get("arabic")
        if not bare:
            continue
        cat = u.get("word_category", "standard")
        category_by_bare[bare] = cat
        if u.get("cleaned_arabic"):
            cleaned = u["cleaned_arabic"]
            cleaned_by_bare[bare] = cleaned
            category_by_bare[cleaned] = cat
    return category_by_bare, cleaned_by_bare


def _normalize_import_pos(pos: str | None, word_category: str | None) -> str | None:
    """Do not let ambiguous CAMeL noun_prop force normal vocabulary inert."""
    if pos == "noun_prop" and word_category != "proper_name":
        return "noun"
    return pos


def _storage_word_category(category_by_bare: dict[str, str], *bares: str | None) -> str | None:
    for bare in bares:
        if not bare:
            continue
        cat = category_by_bare.get(bare)
        if cat in ("proper_name", "onomatopoeia"):
            return cat
    return None


def extract_words_from_image(image_bytes: bytes) -> tuple[list[dict], int | None]:
    """Extract individual Arabic words/vocabulary from a textbook page image.

    Uses the 3-step pipeline:
    1. OCR only (Gemini Vision) — extract Arabic words + page number
    2. Morphology (CAMeL Tools) — root, base lemma, POS
    3. Translation (LLM text) — English glosses

    Returns (words, page_number) tuple.
    words: list of dicts with: arabic, arabic_bare, english, pos, root, base_lemma.
    page_number: detected textbook page number, or None.
    """
    # Step 1: OCR
    raw_words, page_number = _step1_extract_words(image_bytes)
    if not raw_words:
        return [], page_number

    # Step 2: Morphology
    analyzed = _step2_morphology(raw_words)

    # Step 3: Translation
    translated = _step3_translate(analyzed)

    # Normalize output format to match what process_textbook_page expects
    # Dedup on base_lemma (not bare) so conjugated forms sharing a base are merged
    results = []
    seen_keys: set[str] = set()
    for entry in translated:
        bare = entry.get("bare", "")
        base_lemma = entry.get("base_lemma", bare)
        dedup_key = base_lemma or bare
        if not dedup_key or dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        root_str = entry.get("root")
        if root_str:
            root_str = ".".join(root_str) if "." not in root_str and len(root_str) <= 4 else root_str

        results.append({
            "arabic": entry.get("arabic", ""),
            "arabic_bare": bare,
            "english": entry.get("english"),
            "pos": entry.get("pos"),
            "root": root_str,
            "base_lemma": base_lemma if base_lemma != bare else None,
            "base_lemma_vocalized": entry.get("base_lemma_vocalized"),
        })

    return results, page_number


def process_textbook_page(
    db: Session,
    upload: PageUpload,
    image_bytes: bytes,
    preserve_known: bool = False,
) -> None:
    """Process a single textbook page image: OCR, match words, import new ones.

    This runs as a background task. Updates the PageUpload record with results.
    Triggers sentence generation for imported words. The preserve_known flag is
    kept for old callers, but textbook scans now always enter as encountered
    new-word candidates rather than known review cards.
    """
    _ = preserve_known
    try:
        upload.status = "processing"
        db.commit()

        # Extract words from image (3-step pipeline)
        extracted, page_number = extract_words_from_image(image_bytes)
        upload.textbook_page_number = page_number
        if not extracted:
            upload.status = "completed"
            upload.extracted_words_json = []
            upload.new_words = 0
            upload.existing_words = 0
            upload.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        # Quality gate: filter out junk + classify (names, sounds)
        from app.services.import_quality import classify_lemmas
        useful, rejected = classify_lemmas([
            {"arabic": w.get("arabic_bare", ""), "english": w.get("english", "")}
            for w in extracted
        ])
        _category_by_bare, _cleaned_by_bare = _build_import_category_maps(useful)
        if rejected:
            rejected_bares = {r["arabic"] for r in rejected}
            extracted = [w for w in extracted if w.get("arabic_bare", "") not in rejected_bares]

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
        textbook_lemma_ids: set[int] = set()

        seen_bares: set[str] = set()  # dedup within this page

        for word_data in extracted:
            arabic = word_data.get("arabic", "").strip()
            if not arabic:
                continue

            # Sanitize: strip punctuation, reject multi-word + letter-free tokens
            arabic, san_warnings = sanitize_arabic_word(arabic)
            if (
                not arabic
                or "multi_word" in san_warnings
                or "too_short" in san_warnings
                or "no_letters" in san_warnings
            ):
                continue

            # Compute bare form and get base_lemma from morphological analysis
            bare = compute_bare_form(arabic)
            base_lemma_bare = word_data.get("base_lemma")  # from Step 2 morphology

            # Skip function words (check both bare and base_lemma)
            if _is_function_word(bare):
                continue
            if base_lemma_bare and _is_function_word(base_lemma_bare):
                continue

            # Dedup: use base_lemma if available, fall back to bare
            dedup_key = base_lemma_bare or bare
            if dedup_key in seen_bares:
                continue
            seen_bares.add(dedup_key)
            if base_lemma_bare and bare != base_lemma_bare:
                seen_bares.add(bare)

            # Try to find existing lemma — try base_lemma first, then bare
            lemma_id = None
            if base_lemma_bare and base_lemma_bare != bare:
                lemma_id = lookup_lemma(base_lemma_bare, lemma_lookup)
            if not lemma_id:
                lemma_id = lookup_lemma(bare, lemma_lookup)

            if lemma_id:
                # If lookup landed on a variant, redirect to its canonical so
                # we never create or update a variant-scoped ULK row.
                from app.services.canonical_resolution import resolve_canonical_lemma_id
                lemma_id = resolve_canonical_lemma_id(db, lemma_id)

                # Existing word — increment encounter count
                lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
                ulk = knowledge_map.get(lemma_id)

                if ulk:
                    ulk = _record_textbook_encounter(db, lemma_id)
                    lemma_id = ulk.lemma_id
                    lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
                    knowledge_map[lemma_id] = ulk
                    textbook_lemma_ids.add(lemma_id)
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
                    # Lemma exists but no knowledge record
                    new_ulk = _record_textbook_encounter(db, lemma_id)
                    lemma_id = new_ulk.lemma_id
                    lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
                    textbook_lemma_ids.add(lemma_id)
                    knowledge_map[lemma_id] = new_ulk
                    existing_count += 1
                    results.append({
                        "arabic": lemma.lemma_ar if lemma else arabic,
                        "arabic_bare": bare,
                        "english": lemma.gloss_en if lemma else word_data.get("english"),
                        "status": "existing",
                        "lemma_id": lemma_id,
                        "knowledge_state": new_ulk.knowledge_state,
                    })
            else:
                # New word — create lemma + knowledge record
                # Use base_lemma for the canonical bare form if available
                import_bare = base_lemma_bare if base_lemma_bare else bare
                # Apply LLM-cleaned bare form if available (fixes ال-prefix, ه→ة)
                if import_bare in _cleaned_by_bare:
                    import_bare = _cleaned_by_bare[import_bare]
                english = (word_data.get("english") or "").strip()
                pos = word_data.get("pos")
                root_str = word_data.get("root")
                # Prefer CAMeL's vocalized lex for the headword when its
                # stripped bare matches our chosen import_bare. This avoids
                # storing al-prefixed surfaces like 'الْمَاشِي' as the
                # displayed lemma_ar when CAMeL clearly canonicalized to
                # 'ماشِي' (prc0='Al_det'). Falls back to the OCR surface if
                # CAMeL gave nothing or its stripped form would change the
                # bare key.
                import_lemma_ar = arabic
                base_voc = word_data.get("base_lemma_vocalized")
                if base_voc and normalize_alef(strip_diacritics(base_voc)) == normalize_alef(import_bare):
                    import_lemma_ar = base_voc

                # Never create a Lemma without an English gloss
                if not english:
                    logger.warning("Skipping lemma creation for %s: no English gloss", arabic)
                    results.append({
                        "arabic": arabic,
                        "arabic_bare": bare,
                        "english": "",
                        "status": "skipped_no_gloss",
                    })
                    continue

                # Find or create root (with validation)
                root_id = None
                if root_str and _is_valid_root(root_str):
                    existing_root = db.query(Root).filter(Root.root == root_str).first()
                    if existing_root:
                        root_id = existing_root.root_id
                    else:
                        new_root = Root(root=root_str, core_meaning_en="")
                        db.add(new_root)
                        db.flush()
                        root_id = new_root.root_id

                word_cat = _storage_word_category(_category_by_bare, import_bare, bare)
                pos = _normalize_import_pos(pos, word_cat)
                lemma_gloss = english
                if word_cat == "proper_name" and english and not english.startswith("(name)"):
                    lemma_gloss = f"(name) {english}"

                new_lemma = Lemma(
                    lemma_ar=import_lemma_ar,
                    lemma_ar_bare=import_bare,
                    root_id=root_id,
                    pos=pos,
                    gloss_en=lemma_gloss,
                    source="textbook_scan",
                    word_category=word_cat,
                )
                db.add(new_lemma)
                db.flush()

                new_ulk = _record_textbook_encounter(db, new_lemma.lemma_id)
                textbook_lemma_ids.add(new_ulk.lemma_id)

                # Update lookup for subsequent words in same batch
                lemma_lookup[import_bare] = new_lemma.lemma_id
                if import_bare != bare:
                    lemma_lookup[bare] = new_lemma.lemma_id
                if import_bare.startswith("ال") and len(import_bare) > 2:
                    lemma_lookup[import_bare[2:]] = new_lemma.lemma_id
                else:
                    lemma_lookup["ال" + import_bare] = new_lemma.lemma_id
                knowledge_map[new_lemma.lemma_id] = new_ulk
                bare_to_lemma[import_bare] = new_lemma

                new_count += 1
                new_lemma_ids.append(new_lemma.lemma_id)
                results.append({
                    "arabic": arabic,
                    "arabic_bare": import_bare,
                    "english": english,
                    "status": "new",
                    "lemma_id": new_lemma.lemma_id,
                    "knowledge_state": new_ulk.knowledge_state,
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

        # Run centralized quality gates (finalize + variants + enrich + stamp)
        variants_detected = 0
        variant_ids: set[int] = set()
        if new_lemma_ids:
            from app.services.lemma_quality import run_quality_gates
            gate_result = run_quality_gates(db, new_lemma_ids)
            variants_detected = gate_result.get("variants", 0)

            if variants_detected:
                variant_lemmas = db.query(Lemma).filter(
                    Lemma.lemma_id.in_(new_lemma_ids),
                    Lemma.canonical_lemma_id.isnot(None),
                ).all()
                variant_ids = {vl.lemma_id for vl in variant_lemmas}
                for vlem in variant_lemmas:
                    vulk = knowledge_map.get(vlem.lemma_id)
                    if vulk and vulk.knowledge_state not in ("suspended",):
                        vulk.knowledge_state = "encountered"
                        vulk.fsrs_card_json = None
                        vulk.last_reviewed = None
                        vulk.experiment_group = None
                        vulk.experiment_intro_shown_at = None
                        vulk.acquisition_box = None
                        vulk.acquisition_next_due = None
                        vulk.acquisition_started_at = None
                        vulk.entered_acquiring_at = None
                    if vlem.canonical_lemma_id:
                        canonical_ulk = _record_textbook_encounter(db, vlem.canonical_lemma_id)
                        textbook_lemma_ids.add(canonical_ulk.lemma_id)
                db.commit()

        log_interaction(
            event="textbook_page_processed",
            upload_id=upload.id,
            new_words=new_count,
            existing_words=existing_count,
            total_extracted=len(extracted),
            variants_detected=variants_detected,
        )

        # Commit before backfill_root_meanings which makes LLM calls
        db.commit()
        backfill_root_meanings(db)
        db.commit()

        # Generate material for textbook words so they can later enter the
        # normal new-word acquisition path with ready sentence practice.
        if not variant_ids and new_lemma_ids:
            variant_ids = {
                r[0] for r in db.query(Lemma.lemma_id)
                .filter(Lemma.lemma_id.in_(new_lemma_ids), Lemma.canonical_lemma_id.isnot(None))
                .all()
            }
        gen_ids = [lid for lid in set(new_lemma_ids) | textbook_lemma_ids if lid not in variant_ids]
        _schedule_material_generation(db, gen_ids)

    except Exception as e:
        logger.exception(f"Failed to process textbook page upload {upload.id}: {e}")
        upload.status = "failed"
        upload.error_message = str(e)[:500]
        db.commit()


def _schedule_material_generation(db: Session, lemma_ids: list[int]) -> None:
    """Generate sentences for words touched by a textbook scan.

    Uses batch generation (2 CLI calls for ~15 words) when ≥3 words need
    sentences. Falls back to single-word generation for small batches and
    for words that fail in the batch.
    """
    from sqlalchemy import func

    from app.services.material_generator import (
        batch_generate_material,
        BATCH_WORD_SIZE,
        generate_material_for_word,
    )

    # Filter to words that actually need sentences
    words_needing: list[int] = []
    for lemma_id in lemma_ids:
        existing_count = (
            db.query(func.count(Sentence.id))
            .filter(
                Sentence.target_lemma_id == lemma_id,
                reviewable_sentence_clauses(),
            )
            .scalar() or 0
        )
        if existing_count < MIN_SENTENCES_PER_WORD:
            words_needing.append(lemma_id)

    if not words_needing:
        logger.info("Post-scan generation: all %d words already have sentences", len(lemma_ids))
        return

    if len(words_needing) >= 3:
        # Batch path: 2 CLI calls per chunk of ~15 words
        total_generated = 0
        total_failed = 0
        for i in range(0, len(words_needing), BATCH_WORD_SIZE):
            chunk = words_needing[i:i + BATCH_WORD_SIZE]
            result = batch_generate_material(chunk, count_per_word=MIN_SENTENCES_PER_WORD)
            total_generated += result.get("generated", 0)
            # Single-word fallback for words that failed in the batch
            for lid in result.get("words_failed", []):
                try:
                    stored = generate_material_for_word(lid, needed=MIN_SENTENCES_PER_WORD)
                    total_generated += stored
                except Exception:
                    total_failed += 1
                    logger.exception("Single-word fallback failed for %d", lid)
        logger.info(
            "Post-scan batch generation: %d sentences generated, %d words failed, %d skipped",
            total_generated, total_failed, len(lemma_ids) - len(words_needing),
        )
    else:
        # Small batch: single-word path
        generated = 0
        failed = 0
        for lid in words_needing:
            try:
                stored = generate_material_for_word(lid, needed=MIN_SENTENCES_PER_WORD)
                generated += stored
            except Exception:
                failed += 1
                logger.exception("Material generation failed for OCR word %d", lid)
        logger.info(
            "Post-scan generation: %d sentences generated, %d words failed, %d skipped",
            generated, failed, len(lemma_ids) - len(words_needing),
        )


def _commit_with_retry(db: Session, label: str = "ocr", max_retries: int = 3) -> None:
    """Commit with retry on SQLite lock contention."""
    for attempt in range(max_retries):
        try:
            db.commit()
            return
        except OperationalError:
            db.rollback()
            if attempt < max_retries - 1:
                logger.warning(f"DB locked during {label}, retrying ({attempt + 1}/{max_retries})")
                time.sleep(1)
            else:
                raise


def process_batch(
    db: Session,
    batch_id: str,
    file_images: list[tuple[str, bytes]],
    preserve_known: bool = False,
) -> None:
    """Process an entire batch of textbook page images.

    1. OCR all pages in parallel (no DB needed)
    2. Dedupe extracted words across all pages
    3. Single DB transaction to import words + update page records

    The preserve_known flag is accepted for backwards compatibility only.
    Textbook scans are stored as high-priority encountered/new-word candidates.
    """
    _ = preserve_known
    uploads = (
        db.query(PageUpload)
        .filter(PageUpload.batch_id == batch_id)
        .order_by(PageUpload.id)
        .all()
    )
    upload_by_filename: dict[str, PageUpload] = {u.filename: u for u in uploads}

    # Mark all as processing
    for u in uploads:
        u.status = "processing"
    _commit_with_retry(db, "batch-mark-processing")

    # --- Phase 1: OCR all pages (no DB, parallelizable) ---
    def _ocr_one(item: tuple[str, bytes]) -> tuple[str, list[dict], int | None, str | None]:
        filename, image_bytes = item
        try:
            extracted, page_number = extract_words_from_image(image_bytes)
            return (filename, extracted, page_number, None)
        except Exception as e:
            logger.exception(f"OCR failed for {filename}")
            return (filename, [], None, str(e)[:500])

    with ThreadPoolExecutor(max_workers=4) as executor:
        ocr_results = list(executor.map(_ocr_one, file_images))

    # --- Phase 2: Dedupe words across all pages ---
    # Quality gate all extracted words together
    all_extracted: list[dict] = []
    page_word_indices: dict[str, list[int]] = {}  # filename -> indices into all_extracted

    for filename, extracted, page_number, error in ocr_results:
        upload = upload_by_filename.get(filename)
        if upload:
            upload.textbook_page_number = page_number
        if error:
            if upload:
                upload.status = "failed"
                upload.error_message = error
                upload.completed_at = datetime.now(timezone.utc)
            continue
        if not extracted:
            if upload:
                upload.status = "completed"
                upload.extracted_words_json = []
                upload.new_words = 0
                upload.existing_words = 0
                upload.completed_at = datetime.now(timezone.utc)
            continue

        start_idx = len(all_extracted)
        all_extracted.extend(extracted)
        page_word_indices[filename] = list(range(start_idx, len(all_extracted)))

    if not all_extracted:
        _commit_with_retry(db, "batch-no-words")
        return

    # Quality gate on combined word list
    from app.services.import_quality import classify_lemmas
    useful, rejected = classify_lemmas([
        {"arabic": w.get("arabic_bare", ""), "english": w.get("english", "")}
        for w in all_extracted
    ])
    _category_by_bare, _cleaned_by_bare = _build_import_category_maps(useful)
    rejected_bares = {r["arabic"] for r in rejected} if rejected else set()

    # --- Phase 3: Single DB import ---
    all_lemmas = db.query(Lemma).all()
    lemma_lookup = build_lemma_lookup(all_lemmas)
    bare_to_lemma: dict[str, Lemma] = {l.lemma_ar_bare: l for l in all_lemmas}
    knowledge_map: dict[int, UserLemmaKnowledge] = {
        ulk.lemma_id: ulk for ulk in db.query(UserLemmaKnowledge).all()
    }

    seen_bares: set[str] = set()  # dedupe across ALL pages
    new_lemma_ids: list[int] = []
    textbook_lemma_ids: set[int] = set()
    # Track per-word results indexed same as all_extracted
    word_results: list[dict | None] = [None] * len(all_extracted)

    for idx, word_data in enumerate(all_extracted):
        arabic = word_data.get("arabic", "").strip()
        if not arabic:
            continue

        arabic, san_warnings = sanitize_arabic_word(arabic)
        if (
            not arabic
            or "multi_word" in san_warnings
            or "too_short" in san_warnings
            or "no_letters" in san_warnings
        ):
            continue

        bare = compute_bare_form(arabic)
        base_lemma_bare = word_data.get("base_lemma")

        if _is_function_word(bare):
            continue
        if base_lemma_bare and _is_function_word(base_lemma_bare):
            continue
        if bare in rejected_bares or (base_lemma_bare and base_lemma_bare in rejected_bares):
            continue

        dedup_key = base_lemma_bare or bare
        if dedup_key in seen_bares:
            continue
        seen_bares.add(dedup_key)
        if base_lemma_bare and bare != base_lemma_bare:
            seen_bares.add(bare)

        # Look up existing lemma
        lemma_id = None
        if base_lemma_bare and base_lemma_bare != bare:
            lemma_id = lookup_lemma(base_lemma_bare, lemma_lookup)
        if not lemma_id:
            lemma_id = lookup_lemma(bare, lemma_lookup)

        if lemma_id:
            from app.services.canonical_resolution import resolve_canonical_lemma_id
            lemma_id = resolve_canonical_lemma_id(db, lemma_id)
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
            ulk = knowledge_map.get(lemma_id)

            if ulk:
                ulk = _record_textbook_encounter(db, lemma_id)
                lemma_id = ulk.lemma_id
                lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
                knowledge_map[lemma_id] = ulk
                textbook_lemma_ids.add(lemma_id)
                word_results[idx] = {
                    "arabic": lemma.lemma_ar if lemma else arabic,
                    "arabic_bare": bare,
                    "english": lemma.gloss_en if lemma else word_data.get("english"),
                    "status": "existing",
                    "lemma_id": lemma_id,
                    "knowledge_state": ulk.knowledge_state,
                }
            else:
                new_ulk = _record_textbook_encounter(db, lemma_id)
                lemma_id = new_ulk.lemma_id
                lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
                textbook_lemma_ids.add(lemma_id)
                knowledge_map[lemma_id] = new_ulk
                word_results[idx] = {
                    "arabic": lemma.lemma_ar if lemma else arabic,
                    "arabic_bare": bare,
                    "english": lemma.gloss_en if lemma else word_data.get("english"),
                    "status": "existing",
                    "lemma_id": lemma_id,
                    "knowledge_state": new_ulk.knowledge_state,
                }
        else:
            import_bare = base_lemma_bare if base_lemma_bare else bare
            # Apply LLM-cleaned bare form if available (fixes ال-prefix, ه→ة)
            if import_bare in _cleaned_by_bare:
                import_bare = _cleaned_by_bare[import_bare]
            english = (word_data.get("english") or "").strip()
            pos = word_data.get("pos")
            root_str = word_data.get("root")

            # Never create a Lemma without an English gloss
            if not english:
                logger.warning("Skipping lemma creation for %s: no English gloss", arabic)
                word_results[idx] = {
                    "arabic": arabic,
                    "arabic_bare": bare,
                    "english": "",
                    "status": "skipped_no_gloss",
                }
                continue

            root_id = None
            if root_str and _is_valid_root(root_str):
                existing_root = db.query(Root).filter(Root.root == root_str).first()
                if existing_root:
                    root_id = existing_root.root_id
                else:
                    new_root = Root(root=root_str, core_meaning_en="")
                    db.add(new_root)
                    db.flush()
                    root_id = new_root.root_id

            word_cat = _storage_word_category(_category_by_bare, import_bare, bare)
            pos = _normalize_import_pos(pos, word_cat)
            lemma_gloss = english
            if word_cat == "proper_name" and english and not english.startswith("(name)"):
                lemma_gloss = f"(name) {english}"

            new_lemma = Lemma(
                lemma_ar=arabic,
                lemma_ar_bare=import_bare,
                root_id=root_id,
                pos=pos,
                gloss_en=lemma_gloss,
                source="textbook_scan",
                word_category=word_cat,
            )
            db.add(new_lemma)
            db.flush()

            new_ulk = _record_textbook_encounter(db, new_lemma.lemma_id)
            textbook_lemma_ids.add(new_ulk.lemma_id)

            lemma_lookup[import_bare] = new_lemma.lemma_id
            if import_bare != bare:
                lemma_lookup[bare] = new_lemma.lemma_id
            if import_bare.startswith("ال") and len(import_bare) > 2:
                lemma_lookup[import_bare[2:]] = new_lemma.lemma_id
            else:
                lemma_lookup["ال" + import_bare] = new_lemma.lemma_id
            knowledge_map[new_lemma.lemma_id] = new_ulk
            bare_to_lemma[import_bare] = new_lemma

            new_lemma_ids.append(new_lemma.lemma_id)
            word_results[idx] = {
                "arabic": arabic,
                "arabic_bare": import_bare,
                "english": english,
                "status": "new",
                "lemma_id": new_lemma.lemma_id,
                "knowledge_state": new_ulk.knowledge_state,
                "root": root_str,
                "pos": pos,
            }

    # Update page upload records with per-page results
    for filename, indices in page_word_indices.items():
        upload = upload_by_filename.get(filename)
        if not upload:
            continue
        page_results = [word_results[i] for i in indices if word_results[i] is not None]
        new_count = sum(1 for r in page_results if r["status"] == "new")
        existing_count = sum(1 for r in page_results if r["status"] == "existing")
        upload.status = "completed"
        upload.extracted_words_json = page_results
        upload.new_words = new_count
        upload.existing_words = existing_count
        upload.completed_at = datetime.now(timezone.utc)

    # Mark any pages that had no words in page_word_indices as completed
    for u in uploads:
        if u.status == "processing":
            u.status = "completed"
            u.extracted_words_json = []
            u.new_words = 0
            u.existing_words = 0
            u.completed_at = datetime.now(timezone.utc)

    _commit_with_retry(db, "batch-import")

    # Run centralized quality gates (finalize + variants + enrich + stamp)
    variants_detected = 0
    variant_ids: set[int] = set()
    if new_lemma_ids:
        from app.services.lemma_quality import run_quality_gates
        gate_result = run_quality_gates(db, new_lemma_ids)
        variants_detected = gate_result.get("variants", 0)

        if variants_detected:
            variant_lemmas = db.query(Lemma).filter(
                Lemma.lemma_id.in_(new_lemma_ids),
                Lemma.canonical_lemma_id.isnot(None),
            ).all()
            variant_ids = {vl.lemma_id for vl in variant_lemmas}
            for vlem in variant_lemmas:
                vulk = knowledge_map.get(vlem.lemma_id)
                if vulk and vulk.knowledge_state not in ("suspended",):
                    vulk.knowledge_state = "encountered"
                    vulk.fsrs_card_json = None
                    vulk.last_reviewed = None
                    vulk.experiment_group = None
                    vulk.experiment_intro_shown_at = None
                    vulk.acquisition_box = None
                    vulk.acquisition_next_due = None
                    vulk.acquisition_started_at = None
                    vulk.entered_acquiring_at = None
                if vlem.canonical_lemma_id:
                    canonical_ulk = _record_textbook_encounter(db, vlem.canonical_lemma_id)
                    textbook_lemma_ids.add(canonical_ulk.lemma_id)
            _commit_with_retry(db, "batch-variant-revert")

    total_new = sum(u.new_words or 0 for u in uploads)
    total_existing = sum(u.existing_words or 0 for u in uploads)
    log_interaction(
        event="textbook_batch_processed",
        batch_id=batch_id,
        page_count=len(uploads),
        new_words=total_new,
        existing_words=total_existing,
        variants_detected=variants_detected,
    )

    # Commit before backfill_root_meanings which makes LLM calls
    _commit_with_retry(db, "batch-pre-root-backfill")
    backfill_root_meanings(db)
    _commit_with_retry(db, "batch-root-backfill")

    # Sentence generation for textbook words makes future review possible
    # before they enter the normal acquisition budget.
    if not variant_ids and new_lemma_ids:
        variant_ids = {
            r[0] for r in db.query(Lemma.lemma_id)
            .filter(Lemma.lemma_id.in_(new_lemma_ids), Lemma.canonical_lemma_id.isnot(None))
            .all()
        }
    all_needing_gen = textbook_lemma_ids - variant_ids
    # Also include new lemmas saved without preservation (they may be introduced later).
    all_needing_gen |= set(lid for lid in new_lemma_ids if lid not in variant_ids)
    gen_ids = list(all_needing_gen)
    logger.info(
        "Batch %s: scheduling sentence generation for %d words "
        "(%d new lemmas, %d textbook encounters)",
        batch_id, len(gen_ids), len(new_lemma_ids), len(textbook_lemma_ids),
    )
    _schedule_material_generation(db, gen_ids)
