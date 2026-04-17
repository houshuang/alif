"""Story generation, import, and review service.

Handles generating stories from known vocabulary via LLM,
importing external Arabic text with readiness analysis,
and managing story completion with FSRS credit.
"""

import random
from datetime import datetime, timezone

from sqlalchemy.orm import Session

import logging

from app.models import Lemma, Root, Sentence, UserLemmaKnowledge, Story, StoryWord
from app.services.fsrs_service import submit_review
from app.services.interaction_logger import log_interaction
from app.services.llm import (
    ARABIC_STYLE_RULES,
    DIFFICULTY_STYLE_GUIDE,
    AllProvidersFailed,
    generate_completion,
)
from app.services.sentence_generator import (
    get_content_word_counts,
    get_avoid_words,
    sample_known_words_weighted,
)
from app.services.sentence_validator import (
    ARABIC_PUNCTUATION,
    FUNCTION_WORD_GLOSSES,
    FUNCTION_WORDS,
    build_lemma_lookup,
    normalize_alef,
    resolve_existing_lemma,
    strip_diacritics,
    strip_tatweel,
    tokenize,
    _is_function_word,
    lookup_lemma,
)
from app.services.morphology import find_best_db_match, get_word_features, is_valid_root

logger = logging.getLogger(__name__)

KNOWN_SAMPLE_SIZE = 500
MAX_STORY_ATTEMPTS = 1
MAX_CORRECTION_ROUNDS = 3
TERMINAL_STORY_STATUSES = {"completed"}
HIDDEN_STORY_STATUSES = {"deleted", "failed"}

STORY_SYSTEM_PROMPT = f"""\
You are a brilliant Arabic storyteller — think Borges writing flash fiction with a \
limited palette. Your constraint (a small vocabulary) is your creative challenge. \
Write in MSA (fusha) with a real narrative arc that surprises and delights.

CRITICAL: Write a COHESIVE STORY with beginning, middle, and end. Every sentence must \
connect to the previous one and advance the narrative.

{ARABIC_STYLE_RULES}

Story craft — the story MUST have at least one of these qualities:
- HUMOR: a situation that escalates absurdly, a misunderstanding, irony, a deadpan punchline
- SUSPENSE: withhold key information, reveal it at the end, make the reader curious
- TWIST: the ending reframes everything — the reader sees the story differently
- POETRY: a moment of beauty, a metaphor that lands, a bittersweet observation
- WARMTH: an unexpected connection between characters, a small kindness that matters

Craft techniques:
- Give the main character a name and a specific situation (not generic)
- Use concrete details — a white horse, a broken door, a cold morning — not abstractions
- Dialogue brings characters alive (use قَالَ/قَالَتْ sparingly but effectively)
- The last sentence matters most — land the ending
- VSO for narration (ذَهَبَ الرَّجُلُ), SVO for emphasis/contrast (الرَّجُلُ ذَهَبَ وَحْدَهُ)
- Nominal sentences for scene-setting (اللَّيْلُ طَوِيلٌ)
- Avoid clichés: no "and they lived happily", no "the moral is...", no "one day there was a man"

{DIFFICULTY_STYLE_GUIDE}

Vocabulary constraint:
- Use ONLY words from the provided vocabulary list and common function words
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، ك، هذا، هذه، \
ذلك، تلك، هو، هي، أنا، أنت، نحن، هم، ما، لا، أن، إن، كان، كانت، ليس، هل، لم، \
لن، قد، الذي، التي، كل، بعض، هنا، هناك، الآن، جدا، فقط، أيضا، أو، ثم، لكن، يا
- Do NOT invent or use Arabic content words not in the vocabulary list
- Include full diacritics (tashkeel) on ALL Arabic words with correct i'rab
- Include Arabic punctuation: use ؟ for questions, . for statements, ، between clauses
- Transliteration: ALA-LC standard with macrons for long vowels

Respond with JSON only: {{"title_ar": "...", "title_en": "...", "body_ar": "...", "body_en": "...", "transliteration": "..."}}"""


def _get_known_words(db: Session) -> list[dict]:
    """Fetch words eligible for story vocabulary.

    Includes learning/known words and acquiring words in Leitner box 2+.
    Box 1 words are too fresh — they've only been seen once and shouldn't
    appear in stories yet.
    """
    rows = (
        db.query(Lemma, UserLemmaKnowledge)
        .join(UserLemmaKnowledge, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "acquiring"]))
        .filter(Lemma.canonical_lemma_id.is_(None))
        .all()
    )
    return [
        {
            "lemma_id": lemma.lemma_id,
            "arabic": lemma.lemma_ar,
            "arabic_bare": lemma.lemma_ar_bare,
            "english": lemma.gloss_en or "",
            "pos": lemma.pos or "",
            "state": ulk.knowledge_state,
        }
        for lemma, ulk in rows
        if ulk.knowledge_state != "acquiring" or (ulk.acquisition_box or 0) >= 2
    ]


def _get_all_lemmas(db: Session) -> list:
    """Fetch all lemmas for lookup building."""
    return db.query(Lemma).all()


def _tokenize_story(text: str) -> list[str]:
    """Tokenize story text, stripping punctuation (for lookup/compliance)."""
    return tokenize(text)


def _tokenize_story_display(text: str) -> list[tuple[str, str]]:
    """Tokenize story text preserving punctuation on surface forms.

    Same approach as tokenize_display() used for sentence review.
    Returns list of (display_form, clean_form) tuples.
    display_form keeps punctuation attached. clean_form is bare for lookup.
    """
    results = []
    for raw_token in text.split():
        if not raw_token.strip():
            continue
        clean = strip_diacritics(raw_token)
        clean = strip_tatweel(clean)
        clean = ARABIC_PUNCTUATION.sub("", clean)
        if not clean.strip():
            continue
        results.append((raw_token, clean))
    return results


def _create_story_words(
    db: Session,
    story: Story,
    body_ar: str,
    lemma_lookup: dict[str, int],
    knowledge_map: dict[int, str],
) -> tuple[int, int, int]:
    """Create StoryWord records for each token in the story.

    Surface forms preserve original punctuation (commas, periods, guillemets)
    so the reader can display them naturally.

    Returns (total_words, known_count, function_word_count).
    """
    import re
    raw_parts = re.split(r"[.\n]", body_ar)
    sentences = [s.strip() for s in raw_parts if s.strip()]
    position = 0
    total = 0
    known = 0
    func = 0

    # Build set of known bare forms for morphological fallback
    known_bare_forms: set[str] = set()
    for lem in db.query(Lemma).all():
        known_bare_forms.add(normalize_alef(lem.lemma_ar_bare))

    # Batch-load all lemmas for gloss lookup (avoid N+1 queries)
    all_lemma_ids_needed: set[int] = set()
    morph_cache: dict[str, int | None] = {}
    for sent_idx, sentence_text in enumerate(sentences):
        for display_form, clean_form in _tokenize_story_display(sentence_text):
            bare_norm = normalize_alef(clean_form)
            if not _is_function_word(clean_form):
                lid = lookup_lemma(bare_norm, lemma_lookup)
                if not lid and bare_norm not in morph_cache:
                    match = find_best_db_match(clean_form, known_bare_forms)
                    if match:
                        lex_norm = normalize_alef(match["lex_bare"])
                        lid = lemma_lookup.get(lex_norm)
                    morph_cache[bare_norm] = lid
                elif not lid:
                    lid = morph_cache.get(bare_norm)
                if lid:
                    all_lemma_ids_needed.add(lid)

    lemma_by_id: dict[int, Lemma] = {}
    if all_lemma_ids_needed:
        lemma_rows = db.query(Lemma).filter(Lemma.lemma_id.in_(all_lemma_ids_needed)).all()
        lemma_by_id = {l.lemma_id: l for l in lemma_rows}

    for sent_idx, sentence_text in enumerate(sentences):
        for display_form, clean_form in _tokenize_story_display(sentence_text):
            bare_norm = normalize_alef(clean_form)

            is_func = _is_function_word(clean_form)
            lemma_id = None if is_func else lookup_lemma(bare_norm, lemma_lookup)
            if not lemma_id and not is_func:
                lemma_id = morph_cache.get(bare_norm)

            # Also check the resolved lemma's bare form (catches cliticized
            # surface forms like بِهِ whose lemma بِ is a function word)
            if lemma_id and not is_func:
                lemma = lemma_by_id.get(lemma_id)
                if lemma and _is_function_word(lemma.lemma_ar_bare):
                    is_func = True

            is_known = False
            if lemma_id:
                state = knowledge_map.get(lemma_id)
                is_known = state in ("learning", "known")

            gloss = None
            if lemma_id:
                lemma = lemma_by_id.get(lemma_id)
                if lemma:
                    gloss = lemma.gloss_en
            elif is_func:
                gloss = FUNCTION_WORD_GLOSSES.get(bare_norm) or FUNCTION_WORD_GLOSSES.get(clean_form)

            sw = StoryWord(
                story_id=story.id,
                position=position,
                surface_form=display_form,
                lemma_id=lemma_id,
                sentence_index=sent_idx,
                gloss_en=gloss,
                is_known_at_creation=is_known or is_func,
                is_function_word=is_func,
            )
            db.add(sw)
            total += 1
            if is_known:
                known += 1
            if is_func:
                func += 1
            position += 1

    return total, known, func


