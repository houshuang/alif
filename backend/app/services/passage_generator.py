"""Generated maintenance passages for review and story reading.

The review flow can display any grouped sentences as a passage card. This
service creates higher-quality cohesive passages and stores them using existing
Story + Sentence rows so they can feed both the story reader and review cards
without a new table.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, Story, UserLemmaKnowledge
from app.services.fsrs_service import parse_json_column
from app.services.llm import (
    ARABIC_STYLE_RULES,
    DIFFICULTY_STYLE_GUIDE,
    format_known_words_by_pos,
    generate_completion,
    review_sentences_quality,
)
from app.services.sentence_validator import (
    build_comprehensive_lemma_lookup,
    build_lemma_lookup,
    map_tokens_to_lemmas,
    normalize_alef,
    strip_diacritics,
    tokenize_display,
    validate_sentence_multi_target,
    _is_function_word,
)
from app.services.story_service import _create_story_words
from app.services.transliteration import transliterate_arabic


PASSAGE_STYLES = (
    "beautiful",
    "poignant",
    "nostalgic",
    "humorous",
    "surprising",
    "informative",
)


PASSAGE_SYSTEM_PROMPT = f"""\
You create short MSA (fusha) reading passages for maintenance review.
This is reading, not a drill: no exercises, no grammar explanations, no
questions for the learner, no "translate this" prompts.

Write a tiny complete scene, memory, observation, joke, or fact. The passage
should feel rewarding to read despite the limited vocabulary: beautiful,
poignant, nostalgic, quietly humorous, surprising, or informative.

{ARABIC_STYLE_RULES}

{DIFFICULTY_STYLE_GUIDE}

Passage craft:
- 3-5 connected sentences with a satisfying final sentence.
- Every sentence should be natural Arabic a literate speaker might write.
- Prefer concrete details, warmth, irony, or a small reveal.
- Use the target words when they fit naturally; never force a bizarre list.
- Do not invent proper names or content words outside the vocabulary.

Vocabulary constraint:
- Use ONLY the provided learner vocabulary, target words, and common function words.
- Every Arabic content word must come from the TARGET WORDS or SUPPORT WORDS
  lists in the user prompt. If a good scene needs another word, choose a
  simpler scene instead.
- Do not use family members, countries, illnesses, foods, animals, body parts,
  or place names unless that exact word is listed in the prompt.
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، ك، هذا، هذه،
ذلك، تلك، هو، هي، أنا، أنت، نحن، هم، ما، لا، أن، إن، كان، كانت، ليس، هل، لم،
لن، قد، الذي، التي، كل، بعض، هنا، هناك، الآن، جدا، فقط، أيضا، أو، ثم، لكن، يا
- Include full diacritics (tashkeel) on ALL Arabic words with correct i'rab.
- Include Arabic punctuation.

