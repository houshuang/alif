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
    FUNCTION_WORDS,
    build_lemma_lookup,
    normalize_alef,
    strip_diacritics,
    strip_tatweel,
    tokenize,
    _is_function_word,
    lookup_lemma,
)
from app.services.morphology import find_best_db_match, get_word_features, is_valid_root

logger = logging.getLogger(__name__)

KNOWN_SAMPLE_SIZE = 500
MAX_NEW_WORDS_IN_STORY = 5
MAX_STORY_RETRIES = 3
STORY_COMPLIANCE_THRESHOLD = 70.0
TERMINAL_STORY_STATUSES = {"completed"}

STORY_SYSTEM_PROMPT = f"""\
You are a creative Arabic storyteller writing for language learners. Write genuinely \
engaging mini-stories in MSA (fusha) with a real narrative arc, characters, and a satisfying ending.

CRITICAL: Write a COHESIVE STORY with beginning, middle, and end. Every sentence must \
connect to the previous one and advance the narrative.

{ARABIC_STYLE_RULES}

Story craft:
- Give the main character a name and a situation/problem
- Build tension or curiosity
- End with a twist, punchline, resolution, or poetic moment
- Use dialogue (with قَالَ/قَالَتْ) when it serves the story
- Use VSO for narration (ذَهَبَ الرَّجُلُ), SVO for emphasis/contrast (الرَّجُلُ ذَهَبَ وَحْدَهُ)
- Nominal sentences for scene-setting (اللَّيْلُ طَوِيلٌ)

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
    """Fetch all known/learning/acquiring words with their lemma info.

    Includes acquiring words (Leitner box 1-3) — these are being actively
    learned and should be available for story vocabulary.
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
    ]


def _get_all_lemmas(db: Session) -> list:
    """Fetch all lemmas for lookup building."""
    return db.query(Lemma).all()


def _tokenize_story(text: str) -> list[str]:
    """Tokenize story text, preserving sentence boundaries via index tracking."""
    return tokenize(text)