def _import_unknown_words(
    db: Session,
    story: Story,
    lemma_lookup: dict[str, int],
) -> list[int]:
    """Create Lemma entries for unknown words in a story.

    For words with lemma_id=None (excluding function words), uses CAMeL morphology
    + LLM batch translation to create proper Lemma (+ Root) entries.
    Does NOT create ULK — words become Learn mode candidates via story_bonus.

    Structured to avoid holding the DB write lock during LLM calls:
    Phase 1-2: Read DB + CAMeL analysis + all LLM calls (no DB writes)
    Phase 3: Batch DB writes (roots, lemmas, story word updates)
    Phase 4: Post-write LLM calls (variant detection, mapping verification)

    Returns list of newly created lemma_ids.
    """
    # ── Phase 1: Collect unknowns + CAMeL analysis (read-only) ──────────

    # Collect unknown surface forms
    unknown_words: list[StoryWord] = []
    seen_bares: set[str] = set()
    for sw in story.words:
        if sw.lemma_id is not None or sw.is_function_word:
            continue
        bare = normalize_alef(strip_diacritics(sw.surface_form))
        if bare in seen_bares or len(bare) < 2:
            continue
        seen_bares.add(bare)
        unknown_words.append(sw)

    if not unknown_words:
        return []

    # CAMeL morphological analysis — resolve to existing lemmas where possible
    word_analyses: list[dict] = []
    # Deferred StoryWord updates for words resolved to existing lemmas.
    # Collected here, applied in Phase 3 to avoid dirtying the session early.
    resolved_updates: list[tuple[StoryWord, int]] = []  # (sw, existing_lemma_id)
    resolved_lemma_ids: set[int] = set()

    for sw in unknown_words:
        # Normalize Quranic orthography before CAMeL analysis:
        # alef wasla (ٱ) → regular alef (ا), small/superscript marks
        surface_normalized = sw.surface_form.replace("\u0671", "\u0627")
        features = get_word_features(surface_normalized)
        lex_bare = strip_diacritics(features.get("lex", surface_normalized))
        lex_norm = normalize_alef(lex_bare)
        # Check if the base lemma from CAMeL already exists in DB
        existing_id = lemma_lookup.get(lex_norm)
        # If CAMeL returned a cliticized form, try stripping prefixes
        if existing_id is None:
            existing_id = resolve_existing_lemma(lex_bare, lemma_lookup)
        if existing_id:
            # CAMeL resolved it to a known lemma — defer update to Phase 3
            resolved_updates.append((sw, existing_id))
            resolved_lemma_ids.add(existing_id)
            continue
        word_analyses.append({
            "story_word": sw,
            "surface": sw.surface_form,
            "lex": features.get("lex", surface_normalized),
            "lex_bare": lex_bare,
            "lex_norm": lex_norm,
            "root": features.get("root"),
            "pos": features.get("pos", "UNK"),
        })

    # Batch-load glosses for all resolved lemmas (avoids N+1 queries later)
    resolved_gloss_map: dict[int, str] = {}
    if resolved_lemma_ids:
        for lem in db.query(Lemma).filter(Lemma.lemma_id.in_(resolved_lemma_ids)).all():
            resolved_gloss_map[lem.lemma_id] = lem.gloss_en or ""

    if not word_analyses:
        # Apply deferred resolved updates before returning
        for sw, lid in resolved_updates:
            sw.lemma_id = lid
            sw.gloss_en = resolved_gloss_map.get(lid)
        db.flush()
        return []

    # Pre-load existing roots for root lookup in Phase 3 (avoids queries during writes)
    root_strs_needed = {a["root"] for a in word_analyses if a.get("root") and is_valid_root(a["root"])}
    existing_roots: dict[str, int] = {}
    if root_strs_needed:
        for r in db.query(Root).filter(Root.root.in_(root_strs_needed)).all():
            existing_roots[r.root] = r.root_id

    # Pre-load lemmas for dedup checks in Phase 3 (batch instead of per-word queries)
    dedup_lemma_ids: set[int] = set()
    for a in word_analyses:
        lid = lemma_lookup.get(a["lex_norm"])
        if lid:
            dedup_lemma_ids.add(lid)
        lid2 = resolve_existing_lemma(a["lex_bare"], lemma_lookup)
        if lid2:
            dedup_lemma_ids.add(lid2)
    dedup_gloss_map: dict[int, str] = {}
    if dedup_lemma_ids:
        for lem in db.query(Lemma).filter(Lemma.lemma_id.in_(dedup_lemma_ids)).all():
            dedup_gloss_map[lem.lemma_id] = lem.gloss_en or ""

    # ── Phase 2: All LLM calls (no DB writes) ───────────────────────────

    # Step 2a: LLM batch translation — use lex (base form) not surface form
    gloss_map: dict[str, dict] = {}
    try:
        # Build word list from CAMeL lex forms for better dictionary glosses
        words_for_llm = []
        for a in word_analyses:
            words_for_llm.append(f"{a['lex']} ({a['surface']})" if a['lex'] != a['surface'] else a['surface'])
        words_list = "، ".join(words_for_llm)

        result = generate_completion(
            prompt=f"""Given these Arabic words (base/dictionary forms), provide dictionary-form English glosses, part of speech, and whether the word is a proper name.

Words: {words_list}

IMPORTANT: Give dictionary-form glosses, NOT conjugated translations:
- Verbs: use infinitive ("to write", "to wake up"), NOT ("she wrote", "he woke up")
- Nouns: use bare singular ("book", "school"), NOT ("his books", "the schools")
- Adjectives: use base form ("big", "beautiful"), NOT ("bigger", "the big one")

Respond with JSON array: [{{"arabic": "the base form word", "english": "dictionary gloss", "pos": "noun/verb/adj/adv/prep/conj", "name_type": null or "personal" or "place"}}]

Set name_type to "personal" for personal names (people, characters), "place" for place names (cities, countries, landmarks), or null for regular vocabulary words.""",
            system_prompt="You translate Arabic words to English. Give concise, dictionary-form glosses (1-3 words). For verbs use infinitive ('to X'). For proper names, provide the transliterated name. Respond with JSON only.",
            json_mode=True,
            task_type="story_word_import",
        )

        # Result may be a list or a dict with a list inside
        items = result if isinstance(result, list) else result.get("words", result.get("translations", []))
        if isinstance(items, list):
            for idx, item in enumerate(items):
                arabic = item.get("arabic", "")
                bare = normalize_alef(strip_diacritics(arabic))
                gloss_data = {
                    "english": item.get("english", ""),
                    "pos": item.get("pos"),
                    "name_type": item.get("name_type"),
                }
                gloss_map[bare] = gloss_data
                # Positional fallback: also key by the surface/lex forms we sent
                if idx < len(word_analyses):
                    a = word_analyses[idx]
                    surface_bare = normalize_alef(strip_diacritics(a["story_word"].surface_form))
                    if surface_bare not in gloss_map:
                        gloss_map[surface_bare] = gloss_data
                    if a["lex_norm"] not in gloss_map:
                        gloss_map[a["lex_norm"]] = gloss_data
    except (AllProvidersFailed, Exception) as e:
        logger.warning("LLM translation failed for story %d unknown words: %s", story.id, e)

    # Step 2b: Quality gate — filter out junk + classify (names, sounds)
    _category_by_bare: dict[str, str] = {}
    if word_analyses and gloss_map:
        try:
            from app.services.import_quality import classify_lemmas
            lemma_dicts = [
                {"arabic": a["lex_bare"], "english": gloss_map.get(
                    normalize_alef(strip_diacritics(a["story_word"].surface_form)), {}
                ).get("english", "")}
                for a in word_analyses
            ]
            useful, rejected = classify_lemmas(lemma_dicts)
            # Build category lookup by bare form
            _category_by_bare: dict[str, str] = {}
            for u in useful:
                _category_by_bare[u["arabic"]] = u.get("word_category", "standard")
            if rejected:
                rejected_bares = {r["arabic"] for r in rejected}
                word_analyses = [a for a in word_analyses if a["lex_bare"] not in rejected_bares]
                logger.info("Story %d: quality gate rejected %d words: %s",
                           story.id, len(rejected),
                           ", ".join(r["arabic"] for r in rejected[:5]))
        except Exception as e:
            logger.warning("Quality gate failed for story %d: %s", story.id, e)

    # ── Phase 3: Batch DB writes (all roots, lemmas, story word updates) ─

    # First, apply deferred resolved updates from Phase 1
    for sw, lid in resolved_updates:
        sw.lemma_id = lid
        sw.gloss_en = resolved_gloss_map.get(lid)

    # Prepare transliterations (deterministic, no DB/LLM needed)
    from app.services.transliteration import transliterate_lemma

    # Process each analysis: classify, prepare root/lemma data, then write
    new_lemma_ids: list[int] = []

    # Batch-create all needed new roots first (single flush)
    roots_to_create: dict[str, Root] = {}  # root_str -> Root object
    for analysis in word_analyses:
        root_str = analysis.get("root")
        if root_str and is_valid_root(root_str) and root_str not in existing_roots and root_str not in roots_to_create:
            new_root = Root(root=root_str, core_meaning_en="")
            db.add(new_root)
            roots_to_create[root_str] = new_root
    if roots_to_create:
        db.flush()
        # Update lookup with newly created root IDs
        for root_str, root_obj in roots_to_create.items():
            existing_roots[root_str] = root_obj.root_id

    # Now create lemmas and update story words
    for analysis in word_analyses:
        lex_norm = analysis["lex_norm"]
        lex_bare = analysis["lex_bare"]
        sw = analysis["story_word"]

        # Get gloss from LLM — skip creating lemma if translation missing
        surface_bare = normalize_alef(strip_diacritics(sw.surface_form))
        gloss_data = gloss_map.get(surface_bare, gloss_map.get(lex_norm, {}))
        english = gloss_data.get("english", "").strip()
        pos = gloss_data.get("pos") or analysis["pos"]
        name_type = gloss_data.get("name_type")
        if pos == "UNK":
            pos = None

        # Never create a Lemma without a gloss — useless for learning
        if not english and name_type not in ("personal", "place"):
            logger.warning(
                "Skipping lemma creation for %s (story %d): no English gloss from LLM",
                sw.surface_form, story.id,
            )
            continue

        # Proper nouns: mark as function word, no Lemma entry
        if name_type in ("personal", "place"):
            sw.is_function_word = True
            sw.name_type = name_type
            sw.gloss_en = english
            # Also tag any duplicate surface forms in the story
            for other_sw in story.words:
                if other_sw.id == sw.id:
                    continue
                other_bare = normalize_alef(strip_diacritics(other_sw.surface_form))
                if other_bare == surface_bare and other_sw.lemma_id is None:
                    other_sw.is_function_word = True
                    other_sw.name_type = name_type
                    other_sw.gloss_en = english
            continue

        # Look up root (already created in batch above)
        root_id = None
        root_str = analysis.get("root")
        if root_str and is_valid_root(root_str):
            root_id = existing_roots.get(root_str)

        # Dedup check: direct match or clitic-aware resolve
        existing_id = lemma_lookup.get(lex_norm)
        if existing_id is None:
            existing_id = resolve_existing_lemma(lex_bare, lemma_lookup)
        if existing_id is not None:
            sw.lemma_id = existing_id
            sw.gloss_en = dedup_gloss_map.get(existing_id)
            continue

        word_cat = _category_by_bare.get(lex_bare)
        # Prefix gloss for proper names so it's clear during review
        lemma_gloss = english
        if word_cat == "proper_name" and english and not english.startswith("(name)"):
            lemma_gloss = f"(name) {english}"

        # Generate transliteration inline (deterministic, instant)
        translit = None
        lex_form = analysis["lex"]
        if lex_form and any("\u0610" <= c <= "\u065f" or "\u0670" <= c <= "\u0670" for c in lex_form):
            try:
                translit = transliterate_lemma(lex_form)
            except Exception:
                pass

        new_lemma = Lemma(
            lemma_ar=lex_form,
            lemma_ar_bare=lex_bare,
            root_id=root_id,
            pos=pos,
            gloss_en=lemma_gloss,
            transliteration_ala_lc=translit,
            source="story_import",
            source_story_id=story.id,
            word_category=word_cat if word_cat in ("proper_name", "onomatopoeia") else None,
        )
        db.add(new_lemma)
        db.flush()

        # Update lookup for dedup within batch
        lemma_lookup[lex_norm] = new_lemma.lemma_id
        if lex_norm != surface_bare:
            lemma_lookup[surface_bare] = new_lemma.lemma_id

        # Update all StoryWords with matching bare form
        for other_sw in story.words:
            if other_sw.lemma_id is not None:
                continue
            other_bare = normalize_alef(strip_diacritics(other_sw.surface_form))
            if other_bare == surface_bare or other_bare == lex_norm:
                other_sw.lemma_id = new_lemma.lemma_id
                other_sw.gloss_en = english

        new_lemma_ids.append(new_lemma.lemma_id)

    db.flush()

    # ── Phase 4: Post-write quality gates + mapping verification ─────────

    if new_lemma_ids:
        from app.services.lemma_quality import run_quality_gates
        run_quality_gates(db, new_lemma_ids)

    # Verify new lemma-StoryWord mappings via LLM
    if new_lemma_ids:
        _verify_new_story_mappings(db, story, set(new_lemma_ids))

    return new_lemma_ids