Return JSON only:
{{"title_ar": "...", "title_en": "...", "style_tag": "beautiful|poignant|nostalgic|humorous|surprising|informative", "sentences": [{{"arabic": "...", "english": "..."}}, ...]}}"""


PASSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "title_ar": {"type": "string"},
        "title_en": {"type": "string"},
        "style_tag": {"type": "string", "enum": list(PASSAGE_STYLES)},
        "sentences": {
            "type": "array",
            "minItems": 3,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "arabic": {"type": "string"},
                    "english": {"type": "string"},
                },
                "required": ["arabic", "english"],
            },
        },
    },
    "required": ["title_ar", "title_en", "style_tag", "sentences"],
}


class PassageGenerationError(RuntimeError):
    pass


def _limit_targets_for_passage(
    targets: list[dict[str, Any]],
    sentence_count: int,
) -> list[dict[str, Any]]:
    """Keep passage targets dense enough for review without forcing word salad."""
    max_targets = max(3, min(5, sentence_count + 1, len(targets)))
    return targets[:max_targets]


def _prompt_support_words(
    eligible_words: list[dict[str, Any]],
    target_words: list[dict[str, Any]],
    limit: int = 180,
) -> list[dict[str, Any]]:
    target_ids = {int(w["lemma_id"]) for w in target_words}
    state_rank = {
        "known": 0,
        "learning": 1,
        "lapsed": 2,
        "acquiring": 3,
    }
    support = [
        w for w in eligible_words
        if int(w["lemma_id"]) not in target_ids
    ]
    support.sort(key=lambda w: (
        state_rank.get(str(w.get("state") or ""), 9),
        str(w.get("pos") or ""),
        int(w["lemma_id"]),
    ))
    return support[:limit]


def _due_dt(ulk: UserLemmaKnowledge) -> datetime | None:
    card = parse_json_column(ulk.fsrs_card_json)
    if not card:
        return None
    due_raw = card.get("due")
    if not due_raw:
        return None
    due = datetime.fromisoformat(due_raw)
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due


def _eligible_passage_words(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(Lemma, UserLemmaKnowledge)
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(
            UserLemmaKnowledge.knowledge_state.in_(["known", "learning", "lapsed", "acquiring"]),
            Lemma.canonical_lemma_id.is_(None),
            Lemma.gloss_en.isnot(None),
        )
        .all()
    )
    words: list[dict[str, Any]] = []
    for lemma, ulk in rows:
        if lemma.word_category == "proper_name":
            continue
        if lemma.lemma_ar_bare and _is_function_word(lemma.lemma_ar_bare):
            continue
        if ulk.knowledge_state == "acquiring" and (ulk.acquisition_box or 1) < 2:
            continue
        words.append({
            "lemma_id": lemma.lemma_id,
            "arabic": lemma.lemma_ar,
            "arabic_bare": lemma.lemma_ar_bare,
            "english": lemma.gloss_en or "",
            "pos": lemma.pos or "",
            "state": ulk.knowledge_state,
        })
    return words


def _due_maintenance_targets(db: Session, limit: int = 8) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(Lemma, UserLemmaKnowledge)
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(
            UserLemmaKnowledge.knowledge_state.in_(["known", "learning", "lapsed"]),
            UserLemmaKnowledge.fsrs_card_json.isnot(None),
            Lemma.canonical_lemma_id.is_(None),
            Lemma.gloss_en.isnot(None),
        )
        .all()
    )
    due_rows: list[tuple[datetime, Lemma]] = []
    for lemma, ulk in rows:
        if lemma.word_category == "proper_name":
            continue
        if lemma.lemma_ar_bare and _is_function_word(lemma.lemma_ar_bare):
            continue
        due = _due_dt(ulk)
        if due and due <= now:
            due_rows.append((due, lemma))
    due_rows.sort(key=lambda item: item[0])
    return [
        {
            "lemma_id": lemma.lemma_id,
            "arabic": lemma.lemma_ar,
            "arabic_bare": lemma.lemma_ar_bare,
            "english": lemma.gloss_en or "",
            "pos": lemma.pos or "",
        }
        for _due, lemma in due_rows[:limit]
    ]


def generate_maintenance_passage_draft(
    target_words: list[dict[str, Any]],
    known_words: list[dict[str, Any]],
    style: str | None = None,
    sentence_count: int = 4,
    model_override: str = "claude_sonnet",
) -> dict[str, Any]:
    if not target_words:
        raise PassageGenerationError("No maintenance targets available")
    style = style if style in PASSAGE_STYLES else random.choice(PASSAGE_STYLES)
    sentence_count = max(3, min(5, sentence_count))

    target_list = "\n".join(
        f"- {w['arabic']} ({w['english']})" for w in target_words
    )
    support_words = _prompt_support_words(known_words, target_words)
    known_list = format_known_words_by_pos(support_words)
    prompt = f"""Write one cohesive {sentence_count}-sentence MSA maintenance passage.

Style target: {style}

TARGET WORDS TO REINFORCE:
{target_list}

