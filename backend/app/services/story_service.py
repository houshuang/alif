"""Story generation, import, and review service.

Handles generating stories from known vocabulary via LLM,
importing external Arabic text with readiness analysis,
and managing story completion with FSRS credit.
"""

import random
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Lemma, UserLemmaKnowledge, Story, StoryWord
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
from app.services.morphology import find_best_db_match

KNOWN_SAMPLE_SIZE = 80
MAX_NEW_WORDS_IN_STORY = 5
TERMINAL_STORY_STATUSES = {"completed", "skipped", "too_difficult"}

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
    """Fetch all known/learning words with their lemma info."""
    rows = (
        db.query(Lemma, UserLemmaKnowledge)
        .join(UserLemmaKnowledge, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.knowledge_state.in_(["learning", "known"]))
        .all()
    )
    return [
        {
            "lemma_id": lemma.lemma_id,
            "arabic": lemma.lemma_ar,
            "arabic_bare": lemma.lemma_ar_bare,
            "english": lemma.gloss_en or "",
        }
        for lemma, _ulk in rows
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


def _build_knowledge_map(db: Session) -> dict[int, str]:
    """Build lemma_id -> knowledge_state map."""
    rows = db.query(UserLemmaKnowledge).all()
    return {r.lemma_id: r.knowledge_state for r in rows}


LENGTH_SENTENCES = {"short": (2, 4), "medium": (4, 7), "long": (7, 12)}


def generate_story(
    db: Session,
    difficulty: str = "beginner",
    max_sentences: int = 6,
    length: str = "medium",
    topic: str | None = None,
) -> Story:
    """Generate a story using LLM from the user's known vocabulary."""
    known_words = _get_known_words(db)
    if not known_words:
        raise ValueError("No known/learning words found. Learn some words first.")

    # Diversity: weighted sampling to avoid over-represented words
    content_word_counts = get_content_word_counts(db)
    sample = sample_known_words_weighted(
        known_words, content_word_counts, KNOWN_SAMPLE_SIZE,
    )
    avoid_words = get_avoid_words(content_word_counts, known_words)

    vocab_list = "\n".join(
        f"- {w['arabic']} ({w['english']})" for w in sample
    )

    # Pick up to MAX_NEW_WORDS_IN_STORY unknown words to weave into the story
    known_ids = {w["lemma_id"] for w in known_words}
    unknown_lemmas = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.notin_(known_ids) if known_ids else True)
        .filter(Lemma.frequency_rank.isnot(None))
        .order_by(Lemma.frequency_rank.asc())
        .limit(MAX_NEW_WORDS_IN_STORY * 3)
        .all()
    )
    new_words = random.sample(
        unknown_lemmas,
        min(MAX_NEW_WORDS_IN_STORY, len(unknown_lemmas)),
    ) if unknown_lemmas else []

    new_words_section = ""
    if new_words:
        new_words_list = "\n".join(
            f"- {w.lemma_ar} ({w.gloss_en or ''})" for w in new_words
        )
        new_words_section = f"""
NEW VOCABULARY (weave these new words into the story — the reader will learn them from context):
{new_words_list}
"""

    lo, hi = LENGTH_SENTENCES.get(length, LENGTH_SENTENCES["medium"])
    topic_line = f"\nTOPIC/THEME: Write the story about or inspired by: {topic}" if topic else ""

    # Pick a random genre to keep stories varied
    genres = [
        "a funny story with a punchline at the end",
        "a mystery — something is not what it seems",
        "a heartwarming story about an unexpected friendship",
        "a story with an ironic twist ending",
        "a short adventure with a moment of danger",
        "a story where someone learns a surprising lesson",
    ]
    genre = random.choice(genres)

    prompt = f"""Write a cohesive mini-story ({lo}-{hi} sentences) for a {difficulty} Arabic learner.

GENRE: {genre}
{topic_line}
KNOWN VOCABULARY (the reader already knows these words):
{vocab_list}
{new_words_section}
IMPORTANT RULES:
- Use ONLY words from the known vocabulary, new vocabulary, and common function words
- Try to use ALL of the new vocabulary words naturally in the story
- Make new words understandable from context
- Write a REAL STORY with a narrative arc: setup → tension/development → resolution/punchline
- Give the main character a name. Make the reader care about what happens
- Every sentence must connect to the next — no disconnected practice sentences!
- Include full diacritics (tashkeel) on ALL Arabic words
- The title should hint at the story without spoiling it
{f"- For variety, try NOT to use these overused words (pick other vocabulary instead): {'، '.join(avoid_words)}" if avoid_words else ""}
Respond with JSON: {{"title_ar": "...", "title_en": "...", "body_ar": "full story in Arabic with diacritics", "body_en": "English translation", "transliteration": "ALA-LC transliteration"}}"""

    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt=STORY_SYSTEM_PROMPT,
            json_mode=True,
            temperature=0.9,
            model_override="openai",
        )
    except AllProvidersFailed as e:
        raise ValueError(f"LLM providers unavailable: {e}") from e

    body_ar = result.get("body_ar", "")
    if not body_ar:
        raise ValueError("LLM returned empty story")

    all_lemmas = _get_all_lemmas(db)
    lemma_lookup = build_lemma_lookup(all_lemmas)
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

    story.total_words = total
    story.known_count = known
    story.unknown_count = total - known - func
    story.readiness_pct = round((known + func) / total * 100, 1) if total > 0 else 0

    db.commit()
    db.refresh(story)

    log_interaction(
        event="story_generated",
        story_id=story.id,
        total_words=total,
        known_count=known,
        readiness_pct=story.readiness_pct,
    )

    return story


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
) -> Story:
    """Import an Arabic text and analyze its readiness."""
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

    story.total_words = total
    story.known_count = known
    story.unknown_count = total - known - func
    story.readiness_pct = round((known + func) / total * 100, 1) if total > 0 else 0

    db.commit()
    db.refresh(story)

    log_interaction(
        event="story_imported",
        story_id=story.id,
        total_words=total,
        known_count=known,
        readiness_pct=story.readiness_pct,
    )

    return story