def _verify_new_story_mappings(
    db: Session, story: Story, new_lemma_ids: set[int]
) -> None:
    """Verify StoryWord mappings for newly created lemmas using LLM.

    Groups story words into chunks by position, builds a sentence-like context,
    and calls verify_and_correct_mappings_llm. Wrong mappings are nulled out
    (never auto-create lemmas from corrections).
    """
    from app.services.sentence_validator import (
        apply_corrections,
        verify_and_correct_mappings_llm,
        TokenMapping,
    )

    # Collect story words that reference new lemmas
    words_to_verify = [
        sw for sw in story.words
        if sw.lemma_id and sw.lemma_id in new_lemma_ids
    ]
    if not words_to_verify:
        return

    # Build context: get surrounding text for each new-lemma word
    all_words = sorted(story.words, key=lambda w: w.position)
    text_tokens = [sw.surface_form for sw in all_words]
    full_text = " ".join(text_tokens)

    # Build mappings for verification (only new-lemma words)
    lemma_ids_needed = {sw.lemma_id for sw in words_to_verify if sw.lemma_id}
    lemma_map = {
        l.lemma_id: l for l in db.query(Lemma).filter(
            Lemma.lemma_id.in_(list(lemma_ids_needed))
        ).all()
    }

    # Batch verify in chunks of 15 words to keep LLM context manageable
    CHUNK_SIZE = 15
    fixed = 0
    nulled = 0
    for i in range(0, len(words_to_verify), CHUNK_SIZE):
        chunk = words_to_verify[i:i + CHUNK_SIZE]
        mappings = [
            TokenMapping(
                position=sw.position,
                surface_form=sw.surface_form,
                lemma_id=sw.lemma_id,
                is_target=False,
                is_function_word=sw.is_function_word or False,
            )
            for sw in chunk
        ]

        # Use story text as context (truncated around the chunk)
        min_pos = min(sw.position for sw in chunk)
        max_pos = max(sw.position for sw in chunk)
        context_start = max(0, min_pos - 5)
        context_end = min(len(text_tokens), max_pos + 6)
        context_text = " ".join(text_tokens[context_start:context_end])

        corrections = verify_and_correct_mappings_llm(
            context_text, "", mappings, lemma_map,
        )
        if corrections is None or not corrections:
            continue

        # apply_corrections mutates mappings (TokenMapping wrappers);
        # mirror successful changes back to the real StoryWord objects
        sw_by_pos = {sw.position: sw for sw in chunk}
        failed_positions = apply_corrections(
            corrections, mappings, db, arabic_text=context_text,
        )
        # Sync corrected lemma_ids from TokenMapping back to StoryWord
        for m in mappings:
            sw = sw_by_pos.get(m.position)
            if sw and sw.lemma_id != m.lemma_id:
                sw.lemma_id = m.lemma_id
                fixed += 1
        # Null out StoryWord mappings that couldn't be corrected
        for pos in failed_positions:
            sw = sw_by_pos.get(pos)
            if sw:
                sw.lemma_id = None
                sw.gloss_en = None
                nulled += 1

    if fixed or nulled:
        db.flush()
        logger.info(f"Story {story.id}: verified new mappings — {fixed} fixed, {nulled} nulled")


_ACTIVELY_LEARNING_STATES = {"acquiring", "learning", "known", "lapsed"}


def _recalculate_story_counts(db: Session, story: Story) -> None:
    """Recalculate total_words, known_count, unknown_count, readiness_pct from StoryWords.

    Counts are deduplicated by lemma_id — each unique lemma is counted once.
    Function words and words without a lemma_id are excluded entirely.

    Also re-checks function word flags on each StoryWord in case the function
    word list has been updated since the story was imported.
    """
    story_lemma_ids = {sw.lemma_id for sw in story.words if sw.lemma_id}
    knowledge_map = _build_knowledge_map(db, lemma_ids=story_lemma_ids or None)

    # Load lemmas for function word re-checking via lemma bare form
    lemma_map: dict[int, Lemma] = {}
    if story_lemma_ids:
        lemma_rows = db.query(Lemma).filter(Lemma.lemma_id.in_(story_lemma_ids)).all()
        lemma_map = {l.lemma_id: l for l in lemma_rows}

    seen_lemmas: set[int] = set()
    seen_func: set[int | str] = set()  # track func words by lemma_id or surface
    total = 0
    known = 0
    func = 0
    func_fixed = 0
    for sw in story.words:
        # Re-check function word status: surface form or resolved lemma bare form
        is_func = sw.is_function_word
        if not is_func:
            surface_clean = strip_diacritics(sw.surface_form).strip()
            if _is_function_word(surface_clean):
                is_func = True
            elif sw.lemma_id:
                lemma = lemma_map.get(sw.lemma_id)
                if lemma and _is_function_word(lemma.lemma_ar_bare):
                    is_func = True
            if is_func and not sw.is_function_word:
                sw.is_function_word = True
                func_fixed += 1

        if is_func:
            key = sw.lemma_id or sw.surface_form
            if key not in seen_func:
                seen_func.add(key)
                func += 1
            continue
        if not sw.lemma_id or sw.lemma_id in seen_lemmas:
            continue
        seen_lemmas.add(sw.lemma_id)
        total += 1
        if knowledge_map.get(sw.lemma_id) in _ACTIVELY_LEARNING_STATES:
            known += 1
    if func_fixed:
        logger.info("Story %d: fixed %d function word flags", story.id, func_fixed)
    story.total_words = total
    story.known_count = known
    story.unknown_count = total - known
    story.readiness_pct = round((known + func) / (total + func) * 100, 1) if (total + func) > 0 else 0