SUPPORT WORDS YOU MAY USE:
{known_list}

Rules:
- Use at least one target word in each sentence.
- Across the full passage, use at least {min(sentence_count, len(target_words))} target words.
- Reuse simple listed words instead of adding any unlisted content word.
- Keep it comprehensible and compact, but not childish.
- No drills, no grammar talk, no learner instructions.
- Return exactly {sentence_count} sentence objects."""

    result = generate_completion(
        prompt=prompt,
        system_prompt=PASSAGE_SYSTEM_PROMPT,
        json_schema=PASSAGE_SCHEMA,
        temperature=0.35,
        timeout=180,
        model_override=model_override,
        task_type="maintenance_passage_gen",
    )
    if not isinstance(result, dict):
        raise PassageGenerationError("Passage generation returned non-object JSON")
    return result


def store_maintenance_passage(
    db: Session,
    generated: dict[str, Any],
    target_words: list[dict[str, Any]],
    eligible_words: list[dict[str, Any]],
    *,
    quality_gate: bool = True,
) -> Story:
    sentences = generated.get("sentences")
    if not isinstance(sentences, list) or not (3 <= len(sentences) <= 5):
        raise PassageGenerationError("Generated passage must contain 3-5 sentences")

    target_bares = {
        normalize_alef(strip_diacritics(w["arabic"])): int(w["lemma_id"])
        for w in target_words
    }
    target_order = [int(w["lemma_id"]) for w in target_words]
    target_bare_by_id = {lid: bare for bare, lid in target_bares.items()}
    known_bare_forms = {
        normalize_alef(strip_diacritics(w["arabic"]))
        for w in eligible_words
    } | set(target_bares.keys())

    mapping_lookup = build_comprehensive_lemma_lookup(db)
    all_lemma_ids = {w["lemma_id"] for w in eligible_words} | set(target_order)
    all_lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(all_lemma_ids)).all()
    story_lemma_lookup = build_lemma_lookup(all_lemmas)
    allowed_bare_forms = set(story_lemma_lookup.keys())
    knowledge_map = {
        row.lemma_id: row.knowledge_state
        for row in db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id.in_(all_lemma_ids))
        .all()
    }

    validated: list[dict[str, Any]] = []
    target_ids_used: set[int] = set()
    for sentence in sentences:
        arabic = str(sentence.get("arabic", "")).strip()
        english = str(sentence.get("english", "")).strip()
        if not arabic or not english:
            raise PassageGenerationError("Every passage sentence needs Arabic and English")

        validation = validate_sentence_multi_target(
            arabic_text=arabic,
            target_bares=target_bares,
            known_bare_forms=allowed_bare_forms or known_bare_forms,
            min_targets=1,
            known_lemma_lookup=story_lemma_lookup,
        )
        if not validation.valid:
            raise PassageGenerationError(
                f"Passage sentence failed vocabulary validation: {validation.issues}"
            )
        found_ids = [
            target_bares[bare]
            for bare, found in validation.targets_found.items()
            if found
        ]
        primary_id = min(found_ids, key=lambda lid: target_order.index(lid) if lid in target_order else 999)
        primary_bare = target_bare_by_id[primary_id]
        mappings = map_tokens_to_lemmas(
            tokenize_display(arabic),
            mapping_lookup,
            target_lemma_id=primary_id,
            target_bare=primary_bare,
        )
        unmapped = [
            m.surface_form for m in mappings
            if m.lemma_id is None and not m.is_function_word and not m.is_proper_name
        ]
        if unmapped:
            raise PassageGenerationError(f"Passage sentence has unmapped words: {unmapped}")

        mapped_ids = {m.lemma_id for m in mappings if m.lemma_id}
        if mapped_ids:
            glossless = [
                lemma.lemma_ar
                for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(mapped_ids)).all()
                if not lemma.gloss_en
            ]
            if glossless:
                raise PassageGenerationError(f"Passage sentence has glossless lemmas: {glossless}")

        target_ids_used.update(found_ids)
        validated.append({
            "arabic": arabic,
            "english": english,
            "transliteration": transliterate_arabic(arabic) or "",
            "mappings": mappings,
            "primary_target_id": primary_id,
        })

    if quality_gate:
        quality = review_sentences_quality([
            {"arabic": item["arabic"], "english": item["english"]}
            for item in validated
        ])
        for item, review in zip(validated, quality):
            if not review.natural or not review.translation_correct:
                raise PassageGenerationError(
                    f"Passage sentence failed quality review: {review.reason}"
                )

    body_ar = "\n".join(item["arabic"] for item in validated)
    body_en = "\n".join(item["english"] for item in validated)
    story = Story(
        title_ar=str(generated.get("title_ar") or "نَصٌّ قَصِيرٌ"),
        title_en=str(generated.get("title_en") or "Short passage"),
        body_ar=body_ar,
        body_en=body_en,
        transliteration="\n".join(item["transliteration"] for item in validated),
        source="maintenance",
        status="active",
        difficulty_level="beginner",
        format_type="maintenance_passage",
        metadata_json={
            "style_tag": generated.get("style_tag"),
            "target_lemma_ids": sorted(target_ids_used),
            "sentence_count": len(validated),
        },
    )
    db.add(story)
    db.flush()

    total, known, func = _create_story_words(
        db,
        story,
        body_ar,
        story_lemma_lookup,
        knowledge_map,
    )
    story.total_words = total
    story.known_count = known + func
    story.unknown_count = max(0, total - story.known_count)
    story.readiness_pct = round((story.known_count / total) * 100, 1) if total else 0.0

    for sentence in validated:
        sent = Sentence(
            arabic_text=sentence["arabic"],
            english_translation=sentence["english"],
            transliteration=sentence["transliteration"],
            source="passage",
            story_id=story.id,
            target_lemma_id=sentence["primary_target_id"],
            created_at=datetime.now(timezone.utc),
            mappings_verified_at=datetime.now(timezone.utc),
        )
        db.add(sent)
        db.flush()
        for mapping in sentence["mappings"]:
            db.add(SentenceWord(
                sentence_id=sent.id,
                position=mapping.position,
                surface_form=mapping.surface_form,
                lemma_id=mapping.lemma_id,
                is_target_word=mapping.is_target,
            ))

    db.commit()
    return story


def generate_and_store_maintenance_passage(
    target_lemma_ids: list[int] | None = None,
    style: str | None = None,
    sentence_count: int = 4,
    model_override: str = "claude_sonnet",
    max_generation_attempts: int = 3,
) -> Story:
    """Generate, validate, and store one maintenance passage.

    Uses a read -> LLM -> write pattern so no DB write transaction is held
    during the LLM call.
    """
    db = SessionLocal()
    try:
        eligible_words = _eligible_passage_words(db)
        if target_lemma_ids:
            target_set = set(target_lemma_ids)
            targets = [w for w in eligible_words if w["lemma_id"] in target_set]
        else:
            targets = _due_maintenance_targets(db, limit=24)
        if not targets:
            raise PassageGenerationError("No eligible maintenance targets")
        targets = _limit_targets_for_passage(targets, sentence_count)
        prompt_vocab = eligible_words
    finally:
        db.close()

    last_error: Exception | None = None
    for _attempt in range(max(1, max_generation_attempts)):
        draft = generate_maintenance_passage_draft(
            target_words=targets,
            known_words=prompt_vocab,
            style=style,
            sentence_count=sentence_count,
            model_override=model_override,
        )

        db = SessionLocal()
        try:
            return store_maintenance_passage(
                db,
                draft,
                target_words=targets,
                eligible_words=prompt_vocab,
                quality_gate=True,
            )
        except PassageGenerationError as exc:
            db.rollback()
            last_error = exc
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    raise PassageGenerationError(
        f"Passage generation failed after retries: {last_error}"
    )