def get_stories(db: Session) -> list[dict]:
    """Return all stories ordered by created_at desc."""
    stories = db.query(Story).order_by(Story.created_at.desc()).all()
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
            "created_at": s.created_at.isoformat() if s.created_at else "",
        }
        for s in stories
    ]


def get_story_detail(db: Session, story_id: int) -> dict:
    """Get story with all words and current knowledge state."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    knowledge_map = _build_knowledge_map(db)

    words = []
    for sw in story.words:
        is_known = False
        if sw.lemma_id:
            state = knowledge_map.get(sw.lemma_id)
            is_known = state in ("learning", "known")

        words.append({
            "position": sw.position,
            "surface_form": sw.surface_form,
            "lemma_id": sw.lemma_id,
            "gloss_en": sw.gloss_en,
            "is_known": is_known or bool(sw.is_function_word),
            "is_function_word": bool(sw.is_function_word),
            "sentence_index": sw.sentence_index or 0,
        })

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

    for sw in story.words:
        if not sw.lemma_id or sw.lemma_id in reviewed_lemmas:
            continue
        ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == sw.lemma_id)
            .first()
        )
        if not ulk:
            continue

        reviewed_lemmas.add(sw.lemma_id)
        if sw.lemma_id in looked_up_set:
            rating = 1
            again_count += 1
        else:
            rating = 3
            good_count += 1

        # Deterministic ID keeps retries idempotent per story+lemma.
        submit_review(
            db,
            lemma_id=sw.lemma_id,
            rating_int=rating,
            review_mode="reading",
            comprehension_signal="story_complete",
            client_review_id=f"story:{story_id}:complete:{sw.lemma_id}",
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


def skip_story(
    db: Session,
    story_id: int,
    looked_up_lemma_ids: list[int] | None = None,
    reading_time_ms: int | None = None,
) -> dict:
    """Mark story as skipped. Submit rating=1 for looked-up words only."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    if story.status == "skipped":
        return {"story_id": story_id, "status": "skipped", "again_count": 0, "duplicate": True}
    if story.status in TERMINAL_STORY_STATUSES:
        return {
            "story_id": story_id,
            "status": story.status,
            "again_count": 0,
            "duplicate": True,
            "conflict": True,
        }

    again_count = 0
    if looked_up_lemma_ids:
        for lid in set(looked_up_lemma_ids):
            ulk = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == lid)
                .first()
            )
            if ulk:
                submit_review(
                    db,
                    lemma_id=lid,
                    rating_int=1,
                    review_mode="reading",
                    client_review_id=f"story:{story_id}:skip:{lid}",
                )
                again_count += 1

    story.status = "skipped"
    db.commit()

    log_interaction(
        event="story_skipped",
        story_id=story_id,
        again_count=again_count,
        words_looked_up=len(looked_up_lemma_ids) if looked_up_lemma_ids else 0,
        reading_time_ms=reading_time_ms,
    )

    return {
        "story_id": story_id,
        "status": "skipped",
        "again_count": again_count,
    }


def too_difficult_story(
    db: Session,
    story_id: int,
    looked_up_lemma_ids: list[int] | None = None,
    reading_time_ms: int | None = None,
) -> dict:
    """Mark story as too_difficult. Submit rating=1 for looked-up words only."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    if story.status == "too_difficult":
        return {"story_id": story_id, "status": "too_difficult", "again_count": 0, "duplicate": True}
    if story.status in TERMINAL_STORY_STATUSES:
        return {
            "story_id": story_id,
            "status": story.status,
            "again_count": 0,
            "duplicate": True,
            "conflict": True,
        }

    again_count = 0
    if looked_up_lemma_ids:
        for lid in set(looked_up_lemma_ids):
            ulk = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == lid)
                .first()
            )
            if ulk:
                submit_review(
                    db,
                    lemma_id=lid,
                    rating_int=1,
                    review_mode="reading",
                    client_review_id=f"story:{story_id}:too_difficult:{lid}",
                )
                again_count += 1

    story.status = "too_difficult"
    db.commit()

    log_interaction(
        event="story_too_difficult",
        story_id=story_id,
        again_count=again_count,
        words_looked_up=len(looked_up_lemma_ids) if looked_up_lemma_ids else 0,
        reading_time_ms=reading_time_ms,
    )

    return {
        "story_id": story_id,
        "status": "too_difficult",
        "again_count": again_count,
    }


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

    knowledge_map = _build_knowledge_map(db)

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
            if state in ("learning", "known"):
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