_STATE_RANK = {"known": 4, "lapsed": 3, "learning": 2, "acquiring": 1, "encountered": 0}


def _classify_unknowns_by_root(
    db: Session,
    unknown_ids: list[int],
) -> tuple[dict[int, int], set[int]]:
    """For a list of unknown lemma IDs, find their roots and which roots have known siblings.

    Returns (unknown_root_map, known_root_ids) where:
    - unknown_root_map: lemma_id -> root_id for unknowns that have a root
    - known_root_ids: root IDs that have at least one actively-learning lemma in the DB
    """
    root_rows = (
        db.query(Lemma.lemma_id, Lemma.root_id)
        .filter(Lemma.lemma_id.in_(unknown_ids), Lemma.root_id.isnot(None))
        .all()
    )
    unknown_root_map = {r.lemma_id: r.root_id for r in root_rows}
    candidate_root_ids = set(unknown_root_map.values())

    known_root_ids: set[int] = set()
    if candidate_root_ids:
        known_root_rows = (
            db.query(Lemma.root_id)
            .join(UserLemmaKnowledge, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
            .filter(
                Lemma.root_id.in_(candidate_root_ids),
                UserLemmaKnowledge.knowledge_state.in_(_ACTIVELY_LEARNING_STATES),
            )
            .distinct()
            .all()
        )
        known_root_ids = {r.root_id for r in known_root_rows}

    return unknown_root_map, known_root_ids


def _is_warm(lid: int, unknown_root_map: dict[int, int], known_root_ids: set[int]) -> bool:
    """Return True if the unknown lemma has a known root sibling."""
    root_id = unknown_root_map.get(lid)
    return root_id is not None and root_id in known_root_ids


def _compute_cold_warm_counts(
    db: Session,
    story_lemma_ids: set[int],
    knowledge_map: dict[int, str],
    known_count: int,
    total_words: int,
) -> tuple[int, int, float]:
    """Classify unknown story lemmas as cold or warm based on root-family knowledge.

    Cold = unknown AND no known root siblings in the full DB.
    Warm = unknown BUT at least one lemma from the same root is known/acquiring.

    Returns (cold_unknown_count, warm_unknown_count, reading_readiness_pct).
    reading_readiness_pct applies 0.6 partial credit for warm unknowns, reflecting
    that root-family knowledge gives ~50-70% semantic access (Boudelaa & Marslen-Wilson 2013).
    """
    unknown_ids = [
        lid for lid in story_lemma_ids
        if knowledge_map.get(lid) not in _ACTIVELY_LEARNING_STATES
    ]
    if not unknown_ids:
        pct = round(known_count / max(1, total_words) * 100, 1)
        return 0, 0, pct

    unknown_root_map, known_root_ids = _classify_unknowns_by_root(db, unknown_ids)

    warm = sum(1 for lid in unknown_ids if _is_warm(lid, unknown_root_map, known_root_ids))
    cold = len(unknown_ids) - warm

    reading_readiness_pct = round(
        (known_count + 0.6 * warm) / max(1, total_words) * 100, 1
    )
    return cold, warm, reading_readiness_pct


def get_pretest_words(db: Session, story_id: int) -> list[dict]:
    """Return top 5 cold unknown words ordered by token frequency in the story.

    Cold = unknown and no known root siblings. These are ideal pretest targets:
    the learner will almost certainly fail (activating encoding preparation),
    and they appear frequently enough to matter for reading comprehension.
    """
    from collections import Counter

    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        return []

    token_counts = Counter(
        sw.lemma_id for sw in story.words
        if sw.lemma_id and not sw.is_function_word
    )
    if not token_counts:
        return []

    story_lemma_ids = set(token_counts.keys())
    knowledge_map = _build_knowledge_map(db, lemma_ids=story_lemma_ids)

    unknown_ids = [
        lid for lid in story_lemma_ids
        if knowledge_map.get(lid) not in _ACTIVELY_LEARNING_STATES
    ]
    if not unknown_ids:
        return []

    unknown_root_map, known_root_ids = _classify_unknowns_by_root(db, unknown_ids)

    cold_unknowns = sorted(
        [
            (lid, token_counts[lid])
            for lid in unknown_ids
            if not _is_warm(lid, unknown_root_map, known_root_ids)
        ],
        key=lambda x: -x[1],
    )

    top_ids = [lid for lid, _ in cold_unknowns[:5]]
    if not top_ids:
        return []

    lemma_map = {
        lem.lemma_id: lem
        for lem in db.query(Lemma).filter(Lemma.lemma_id.in_(top_ids)).all()
    }

    result = []
    for lid, freq in cold_unknowns[:5]:
        lemma = lemma_map.get(lid)
        if not lemma:
            continue
        result.append({
            "lemma_id": lid,
            "arabic": lemma.lemma_ar,
            "gloss_en": lemma.gloss_en or "",
            "root_ar": lemma.root.root if lemma.root else None,
            "token_frequency": freq,
        })
    return result


def _build_knowledge_map(db: Session, lemma_ids: set[int] | None = None) -> dict[int, str]:
    """Build lemma_id -> knowledge_state map.

    Resolves variants: if a lemma is a variant of a canonical lemma,
    the canonical's knowledge state is used when it's more advanced.
    """
    q = db.query(UserLemmaKnowledge)
    if lemma_ids is not None:
        q = q.filter(UserLemmaKnowledge.lemma_id.in_(lemma_ids))
    rows = q.all()
    result = {r.lemma_id: r.knowledge_state for r in rows}

    if not lemma_ids:
        return result

    # Resolve variants: follow chains to root canonical and use best state.
    # First check which of our lemma_ids are variants, then follow chains.
    direct_variants = (
        db.query(Lemma.lemma_id, Lemma.canonical_lemma_id)
        .filter(
            Lemma.lemma_id.in_(lemma_ids),
            Lemma.canonical_lemma_id.isnot(None),
        )
        .all()
    )
    canon_map: dict[int, int] = {}
    if direct_variants:
        # Only load the full mapping table if we actually have variants to resolve
        all_mappings = {
            r.lemma_id: r.canonical_lemma_id
            for r in db.query(Lemma.lemma_id, Lemma.canonical_lemma_id)
            .filter(Lemma.canonical_lemma_id.isnot(None))
            .all()
        }
        for var_id, canon_id in direct_variants:
            current = canon_id
            seen = {var_id, current}
            while current in all_mappings:
                nxt = all_mappings[current]
                if nxt in seen:
                    break
                seen.add(nxt)
                current = nxt
            canon_map[var_id] = current
    if canon_map:
        canonical_ids = set(canon_map.values())
        canon_ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id.in_(canonical_ids))
            .all()
        )
        canon_states = {r.lemma_id: r.knowledge_state for r in canon_ulk}
        for var_id, canon_id in canon_map.items():
            canon_state = canon_states.get(canon_id)
            if canon_state:
                var_state = result.get(var_id)
                if not var_state or _STATE_RANK.get(canon_state, 0) > _STATE_RANK.get(var_state, 0):
                    result[var_id] = canon_state

    return result


LENGTH_SENTENCES = {"short": (2, 4), "medium": (4, 7), "long": (7, 12)}

STORY_GENRES = [
    "a funny story with a deadpan punchline — the humor sneaks up on the reader",
    "a mystery — something strange is happening, and the last sentence explains everything",
    "a heartwarming story about an unexpected connection between two people",
    "a story with a twist ending that reframes every sentence before it",
    "a mini-adventure with a moment of real tension and a clever resolution",
    "a story where someone discovers something surprising about themselves",
    "a quiet, poetic observation — a small moment that reveals something true about life",
    "a comedy of errors — a misunderstanding that spirals into absurdity",
    "a story told from an unusual perspective (an animal, an object, a child)",
    "a ghost story or eerie atmosphere — something is not quite right",
    "a story about revenge, forgiveness, or an old grudge between neighbors",
    "a fable-like story with talking animals that has a sly, non-obvious point",
    "a story set in a market, café, or school where something unexpected disrupts the routine",
    "a bittersweet story — the ending is happy and sad at the same time",
]