def _create_story_words(
    db: Session,
    story: Story,
    body_ar: str,
    lemma_lookup: dict[str, int],
    knowledge_map: dict[int, str],
) -> tuple[int, int, int]:
    """Create StoryWord records for each token in the story.

    Returns (total_words, known_count, function_word_count).
    """
    # Split on periods and newlines to preserve poem/verse formatting
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
        tokens = _tokenize_story(sentence_text)
        for token in tokens:
            bare = strip_diacritics(token)
            bare_clean = strip_tatweel(bare)
            bare_norm = normalize_alef(bare_clean)
            if not _is_function_word(bare_clean):
                lid = lookup_lemma(bare_norm, lemma_lookup)
                if not lid and bare_norm not in morph_cache:
                    match = find_best_db_match(bare_clean, known_bare_forms)
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
        tokens = _tokenize_story(sentence_text)
        for token in tokens:
            bare = strip_diacritics(token)
            bare_clean = strip_tatweel(bare)
            bare_norm = normalize_alef(bare_clean)

            is_func = _is_function_word(bare_clean)
            lemma_id = None if is_func else lookup_lemma(bare_norm, lemma_lookup)
            if not lemma_id and not is_func:
                lemma_id = morph_cache.get(bare_norm)

            is_known = False
            if lemma_id:
                state = knowledge_map.get(lemma_id)
                is_known = state in ("learning", "known")

            gloss = None
            if lemma_id:
                lemma = lemma_by_id.get(lemma_id)
                if lemma:
                    gloss = lemma.gloss_en

            sw = StoryWord(
                story_id=story.id,
                position=position,
                surface_form=token,
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

    Returns list of newly created lemma_ids.
    """
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

    # Step 1: CAMeL morphological analysis for each unknown word
    word_analyses: list[dict] = []
    for sw in unknown_words:
        features = get_word_features(sw.surface_form)
        lex_bare = strip_diacritics(features.get("lex", sw.surface_form))
        lex_norm = normalize_alef(lex_bare)
        # Check if the base lemma from CAMeL already exists in DB
        existing_id = lemma_lookup.get(lex_norm)
        if existing_id:
            # CAMeL resolved it to a known lemma — update StoryWord
            sw.lemma_id = existing_id
            lemma = db.query(Lemma).filter(Lemma.lemma_id == existing_id).first()
            if lemma:
                sw.gloss_en = lemma.gloss_en
            continue
        word_analyses.append({
            "story_word": sw,
            "surface": sw.surface_form,
            "lex": features.get("lex", sw.surface_form),
            "lex_bare": lex_bare,
            "lex_norm": lex_norm,
            "root": features.get("root"),
            "pos": features.get("pos", "UNK"),
        })

    if not word_analyses:
        db.flush()
        return []

    # Step 2: LLM batch translation — use lex (base form) not surface form
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
        )

        # Result may be a list or a dict with a list inside
        items = result if isinstance(result, list) else result.get("words", result.get("translations", []))
        if isinstance(items, list):
            for item in items:
                arabic = item.get("arabic", "")
                bare = normalize_alef(strip_diacritics(arabic))
                gloss_map[bare] = {
                    "english": item.get("english", ""),
                    "pos": item.get("pos"),
                    "name_type": item.get("name_type"),
                }
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

    # Step 3: Create Root + Lemma entries (skip proper nouns)
    new_lemma_ids: list[int] = []
    for analysis in word_analyses:
        lex_norm = analysis["lex_norm"]
        lex_bare = analysis["lex_bare"]
        sw = analysis["story_word"]

        # Get gloss from LLM (fall back to empty)
        surface_bare = normalize_alef(strip_diacritics(sw.surface_form))
        gloss_data = gloss_map.get(surface_bare, gloss_map.get(lex_norm, {}))
        english = gloss_data.get("english", "")
        pos = gloss_data.get("pos") or analysis["pos"]
        name_type = gloss_data.get("name_type")
        if pos == "UNK":
            pos = None

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

        # Find or create root
        root_id = None
        root_str = analysis.get("root")
        if root_str and is_valid_root(root_str):
            existing_root = db.query(Root).filter(Root.root == root_str).first()
            if existing_root:
                root_id = existing_root.root_id
            else:
                new_root = Root(root=root_str, core_meaning_en="")
                db.add(new_root)
                db.flush()
                root_id = new_root.root_id

        # Dedup check: another word in this batch may have created the same lemma
        if lex_norm in lemma_lookup:
            existing_id = lemma_lookup[lex_norm]
            sw.lemma_id = existing_id
            lemma = db.query(Lemma).filter(Lemma.lemma_id == existing_id).first()
            if lemma:
                sw.gloss_en = lemma.gloss_en
            continue

        word_cat = _category_by_bare.get(lex_bare)
        # Prefix gloss for proper names so it's clear during review
        lemma_gloss = english
        if word_cat == "proper_name" and english and not english.startswith("(name)"):
            lemma_gloss = f"(name) {english}"

        # Generate transliteration inline (deterministic, instant)
        from app.services.transliteration import transliterate_lemma
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

    # Step 4: Run variant detection on new lemmas
    if new_lemma_ids:
        try:
            from app.services.variant_detection import (
                detect_variants_llm,
                detect_definite_variants,
                mark_variants,
            )
            camel_vars = detect_variants_llm(db, lemma_ids=new_lemma_ids)
            already = {v[0] for v in camel_vars}
            def_vars = detect_definite_variants(db, lemma_ids=new_lemma_ids, already_variant_ids=already)
            all_vars = camel_vars + def_vars
            if all_vars:
                mark_variants(db, all_vars)
        except Exception as e:
            logger.warning("Variant detection failed for story %d: %s", story.id, e)

    return new_lemma_ids


_ACTIVELY_LEARNING_STATES = {"acquiring", "learning", "known", "lapsed"}


def _recalculate_story_counts(db: Session, story: Story) -> None:
    """Recalculate total_words, known_count, unknown_count, readiness_pct from StoryWords."""
    story_lemma_ids = {sw.lemma_id for sw in story.words if sw.lemma_id}
    knowledge_map = _build_knowledge_map(db, lemma_ids=story_lemma_ids or None)
    total = 0
    known = 0
    func = 0
    for sw in story.words:
        total += 1
        if sw.is_function_word:
            func += 1
        elif sw.lemma_id and knowledge_map.get(sw.lemma_id) in _ACTIVELY_LEARNING_STATES:
            known += 1
    story.total_words = total
    story.known_count = known
    story.unknown_count = total - known - func
    story.readiness_pct = round((known + func) / total * 100, 1) if total > 0 else 0


def _build_knowledge_map(db: Session, lemma_ids: set[int] | None = None) -> dict[int, str]:
    """Build lemma_id -> knowledge_state map."""
    q = db.query(UserLemmaKnowledge)
    if lemma_ids is not None:
        q = q.filter(UserLemmaKnowledge.lemma_id.in_(lemma_ids))
    rows = q.all()
    return {r.lemma_id: r.knowledge_state for r in rows}


LENGTH_SENTENCES = {"short": (2, 4), "medium": (4, 7), "long": (7, 12)}

STORY_GENRES = [
    "a funny story with a punchline at the end",
    "a mystery — something is not what it seems",
    "a heartwarming story about an unexpected friendship",
    "a story with an ironic twist ending",
    "a short adventure with a moment of danger",
    "a story where someone learns a surprising lesson",
    "a story with a philosophical observation about daily life",
    "a story where a misunderstanding leads to an unexpected outcome",
]


def _check_story_compliance(
    body_ar: str,
    lemma_lookup: dict[str, int],
) -> tuple[float, list[str]]:
    """Check vocabulary compliance of generated story text.

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
        if lookup_lemma(bare, lemma_lookup):
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
) -> tuple["Story", list[int]]:
    """Generate a story using Opus with retry loop for vocabulary compliance.

    Returns (story, new_lemma_ids).
    """
    known_words = _get_known_words(db)
    if not known_words:
        raise ValueError("No known/learning/acquiring words found. Learn some words first.")

    # Diversity: weighted sampling to avoid over-represented words
    content_word_counts = get_content_word_counts(db)
    sample = sample_known_words_weighted(
        known_words, content_word_counts, KNOWN_SAMPLE_SIZE,
    )
    avoid_words = get_avoid_words(content_word_counts, known_words)

    # Format vocabulary grouped by POS for better prompting
    from app.services.llm import format_known_words_by_pos
    vocab_list = format_known_words_by_pos(sample)

    # Highlight acquiring words for reinforcement
    acquiring = [w for w in known_words if w.get("state") == "acquiring"]
    acquiring_section = ""
    if acquiring:
        acq_list = ", ".join(f"{w['arabic']} ({w['english']})" for w in acquiring[:15])
        acquiring_section = f"""
REINFORCEMENT WORDS (the reader is currently learning these — try to feature them prominently):
{acq_list}
"""

    lo, hi = LENGTH_SENTENCES.get(length, LENGTH_SENTENCES["medium"])
    topic_line = f"\nTOPIC/THEME: Write the story about or inspired by: {topic}" if topic else ""
    genre = random.choice(STORY_GENRES)

    # Build lemma lookup for compliance checking (uses all lemmas for form matching)
    all_lemmas = _get_all_lemmas(db)
    compliance_lookup = build_lemma_lookup(all_lemmas)

    best_result = None
    best_compliance = 0

    for attempt in range(MAX_STORY_RETRIES):
        retry_section = ""
        if attempt > 0 and best_result:
            _, unknown = _check_story_compliance(best_result.get("body_ar", ""), compliance_lookup)
            if unknown:
                retry_section = f"""
IMPORTANT CORRECTION: Your previous attempt used words NOT in the vocabulary list.
These words are NOT allowed: {', '.join(unknown[:20])}
Replace them with synonyms from the vocabulary list, or restructure sentences to avoid them.
"""

        prompt = f"""{retry_section}Write a cohesive mini-story ({lo}-{hi} sentences) for a {difficulty} Arabic learner.

GENRE: {genre}
{topic_line}
{acquiring_section}
KNOWN VOCABULARY grouped by part of speech (use ONLY these plus function words):
{vocab_list}

RULES:
- Use ONLY words from the vocabulary list above (any conjugated form is fine)
- Write a REAL STORY with narrative arc: setup → development → resolution/punchline
- Give the main character a name
- Include full diacritics (tashkeel) on ALL Arabic words
- Make it genuinely interesting — an adult should enjoy reading it
- Every sentence must connect to the next
{f"- For variety, try NOT to use these overused words: {'، '.join(avoid_words)}" if avoid_words else ""}
Respond with JSON: {{"title_ar": "...", "title_en": "...", "body_ar": "full story in Arabic with diacritics", "body_en": "English translation", "transliteration": "ALA-LC transliteration"}}"""

        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=STORY_SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.9,
                model_override="opus",
                timeout=90,
            )
        except AllProvidersFailed as e:
            logger.warning("Story generation attempt %d failed: %s", attempt + 1, e)
            continue

        body_ar = result.get("body_ar", "")
        if not body_ar:
            continue

        compliance_pct, unknown = _check_story_compliance(body_ar, compliance_lookup)
        logger.info(
            "Story attempt %d: compliance=%.1f%% unknown=%d words",
            attempt + 1, compliance_pct, len(unknown),
        )

        if compliance_pct > best_compliance:
            best_compliance = compliance_pct
            best_result = result

        if compliance_pct >= STORY_COMPLIANCE_THRESHOLD:
            break

    if not best_result or not best_result.get("body_ar"):
        raise ValueError("Failed to generate story after all attempts")

    result = best_result
    body_ar = result.get("body_ar", "")

    lemma_lookup = compliance_lookup
    knowledge_map = _build_knowledge_map(db)

    story = Story(
        title_ar=result.get("title_ar"),
        title_en=result.get("title_en"),
        body_ar=body_ar,
        body_en=result.get("body_en"),
        transliteration=result.get("transliteration"),
        source="generated",
        status="active",
        difficulty_level=difficulty,
    )
    db.add(story)
    db.flush()

    total, known, func = _create_story_words(
        db, story, body_ar, lemma_lookup, knowledge_map
    )

    # Import unknown words (creates Lemma entries, no ULK)
    new_ids = _import_unknown_words(db, story, lemma_lookup)

    # Recalculate readiness now that unknown words have lemma_ids
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
            "arabic_diacritized": s.arabic_diacritized or s.arabic_text,
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
    """Return all stories ordered by created_at desc."""
    stories = db.query(Story).order_by(Story.created_at.desc()).all()

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
            "page_count": s.page_count,
            **(book_stats.get(s.id, {}) if s.source == "book_ocr" else {}),
            "created_at": s.created_at.isoformat() if s.created_at else "",
        }
        for s in stories
    ]