def _check_story_compliance(
    body_ar: str,
    lemma_lookup: dict[str, int],
    known_lemma_ids: set[int] | None = None,
) -> tuple[float, list[str]]:
    """Check vocabulary compliance of generated story text.

    If known_lemma_ids is provided, a word is only "known" if it resolves to
    a lemma in that set. Otherwise falls back to any lemma in DB.

    Returns (compliance_pct, list_of_unknown_bare_forms).
    """
    from app.services.sentence_validator import FUNCTION_WORD_GLOSSES as FWG
    from app.services.sentence_validator import FUNCTION_WORD_FORMS as FWF

    func_bares = {normalize_alef(fw) for fw in FWG} | {normalize_alef(fw) for fw in FWF}
    tokens = tokenize(body_ar)
    content_total = 0
    content_known = 0
    unknown_list = []

    for token in tokens:
        bare = normalize_alef(strip_tatweel(strip_diacritics(token)))
        if bare in func_bares:
            continue
        content_total += 1
        lemma_id = lookup_lemma(bare, lemma_lookup)
        if lemma_id and (known_lemma_ids is None or lemma_id in known_lemma_ids):
            content_known += 1
        elif bare not in unknown_list:
            unknown_list.append(bare)

    pct = round(content_known / content_total * 100, 1) if content_total > 0 else 0
    return pct, unknown_list


def generate_story(
    db: Session,
    difficulty: str = "beginner",
    max_sentences: int = 6,
    length: str = "medium",
    topic: str | None = None,
    format_type: str = "standard",
    existing_story_id: int | None = None,
) -> tuple["Story", list[int]]:
    """Generate a story using Opus with self-correction for 100% vocabulary compliance.

    Strategy: generate once, then iteratively correct unknown words rather than
    regenerating the entire story from scratch.

    format_type: standard, long, breakdown, arabic_explanation.

    If existing_story_id is provided, updates that row instead of creating a new one.

    Returns (story, new_lemma_ids).
    """
    known_words = _get_known_words(db)
    if not known_words:
        raise ValueError("No known/learning/acquiring words found. Learn some words first.")

    known_lemma_ids = {w["lemma_id"] for w in known_words}

    # Diversity: weighted sampling to avoid over-represented words
    content_word_counts = get_content_word_counts(db)
    sample = sample_known_words_weighted(
        known_words, content_word_counts, KNOWN_SAMPLE_SIZE,
    )
    avoid_words = get_avoid_words(content_word_counts, known_words)

    # Format vocabulary grouped by POS for better prompting
    from app.services.llm import format_known_words_by_pos
    vocab_list = format_known_words_by_pos(sample)
    # Full vocab list for correction rounds (LLM needs max options for replacements)
    full_vocab_list = format_known_words_by_pos(known_words)

    # Highlight acquiring words for reinforcement
    acquiring = [w for w in known_words if w.get("state") == "acquiring"]
    acquiring_section = ""
    if acquiring:
        acq_list = ", ".join(f"{w['arabic']} ({w['english']})" for w in acquiring[:15])
        acquiring_section = f"""
REINFORCEMENT WORDS (the reader is currently learning these — try to feature them prominently):
{acq_list}
"""

    # Override sentence count for long format
    LONG_SENTENCES = {"short": (8, 12), "medium": (12, 16), "long": (16, 20)}
    if format_type == "long":
        lo, hi = LONG_SENTENCES.get(length, LONG_SENTENCES["medium"])
    else:
        lo, hi = LENGTH_SENTENCES.get(length, LENGTH_SENTENCES["medium"])

    topic_line = f"\nTOPIC/THEME: Write the story about or inspired by: {topic}" if topic else ""
    genre = random.choice(STORY_GENRES)

    # Build lemma lookup for compliance checking (uses all lemmas for form matching)
    all_lemmas = _get_all_lemmas(db)
    lemma_lookup = build_lemma_lookup(all_lemmas)

    # Format-specific prompt additions
    format_extra = ""
    json_extra = ""
    if format_type == "arabic_explanation":
        format_extra = """
SPECIAL FORMAT: After the story, provide simple Arabic explanations for EACH sentence.
These explanations should be A1-level Arabic — very simple words, short sentences, as if explaining to a young child in Arabic.
Do NOT use English in the explanations.
"""
        json_extra = ', "explanation_ar": ["simple Arabic explanation for sentence 1", "...for sentence 2", ...]'
    elif format_type == "breakdown":
        format_extra = """
SPECIAL FORMAT: Write sentences that have a natural midpoint — they should be easy to split into two meaningful halves.
Each sentence should have at least 4 words so it can be split into a first-half and second-half for audio playback.
"""

    # Step 1: Generate the story
    prompt = f"""Write a {lo}-{hi} sentence mini-story in Arabic for a language learner.

GENRE: {genre}
{topic_line}
{acquiring_section}
{format_extra}
KNOWN VOCABULARY grouped by part of speech (use ONLY these plus function words — any conjugated form is fine):
{vocab_list}

CONSTRAINTS:
- Use ONLY words from the vocabulary list above and common function words
- Include full diacritics (tashkeel) on ALL Arabic words with correct i'rab
- The limited vocabulary is your creative constraint, not an excuse for a boring story
{f"- For variety, avoid these overused words: {'، '.join(avoid_words)}" if avoid_words else ""}

QUALITY BAR: Would an adult enjoy reading this? Would they smile, feel curious, or be surprised? If not, try harder. A five-sentence story with a great ending beats a long boring one.

Respond with JSON: {{"title_ar": "...", "title_en": "...", "body_ar": "full story in Arabic with diacritics", "body_en": "English translation", "transliteration": "ALA-LC transliteration"{json_extra}}}"""

    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt=STORY_SYSTEM_PROMPT,
            json_mode=True,
            temperature=0.9,
            model_override="opus",
            timeout=90,
            task_type="story_gen",
        )
    except AllProvidersFailed as e:
        raise ValueError(f"Story generation failed: {e}")

    body_ar = result.get("body_ar", "")
    if not body_ar:
        raise ValueError("Story generation returned empty body")

    # Step 2: Self-correction loop — ask LLM to fix unknown words in place
    for correction_round in range(MAX_CORRECTION_ROUNDS):
        compliance_pct, unknown = _check_story_compliance(
            body_ar, lemma_lookup, known_lemma_ids
        )
        logger.info(
            "Story compliance round %d: %.1f%% (%d unknown words: %s)",
            correction_round, compliance_pct, len(unknown),
            ", ".join(unknown[:10]),
        )

        if not unknown:
            break

        # Ask LLM to replace only the unknown words
        correction_prompt = f"""The following Arabic story uses words the reader doesn't know yet.

STORY:
{body_ar}

ENGLISH:
{result.get("body_en", "")}

UNKNOWN WORDS (the reader does NOT know these): {', '.join(unknown)}

FULL KNOWN VOCABULARY (use ONLY these plus function words — any conjugated form is fine):
{full_vocab_list}

TASK: Rewrite the story replacing ONLY the unknown words with synonyms or rephrased constructions using known vocabulary. Keep the story structure, diacritics, and meaning as close to the original as possible.

Respond with JSON: {{"title_ar": "...", "title_en": "...", "body_ar": "corrected story", "body_en": "updated English translation", "transliteration": "ALA-LC transliteration"}}"""

        try:
            corrected = generate_completion(
                prompt=correction_prompt,
                system_prompt=STORY_SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.3,
                model_override="opus",
                timeout=90,
                task_type="story_correction",
            )
        except AllProvidersFailed as e:
            logger.warning("Story correction round %d failed: %s", correction_round + 1, e)
            break

        if corrected.get("body_ar"):
            result = corrected
            body_ar = corrected["body_ar"]

    # Final compliance check
    final_pct, final_unknown = _check_story_compliance(
        body_ar, lemma_lookup, known_lemma_ids
    )
    logger.info(
        "Story final compliance: %.1f%% (%d unknown)",
        final_pct, len(final_unknown),
    )

    knowledge_map = _build_knowledge_map(db)

    # Build metadata for format-specific data
    metadata = None
    if format_type == "arabic_explanation" and result.get("explanation_ar"):
        metadata = {"explanation_ar": result["explanation_ar"]}

    if existing_story_id:
        story = db.query(Story).get(existing_story_id)
        if not story:
            raise ValueError(f"Story placeholder {existing_story_id} not found")
        story.title_ar = result.get("title_ar")
        story.title_en = result.get("title_en")
        story.body_ar = body_ar
        story.body_en = result.get("body_en")
        story.transliteration = result.get("transliteration")
        story.format_type = format_type
        story.metadata_json = metadata
        story.status = "active"
        db.flush()
    else:
        story = Story(
            title_ar=result.get("title_ar"),
            title_en=result.get("title_en"),
            body_ar=body_ar,
            body_en=result.get("body_en"),
            transliteration=result.get("transliteration"),
            source="generated",
            status="active",
            difficulty_level=difficulty,
            format_type=format_type,
            metadata_json=metadata,
        )
        db.add(story)
        db.flush()

    total, known, func = _create_story_words(
        db, story, body_ar, lemma_lookup, knowledge_map
    )

    # Safety net: import any remaining unknown words as lemmas (no ULK)
    new_ids = _import_unknown_words(db, story, lemma_lookup)

    _recalculate_story_counts(db, story)

    db.commit()
    db.refresh(story)

    log_interaction(
        event="story_generated",
        story_id=story.id,
        total_words=story.total_words,
        known_count=story.known_count,
        readiness_pct=story.readiness_pct,
        new_words_imported=len(new_ids),
        correction_rounds=min(correction_round + 1, MAX_CORRECTION_ROUNDS) if unknown else 0,
        final_compliance_pct=final_pct,
    )

    return story, new_ids


def _generate_title(arabic_text: str) -> dict:
    """Use LLM to generate Arabic and English titles for imported text."""
    snippet = arabic_text[:500]
    try:
        result = generate_completion(
            prompt=f"Give this Arabic text a short, evocative title (3-6 words) in both Arabic and English.\n\nText:\n{snippet}\n\nRespond with JSON: {{\"title_ar\": \"...\", \"title_en\": \"...\"}}",
            system_prompt="You generate short titles for Arabic texts. Include diacritics on the Arabic title. Respond with JSON only.",
            json_mode=True,
            task_type="story_title",
        )
        return {
            "title_ar": result.get("title_ar") or None,
            "title_en": result.get("title_en") or None,
        }
    except (AllProvidersFailed, Exception):
        return {"title_ar": None, "title_en": None}


def import_story(
    db: Session,
    arabic_text: str,
    title: str | None = None,
) -> tuple["Story", list[int]]:
    """Import an Arabic text and analyze its readiness.

    Returns (story, new_lemma_ids) — new_lemma_ids are Lemma IDs created during import.
    """
    all_lemmas = _get_all_lemmas(db)
    lemma_lookup = build_lemma_lookup(all_lemmas)
    knowledge_map = _build_knowledge_map(db)

    title_ar = title or None
    title_en = None
    if not title_ar:
        titles = _generate_title(arabic_text)
        title_ar = titles["title_ar"]
        title_en = titles["title_en"]

    story = Story(
        title_ar=title_ar,
        title_en=title_en,
        body_ar=arabic_text,
        source="imported",
        status="active",
    )
    db.add(story)
    db.flush()

    total, known, func = _create_story_words(
        db, story, arabic_text, lemma_lookup, knowledge_map
    )

    # Import unknown words (creates Lemma entries, no ULK)
    new_ids = _import_unknown_words(db, story, lemma_lookup)

    # Recalculate readiness now that unknown words have lemma_ids
    _recalculate_story_counts(db, story)

    db.commit()
    db.refresh(story)

    log_interaction(
        event="story_imported",
        story_id=story.id,
        total_words=story.total_words,
        known_count=story.known_count,
        readiness_pct=story.readiness_pct,
        new_words_imported=len(new_ids),
    )

    return story, new_ids


def _get_book_stats(db: Session, book_ids: list[int]) -> dict:
    """Batch-load sentence counts, sentences seen, and page readiness for book stories."""
    from sqlalchemy import func

    if not book_ids:
        return {}

    # Sentence counts and seen counts
    from sqlalchemy import case
    rows = (
        db.query(
            Sentence.story_id,
            func.count(Sentence.id),
            func.sum(case((Sentence.times_shown > 0, 1), else_=0)),
        )
        .filter(Sentence.story_id.in_(book_ids))
        .group_by(Sentence.story_id)
        .all()
    )
    sent_stats = {r[0]: {"total": r[1], "seen": int(r[2] or 0)} for r in rows}

    # Page readiness: get unique lemmas per page per story
    page_words = (
        db.query(StoryWord.story_id, StoryWord.page_number, StoryWord.lemma_id)
        .filter(
            StoryWord.story_id.in_(book_ids),
            StoryWord.page_number.isnot(None),
            StoryWord.is_function_word == False,
            StoryWord.lemma_id.isnot(None),
        )
        .all()
    )

    # Collect unique lemma_ids per (story, page)
    from collections import defaultdict
    page_lemmas: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    all_lemma_ids: set[int] = set()
    for sid, page, lid in page_words:
        page_lemmas[sid][page].add(lid)
        all_lemma_ids.add(lid)

    # Batch knowledge + acquisition timing lookup
    knowledge_map = _build_knowledge_map(db, lemma_ids=all_lemma_ids if all_lemma_ids else None)

    # Earliest review date per lemma — the true indicator of whether a word
    # was being studied before the book was imported (acquisition_started_at
    # can be reset by scripts like reset_ocr_cards.py)
    from app.models import ReviewLog
    first_review: dict[int, datetime] = {}
    if all_lemma_ids:
        from sqlalchemy import func as sa_func
        first_rev_rows = (
            db.query(ReviewLog.lemma_id, sa_func.min(ReviewLog.reviewed_at))
            .filter(ReviewLog.lemma_id.in_(all_lemma_ids))
            .group_by(ReviewLog.lemma_id)
            .all()
        )
        first_review = {r[0]: r[1] for r in first_rev_rows}

    # Map story_id -> created_at for distinguishing pre-existing vs new
    story_created: dict[int, datetime] = {}
    for s in db.query(Story).filter(Story.id.in_(book_ids)).all():
        if s.created_at:
            story_created[s.id] = s.created_at

    # total_encounters per lemma — fallback for words whose review_log was cleared
    enc_counts: dict[int, int] = {}
    if all_lemma_ids:
        enc_rows = (
            db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.total_encounters)
            .filter(UserLemmaKnowledge.lemma_id.in_(all_lemma_ids))
            .all()
        )
        enc_counts = {r[0]: r[1] or 0 for r in enc_rows}

    # Lemma source_story_id to identify words NOT created by this book
    lemma_story_ids: dict[int, int | None] = {}
    if all_lemma_ids:
        ls_rows = db.query(Lemma.lemma_id, Lemma.source_story_id).filter(
            Lemma.lemma_id.in_(all_lemma_ids)
        ).all()
        lemma_story_ids = {r[0]: r[1] for r in ls_rows}

    def _was_known_before_import(lid: int, sid: int, import_time: datetime | None) -> bool:
        """Check if a word was already being studied before the book was imported.

        Uses review_log first-review date as primary signal. Falls back to
        total_encounters for words whose review history was cleared by
        maintenance scripts (e.g. reset_ocr_cards.py).
        """
        if not import_time:
            return False
        fr = first_review.get(lid)
        if fr and fr < import_time:
            return True
        # Fallback: word existed before this book AND has many encounters
        if lemma_story_ids.get(lid) != sid and enc_counts.get(lid, 0) >= 5:
            return True
        return False

    # Build page readiness per story
    page_readiness: dict[int, list[dict]] = {}
    for sid in book_ids:
        if sid not in page_lemmas:
            continue
        import_time = story_created.get(sid)
        pages = []
        for page_num in sorted(page_lemmas[sid].keys()):
            lemmas = page_lemmas[sid][page_num]
            not_started = 0
            started_after_import = 0
            for lid in lemmas:
                if _was_known_before_import(lid, sid, import_time):
                    continue  # pre-existing knowledge, skip
                state = knowledge_map.get(lid)
                if state in _ACTIVELY_LEARNING_STATES:
                    started_after_import += 1
                else:
                    not_started += 1
            pages.append({
                "page": page_num,
                "new_words": not_started + started_after_import,
                "learned_words": started_after_import,
                "unlocked": not_started == 0,
            })
        page_readiness[sid] = pages

    # Deduplicated story-level counts (words that were new at import)
    story_word_stats: dict[int, dict] = {}
    for sid in book_ids:
        if sid not in page_lemmas:
            continue
        import_time = story_created.get(sid)
        all_story_lemmas = set()
        for page_lids in page_lemmas[sid].values():
            all_story_lemmas.update(page_lids)
        new_total = 0
        new_learning = 0
        for lid in all_story_lemmas:
            if _was_known_before_import(lid, sid, import_time):
                continue  # pre-existing knowledge
            state = knowledge_map.get(lid)
            if state in _ACTIVELY_LEARNING_STATES:
                new_total += 1
                new_learning += 1
            else:
                new_total += 1  # not started yet
        story_word_stats[sid] = {"new_total": new_total, "new_learning": new_learning}

    return {sid: {
        "sentence_count": sent_stats.get(sid, {}).get("total"),
        "sentences_seen": sent_stats.get(sid, {}).get("seen"),
        "page_readiness": page_readiness.get(sid),
        **(story_word_stats.get(sid, {})),
    } for sid in book_ids}