def get_story_detail(db: Session, story_id: int) -> dict:
    """Get story with all words and current knowledge state."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    story_lemma_ids = {sw.lemma_id for sw in story.words if sw.lemma_id}
    knowledge_map = _build_knowledge_map(db, lemma_ids=story_lemma_ids or None)

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
    """Permanently delete a story and its words."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    db.query(StoryWord).filter(StoryWord.story_id == story_id).delete()
    db.delete(story)
    db.commit()

    log_interaction(event="story_deleted", story_id=story_id)

    return {"story_id": story_id, "deleted": True}


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
    """Re-check each word's current knowledge state and update readiness."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    story_lemma_ids = {sw.lemma_id for sw in story.words if sw.lemma_id}
    knowledge_map = _build_knowledge_map(db, lemma_ids=story_lemma_ids or None)

    total = 0
    known = 0
    func = 0
    unknown_words = []

    for sw in story.words:
        total += 1
        if sw.is_function_word:
            func += 1
            continue
        if sw.lemma_id:
            state = knowledge_map.get(sw.lemma_id)
            if state in _ACTIVELY_LEARNING_STATES:
                known += 1
            else:
                unknown_words.append({
                    "position": sw.position,
                    "surface_form": sw.surface_form,
                    "lemma_id": sw.lemma_id,
                })
        else:
            unknown_words.append({
                "position": sw.position,
                "surface_form": sw.surface_form,
                "lemma_id": None,
            })

    pct = round((known + func) / total * 100, 1) if total > 0 else 0
    story.readiness_pct = pct
    story.known_count = known
    story.unknown_count = total - known - func
    db.commit()

    return {
        "readiness_pct": pct,
        "unknown_count": len(unknown_words),
        "unknown_words": unknown_words,
    }