def get_book_page_detail(db: Session, story_id: int, page_number: int) -> dict:
    """Get detailed word and sentence info for a single page of a book story."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")
    if story.source != "book_ocr":
        raise ValueError(f"Story {story_id} is not a book import")

    # Words on this page (unique lemmas, non-function)
    page_words = (
        db.query(StoryWord)
        .filter(
            StoryWord.story_id == story_id,
            StoryWord.page_number == page_number,
            StoryWord.is_function_word == False,
            StoryWord.lemma_id.isnot(None),
        )
        .all()
    )

    seen_lemmas: set[int] = set()
    unique_words: list[StoryWord] = []
    for sw in page_words:
        if sw.lemma_id not in seen_lemmas:
            seen_lemmas.add(sw.lemma_id)
            unique_words.append(sw)

    # Batch fetch lemma + ULK info
    lemma_ids = list(seen_lemmas)
    lemmas_by_id: dict[int, Lemma] = {}
    if lemma_ids:
        for lem in db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all():
            lemmas_by_id[lem.lemma_id] = lem

    knowledge_map = _build_knowledge_map(db, lemma_ids=seen_lemmas if seen_lemmas else None)

    # Earliest review date per lemma — true indicator of pre-existing knowledge
    # (acquisition_started_at can be reset by maintenance scripts)
    from app.models import ReviewLog
    first_review: dict[int, datetime] = {}
    if seen_lemmas:
        from sqlalchemy import func as sa_func
        first_rev_rows = (
            db.query(ReviewLog.lemma_id, sa_func.min(ReviewLog.reviewed_at))
            .filter(ReviewLog.lemma_id.in_(seen_lemmas))
            .group_by(ReviewLog.lemma_id)
            .all()
        )
        first_review = {r[0]: r[1] for r in first_rev_rows}

    # total_encounters + lemma source fallback (for words with cleared review history)
    enc_counts: dict[int, int] = {}
    if seen_lemmas:
        enc_rows = (
            db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.total_encounters)
            .filter(UserLemmaKnowledge.lemma_id.in_(seen_lemmas))
            .all()
        )
        enc_counts = {r[0]: r[1] or 0 for r in enc_rows}

    import_time = story.created_at
    known_at_import = 0
    new_not_started = 0
    new_learning = 0
    words_out = []
    for sw in unique_words:
        lem = lemmas_by_id.get(sw.lemma_id)
        state = knowledge_map.get(sw.lemma_id)
        # Was this word already being studied before the book was imported?
        fr = first_review.get(sw.lemma_id)
        was_known_before = (
            fr is not None
            and import_time is not None
            and fr < import_time
        )
        # Fallback: word not created by this book AND encountered many times
        if not was_known_before and lem and lem.source_story_id != story_id:
            if enc_counts.get(sw.lemma_id, 0) >= 5:
                was_known_before = True
        is_new = not was_known_before
        if was_known_before:
            known_at_import += 1
        elif state in _ACTIVELY_LEARNING_STATES:
            new_learning += 1
        else:
            new_not_started += 1
        words_out.append({
            "lemma_id": sw.lemma_id,
            "arabic": lem.lemma_ar_bare if lem else sw.surface_form,
            "gloss_en": sw.gloss_en or (lem.gloss_en if lem else None),
            "transliteration": lem.transliteration_ala_lc if lem else None,
            "knowledge_state": state,
            "is_new": is_new,
        })

    # Sentences on this page
    page_sentences = (
        db.query(Sentence)
        .filter(
            Sentence.story_id == story_id,
            Sentence.page_number == page_number,
        )
        .order_by(Sentence.id)
        .all()
    )

    sentences_out = [
        {
            "id": s.id,
            "arabic_diacritized": s.arabic_text,
            "english_translation": s.english_translation,
            "seen": (s.times_shown or 0) > 0,
        }
        for s in page_sentences
    ]

    return {
        "story_id": story_id,
        "page_number": page_number,
        "story_title_en": story.title_en,
        "known_count": known_at_import,
        "new_not_started": new_not_started,
        "new_learning": new_learning,
        "words": words_out,
        "sentences": sentences_out,
    }


def get_stories(db: Session) -> list[dict]:
    """Return all non-deleted stories ordered by created_at desc."""
    stories = (
        db.query(Story)
        .filter(Story.status.notin_(HIDDEN_STORY_STATUSES))
        .order_by(Story.created_at.desc())
        .all()
    )

    book_ids = [s.id for s in stories if s.source == "book_ocr"]
    book_stats = _get_book_stats(db, book_ids)

    return [
        {
            "id": s.id,
            "title_ar": s.title_ar,
            "title_en": s.title_en,
            "source": s.source,
            "status": s.status,
            "readiness_pct": s.readiness_pct or 0,
            "unknown_count": s.unknown_count or 0,
            "total_words": s.total_words or 0,
            "format_type": s.format_type or "standard",
            "archived_at": s.archived_at.isoformat() if s.archived_at else None,
            "audio_filename": s.audio_filename,
            "page_count": s.page_count,
            **(book_stats.get(s.id, {}) if s.source == "book_ocr" else {}),
            "created_at": s.created_at.isoformat() if s.created_at else "",
        }
        for s in stories
    ]


def get_story_detail(db: Session, story_id: int) -> dict:
    """Get story with all words and current knowledge state."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story or story.status == "deleted":
        raise ValueError(f"Story {story_id} not found")

    if story.status in ("generating", "failed"):
        return {
            "id": story.id,
            "title_ar": story.title_ar,
            "title_en": story.title_en,
            "body_ar": story.body_ar or "",
            "body_en": story.body_en,
            "transliteration": story.transliteration,
            "source": story.source,
            "status": story.status,
            "readiness_pct": 0,
            "unknown_count": 0,
            "total_words": 0,
            "known_count": 0,
            "format_type": story.format_type or "standard",
            "archived_at": story.archived_at.isoformat() if story.archived_at else None,
            "audio_filename": story.audio_filename,
            "voice_id": story.voice_id,
            "page_count": None,
            "created_at": story.created_at.isoformat() if story.created_at else "",
            "words": [],
        }

    # Recalculate counts live (fixes stale counts + re-checks function word flags)
    _recalculate_story_counts(db, story)
    try:
        db.commit()
    except Exception:
        # Best-effort cache update — don't crash the read endpoint if DB is locked
        db.rollback()

    story_lemma_ids = {sw.lemma_id for sw in story.words if sw.lemma_id}
    knowledge_map = _build_knowledge_map(db, lemma_ids=story_lemma_ids or None)
    cold_unknown, warm_unknown, reading_readiness_pct = _compute_cold_warm_counts(
        db, story_lemma_ids, knowledge_map,
        story.known_count or 0, story.total_words or 0,
    )

    words = []
    for sw in story.words:
        is_known = False
        if sw.lemma_id:
            state = knowledge_map.get(sw.lemma_id)
            is_known = state in _ACTIVELY_LEARNING_STATES

        words.append({
            "position": sw.position,
            "surface_form": sw.surface_form,
            "lemma_id": sw.lemma_id,
            "gloss_en": sw.gloss_en,
            "is_known": is_known or bool(sw.is_function_word),
            "is_function_word": bool(sw.is_function_word),
            "name_type": sw.name_type,
            "sentence_index": sw.sentence_index or 0,
        })

    # Book-specific stats
    book_extra = {}
    if story.source == "book_ocr":
        stats = _get_book_stats(db, [story.id])
        book_extra = stats.get(story.id, {})

    return {
        "id": story.id,
        "title_ar": story.title_ar,
        "title_en": story.title_en,
        "body_ar": story.body_ar,
        "body_en": story.body_en,
        "transliteration": story.transliteration,
        "source": story.source,
        "status": story.status,
        "readiness_pct": story.readiness_pct or 0,
        "unknown_count": story.unknown_count or 0,
        "total_words": story.total_words or 0,
        "known_count": story.known_count or 0,
        "cold_unknown_count": cold_unknown,
        "warm_unknown_count": warm_unknown,
        "reading_readiness_pct": reading_readiness_pct,
        "format_type": story.format_type or "standard",
        "archived_at": story.archived_at.isoformat() if story.archived_at else None,
        "audio_filename": story.audio_filename,
        "voice_id": story.voice_id,
        "page_count": story.page_count,
        **book_extra,
        "created_at": story.created_at.isoformat() if story.created_at else "",
        "words": words,
    }


def complete_story(
    db: Session,
    story_id: int,
    looked_up_lemma_ids: list[int],
    reading_time_ms: int | None = None,
) -> dict:
    """Mark story as completed and submit FSRS reviews for all words."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    if story.status == "completed":
        return {
            "story_id": story_id,
            "status": "completed",
            "words_reviewed": 0,
            "good_count": 0,
            "again_count": 0,
            "duplicate": True,
        }
    if story.status in TERMINAL_STORY_STATUSES:
        return {
            "story_id": story_id,
            "status": story.status,
            "words_reviewed": 0,
            "good_count": 0,
            "again_count": 0,
            "duplicate": True,
            "conflict": True,
        }

    looked_up_set = set(looked_up_lemma_ids)
    reviewed_lemmas: set[int] = set()
    good_count = 0
    again_count = 0
    encountered_count = 0

    # Pre-fetch all ULK records for story words
    story_lemma_ids = {sw.lemma_id for sw in story.words if sw.lemma_id and not sw.is_function_word}
    ulk_map = {}
    if story_lemma_ids:
        ulks = db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id.in_(story_lemma_ids)
        ).all()
        ulk_map = {u.lemma_id: u for u in ulks}

    for sw in story.words:
        if not sw.lemma_id or sw.is_function_word or sw.lemma_id in reviewed_lemmas:
            continue

        reviewed_lemmas.add(sw.lemma_id)
        ulk = ulk_map.get(sw.lemma_id)

        if not ulk:
            # No existing knowledge — create encountered record (no FSRS card)
            new_ulk = UserLemmaKnowledge(
                lemma_id=sw.lemma_id,
                knowledge_state="encountered",
                fsrs_card_json=None,
                source="encountered",
                total_encounters=1,
            )
            db.add(new_ulk)
            encountered_count += 1
            continue

        if ulk.knowledge_state == "encountered":
            # Already encountered but no FSRS card — just increment encounters
            ulk.total_encounters = (ulk.total_encounters or 0) + 1
            encountered_count += 1
            continue

        if ulk.knowledge_state == "suspended":
            continue

        # Has active FSRS card — submit real review
        if sw.lemma_id in looked_up_set:
            rating = 1
            again_count += 1
        else:
            rating = 3
            good_count += 1

        submit_review(
            db,
            lemma_id=sw.lemma_id,
            rating_int=rating,
            review_mode="reading",
            comprehension_signal="story_complete",
            client_review_id=f"story:{story_id}:complete:{sw.lemma_id}",
            commit=False,
        )

    story.status = "completed"
    story.completed_at = datetime.now(timezone.utc)
    db.commit()

    log_interaction(
        event="story_completed",
        story_id=story_id,
        good_count=good_count,
        again_count=again_count,
        words_reviewed=len(reviewed_lemmas),
        words_looked_up=len(looked_up_set),
        reading_time_ms=reading_time_ms,
    )

    return {
        "story_id": story_id,
        "status": "completed",
        "words_reviewed": len(reviewed_lemmas),
        "good_count": good_count,
        "again_count": again_count,
    }


def suspend_story(db: Session, story_id: int) -> dict:
    """Toggle story suspension. Suspended stories are hidden from the active list."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    if story.status == "suspended":
        story.status = "active"
        db.commit()
        log_interaction(event="story_reactivated", story_id=story_id)
        return {"story_id": story_id, "status": "active"}

    story.status = "suspended"
    db.commit()
    log_interaction(event="story_suspended", story_id=story_id)
    return {"story_id": story_id, "status": "suspended"}


def delete_story(db: Session, story_id: int) -> dict:
    """Soft-delete a story (set status to 'deleted')."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    story.status = "deleted"
    db.commit()

    log_interaction(event="story_deleted", story_id=story_id)

    return {"story_id": story_id, "deleted": True}


def archive_story(db: Session, story_id: int) -> dict:
    """Toggle story archive state."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    if story.archived_at:
        story.archived_at = None
        db.commit()
        log_interaction(event="story_unarchived", story_id=story_id)
        return {"story_id": story_id, "archived": False}

    story.archived_at = datetime.now(timezone.utc)
    db.commit()
    log_interaction(event="story_archived", story_id=story_id)
    return {"story_id": story_id, "archived": True}


def mark_story_heard(db: Session, story_id: int) -> dict:
    """Increment times_heard for all non-function words in a story.

    This is passive listening credit — no FSRS reviews are submitted.
    """
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    story_lemma_ids = {sw.lemma_id for sw in story.words if sw.lemma_id and not sw.is_function_word}
    if not story_lemma_ids:
        return {"story_id": story_id, "words_heard": 0}

    ulks = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id.in_(story_lemma_ids)
    ).all()

    heard_count = 0
    for ulk in ulks:
        ulk.times_heard = (ulk.times_heard or 0) + 1
        heard_count += 1

    db.commit()

    log_interaction(
        event="story_heard",
        story_id=story_id,
        words_heard=heard_count,
    )

    return {"story_id": story_id, "words_heard": heard_count}


async def generate_story_audio(db: Session, story_id: int) -> dict:
    """Generate TTS audio for a story, using the podcast segment stitching pipeline.

    Audio format depends on story.format_type:
    - standard/long: read full body_ar at learner speed
    - breakdown: per sentence — first half, pause, full sentence, then full story
    - arabic_explanation: per sentence — Arabic, pause, simple Arabic explanation
    """
    import asyncio
    import io
    from pathlib import Path

    from app.services.tts import (
        STORY_AUDIO_DIR,
        generate_audio,
        pick_voice_for_story,
        DEFAULT_VOICE_SETTINGS,
        DEFAULT_MODEL,
    )

    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    if story.audio_filename:
        existing = STORY_AUDIO_DIR / story.audio_filename
        if existing.exists():
            return {"story_id": story_id, "audio_filename": story.audio_filename, "cached": True}

    voice = pick_voice_for_story(story_id)
    voice_id = voice["id"]
    format_type = story.format_type or "standard"

    # Split story into sentences
    sentences = [s.strip() for s in story.body_ar.split(".") if s.strip()]
    if not sentences:
        sentences = [story.body_ar]

    # Better sentence splitting: use Arabic period (.) and newlines
    import re
    sentences = [s.strip() for s in re.split(r'[.\n]', story.body_ar) if s.strip()]
    if not sentences:
        sentences = [story.body_ar]

    # Build audio segments
    try:
        from pydub import AudioSegment as PydubSegment
    except ImportError:
        raise ValueError("pydub not installed — needed for audio stitching")

    STORY_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    settings = dict(DEFAULT_VOICE_SETTINGS)
    settings["speed"] = 0.75  # learner speed for stories

    final = PydubSegment.empty()

    async def tts(text: str, speed: float = 0.75) -> PydubSegment:
        s = dict(DEFAULT_VOICE_SETTINGS)
        s["speed"] = speed
        audio_bytes = await generate_audio(text, voice_id, voice_settings=s)
        return PydubSegment.from_mp3(io.BytesIO(audio_bytes))

    silence_1s = PydubSegment.silent(duration=1000)
    silence_1_5s = PydubSegment.silent(duration=1500)
    silence_2s = PydubSegment.silent(duration=2000)

    if format_type in ("standard", "long"):
        # Read full story at learner speed
        clip = await tts(story.body_ar, speed=0.75)
        final += clip

    elif format_type == "breakdown":
        # Per sentence: first half → pause → full sentence
        for sent in sentences:
            words = sent.split()
            mid = len(words) // 2
            if mid > 0:
                first_half = " ".join(words[:mid])
                clip_half = await tts(first_half, speed=0.7)
                final += clip_half
                final += silence_1_5s
            clip_full = await tts(sent, speed=0.75)
            final += clip_full
            final += silence_1s

        # Then full story at normal speed
        final += silence_2s
        clip_full_story = await tts(story.body_ar, speed=0.9)
        final += clip_full_story

    elif format_type == "arabic_explanation":
        # Per sentence: Arabic → pause → simple Arabic explanation
        explanations = []
        if story.metadata_json and isinstance(story.metadata_json, dict):
            explanations = story.metadata_json.get("explanation_ar", [])

        for i, sent in enumerate(sentences):
            clip_ar = await tts(sent, speed=0.75)
            final += clip_ar
            final += silence_1s

            if i < len(explanations) and explanations[i]:
                clip_explain = await tts(explanations[i], speed=0.8)
                final += clip_explain
                final += silence_2s
            else:
                final += silence_1s

    else:
        # Fallback: just read the story
        clip = await tts(story.body_ar, speed=0.75)
        final += clip

    # Export
    filename = f"story_{story_id}.mp3"
    output_path = STORY_AUDIO_DIR / filename
    final.export(str(output_path), format="mp3", bitrate="128k")

    duration_s = len(final) / 1000
    logger.info(
        "Story audio saved: %s (%.1f min, %.1f MB)",
        filename, duration_s / 60, output_path.stat().st_size / 1e6,
    )

    # Update story record
    story.audio_filename = filename
    story.voice_id = voice_id
    db.commit()

    log_interaction(
        event="story_audio_generated",
        story_id=story_id,
        voice=voice["name"],
        duration_s=round(duration_s, 1),
        format_type=format_type,
    )

    return {
        "story_id": story_id,
        "audio_filename": filename,
        "voice": voice["name"],
        "duration_s": round(duration_s, 1),
    }


def lookup_word(
    db: Session,
    story_id: int,
    lemma_id: int,
    position: int,
) -> dict:
    """Look up a word's details during story reading."""
    lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
    if not lemma:
        raise ValueError(f"Lemma {lemma_id} not found")

    root_str = None
    if lemma.root:
        root_str = lemma.root.root

    surface_form = None
    story_word = (
        db.query(StoryWord)
        .filter(StoryWord.story_id == story_id, StoryWord.position == position)
        .first()
    )
    if story_word:
        surface_form = story_word.surface_form

    log_interaction(
        event="story_word_lookup",
        lemma_id=lemma_id,
        story_id=story_id,
        position=position,
        surface_form=surface_form,
    )

    return {
        "lemma_id": lemma.lemma_id,
        "lemma_ar": lemma.lemma_ar,
        "gloss_en": lemma.gloss_en,
        "transliteration": lemma.transliteration_ala_lc,
        "root": root_str,
        "pos": lemma.pos,
    }


def recalculate_readiness(db: Session, story_id: int) -> dict:
    """Re-check each word's current knowledge state and update readiness.

    Delegates to _recalculate_story_counts for consistent deduplication and
    function word re-checking, then builds the unknown_words list.
    """
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    _recalculate_story_counts(db, story)

    # Build unknown_words list (deduplicated by lemma_id)
    story_lemma_ids = {sw.lemma_id for sw in story.words if sw.lemma_id}
    knowledge_map = _build_knowledge_map(db, lemma_ids=story_lemma_ids or None)
    seen_lemmas: set[int] = set()
    unknown_words = []
    for sw in story.words:
        if sw.is_function_word:
            continue
        if not sw.lemma_id or sw.lemma_id in seen_lemmas:
            continue
        seen_lemmas.add(sw.lemma_id)
        state = knowledge_map.get(sw.lemma_id)
        if state not in _ACTIVELY_LEARNING_STATES:
            unknown_words.append({
                "position": sw.position,
                "surface_form": sw.surface_form,
                "lemma_id": sw.lemma_id,
            })

    db.commit()

    return {
        "readiness_pct": story.readiness_pct,
        "unknown_count": story.unknown_count,
        "unknown_words": unknown_words,
    }
