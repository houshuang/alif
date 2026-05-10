"""Generated maintenance passages for review and story reading.

The review flow can display any grouped sentences as a passage card. This
service creates higher-quality cohesive passages and stores them using existing
Story + Sentence rows so they can feed both the story reader and review cards
without a new table.
"""

from __future__ import annotations

import random
import tempfile
from collections import Counter
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


PASSAGE_TARGET_POOL_SIZE = 24
PASSAGE_PROMPT_VOCAB_SIZE = 320
PASSAGE_MAX_TARGETS_USED = 2

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
- Repeat at least one concrete content word, image, or setting across two or
  more sentences so the passage has a visible anchor.

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


PASSAGE_AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "title_ar": {"type": "string"},
        "title_en": {"type": "string"},
        "style_tag": {"type": "string", "enum": list(PASSAGE_STYLES)},
        "premise": {"type": "string"},
        "selected_target_lemma_ids": {
            "type": "array",
            "items": {"type": "integer"},
        },
        "sentences": {
            "type": "array",
            "minItems": 3,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "arabic": {"type": "string"},
                    "english": {"type": "string"},
                    "target_lemma_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["arabic", "english"],
            },
        },
    },
    "required": [
        "title_ar",
        "title_en",
        "style_tag",
        "premise",
        "selected_target_lemma_ids",
        "sentences",
    ],
}


PASSAGE_QUALITY_SCHEMA = {
    "type": "object",
    "properties": {
        "cohesive": {"type": "boolean"},
        "rewarding": {"type": "boolean"},
        "not_disconnected_list": {"type": "boolean"},
        "translation_correct": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": [
        "cohesive",
        "rewarding",
        "not_disconnected_list",
        "translation_correct",
        "reason",
    ],
}


PASSAGE_AGENT_SYSTEM_PROMPT = f"""\
You are a brilliant Arabic (MSA / fusha) miniaturist using tools: think Borges
flash fiction with a very limited palette. The constraint is the creative
challenge. Your job is to turn a pool of due review words into ONE cohesive
miniature text.

This is reading, not a drill: no exercises, no grammar explanations, no
questions for the learner, no "translate this" prompts.

The most important objective is a satisfying short passage. It is better to
practice fewer due words in a beautiful, coherent text than to force awkward
combinations. Words you skip will be practiced later as single sentences.

{ARABIC_STYLE_RULES}

{DIFFICULTY_STYLE_GUIDE}

Passage craft:
- 3-5 connected sentences with a beginning, middle, and end. Sentence order must
  matter; if the sentences could be shuffled, the passage has failed.
- The last sentence matters most. Land a small ending: a turn, a joke, a
  bittersweet observation, a useful final fact, or quiet closure.
- Choose 1-3 target words from the candidate pool that belong together
  topically, narratively, emotionally, or conceptually.
- Prefer one reusable anchor target over four awkward one-off targets. It is
  fine if the same target word appears in several sentences.
- Better still: use a support word as the recurring anchor and let a due target
  appear once, exactly where it naturally belongs. Do not repeat an abstract,
  adjectival, ordinal, or semantically narrow target just to satisfy review.
- Every sentence must advance the same scene, memory, observation, joke, or
  fact. Do not write three unrelated standalone examples.
- Repeat at least one concrete content word, image, or setting across two or
  more sentences. The repetition should feel natural and help the learner hold
  the passage together.
- The passage must have at least one adult-readable reward: humor, suspense, a
  small twist, poetry, warmth, nostalgia, surprise, or a genuinely useful fact.
- Prefer concrete details, warmth, irony, nostalgia, a small reveal, or a useful
  fact. Do not write generic inventory sentences.
- Prefer a realistic tiny scene or observation for an adult reader. No slapstick
  transformation, disconnected metaphor, or random kitchen/food chain unless
  the cause and effect are obvious.
- Keep physical and narrative logic plausible. Do not make foods "melt" unless
  the food really melts, do not make animals suddenly become huge, and do not
  combine random objects for surreal effect unless the whole passage clearly
  earns that effect.
- Every sentence should answer why the next sentence follows.
- Use target words only when they fit naturally; never force a bizarre list.
- If a sentence is almost good but contains one bad word, revise that word
  surgically instead of restarting the whole passage.

Vocabulary constraint:
- Use only learner vocabulary from vocab_prompt.txt / vocab_lookup.tsv, selected
  target words from targets.json, and common function words.
- Do not invent proper names or content words outside the vocabulary.
- Full tashkeel on all Arabic words with correct i'rab.
- Include Arabic punctuation.

Tool workflow:
1. Read targets.json and vocab_prompt.txt.
2. In scratch only, make several possible target clusters/premises. Reject any
   premise that would become disconnected examples, an inventory, or a forced
   parade of due words.
3. Pick the single best premise around one story-suitable due word. Add a second
   due word only if it improves the premise. Never use a third due word.
4. Draft the full passage from that premise.
5. Validate each sentence with validator.py using a selected target bare form if
   one appears there; otherwise use any repeated support-word bare form that
   appears in that sentence. The app will still run its own full validation.
6. On unknown_words, replace those words with allowed vocabulary and re-run the
   validator. Preserve the passage when editing; do not collapse into examples.

Return JSON only. Include the chosen premise in the premise field."""


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
            "forms_json": lemma.forms_json,
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
            "state": "known",
            "forms_json": lemma.forms_json,
        }
        for _due, lemma in due_rows[:limit]
    ]


_PASSAGE_STRONG_GLOSS_CUES = (
    "animal",
    "book",
    "cantaloupe",
    "cat",
    "coffee",
    "cup",
    "door",
    "garden",
    "house",
    "kitchen",
    "market",
    "pear",
    "pears",
    "rat",
    "racket",
    "room",
    "shoe",
    "slipper",
    "table",
    "tree",
    "window",
)

_PASSAGE_HARD_GLOSS_CUES = (
    "feminine",
    "masculine",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "measles",
    "smallpox",
    "thigh",
    "index finger",
    "folkloric",
    "contribute",
    "is located",
    "read it",
)


def _passage_target_story_score(word: dict[str, Any]) -> int:
    """Rank due words by how naturally they can anchor a tiny passage.

    This is intentionally only a prompt-ordering heuristic. Hard words still
    remain eligible for ordinary single-sentence review; passages should spend
    their scarce reading cost on words that can carry a coherent scene.
    """
    pos = str(word.get("pos") or "").lower()
    gloss = str(word.get("english") or "").lower()
    score = 0
    if "noun" in pos:
        score += 4
    if "verb" in pos:
        score += 2
    if "adj" in pos or "adjective" in pos:
        score -= 1
    if any(cue in gloss for cue in _PASSAGE_STRONG_GLOSS_CUES):
        score += 4
    if any(cue in gloss for cue in _PASSAGE_HARD_GLOSS_CUES):
        score -= 5
    if gloss.startswith("to "):
        score += 1
    if len(gloss) > 28:
        score -= 1
    return score


def _rank_targets_for_passage(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        words,
        key=lambda w: (
            -_passage_target_story_score(w),
            int(w["lemma_id"]),
        ),
    )


def _agent_model_name(model_override: str) -> str:
    if model_override in ("opus", "claude_opus"):
        return "opus"
    return "sonnet"


def _agent_rows(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "lemma_id": int(w["lemma_id"]),
            "lemma_ar": w["arabic"],
            "lemma_ar_bare": w.get("arabic_bare") or strip_diacritics(w["arabic"]),
            "gloss_en": w.get("english") or "",
            "pos": w.get("pos") or "",
            "forms_json": w.get("forms_json"),
            "knowledge_state": w.get("state") or "known",
        }
        for w in words
    ]


def _agent_targets(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "target_lemma_id": int(w["lemma_id"]),
            "target_word": w["arabic"],
            "target_bare": w.get("arabic_bare") or strip_diacritics(w["arabic"]),
            "target_translation": w.get("english") or "",
            "pos": w.get("pos") or "",
            "state": w.get("state") or "known",
        }
        for w in words
    ]


def _generate_agent_with_tools(**kwargs) -> dict[str, Any]:
    from limbic.cerebellum.claude_cli import ClaudeCLIError, generate as _limbic_generate

    try:
        result, _meta = _limbic_generate(
            prompt=kwargs["prompt"],
            project="alif",
            purpose="maintenance_passage_agentic",
            system=kwargs["system_prompt"],
            schema=kwargs["json_schema"],
            model=kwargs.get("model", "sonnet"),
            tools=kwargs.get("tools", "Read,Bash"),
            allowed_tools="Bash Read",
            max_budget=kwargs.get("max_budget_usd", 0.60),
            work_dir=kwargs["work_dir"],
            dangerously_skip_permissions=False,
            timeout=kwargs.get("timeout", 300),
        )
    except ClaudeCLIError as exc:
        raise RuntimeError(str(exc)) from exc
    return result


def generate_maintenance_passage_agentic(
    target_pool: list[dict[str, Any]],
    known_words: list[dict[str, Any]],
    style: str | None = None,
    sentence_count: int = 4,
    model_override: str = "claude_sonnet",
    feedback: str | None = None,
) -> dict[str, Any]:
    """Use a tool-enabled Sonnet session to choose and validate a cohesive passage."""
    if not target_pool:
        raise PassageGenerationError("No maintenance targets available")

    from app.services.sentence_self_correct import _write_batch_files, _write_validator_script

    style = style if style in PASSAGE_STYLES else random.choice(PASSAGE_STYLES)
    sentence_count = max(3, min(5, sentence_count))
    target_pool = _rank_targets_for_passage(target_pool)[:PASSAGE_TARGET_POOL_SIZE]

    with tempfile.TemporaryDirectory(prefix="alif-maint-passage-") as work_dir:
        _write_batch_files(
            _agent_rows(known_words),
            work_dir,
            _agent_targets(target_pool),
            prompt_sample_size=PASSAGE_PROMPT_VOCAB_SIZE,
        )
        _write_validator_script(work_dir)

        prompt = f"""Create one cohesive {sentence_count}-sentence maintenance passage.

Files:
- Candidate due/review target pool: {work_dir}/targets.json
- Supporting learner vocabulary: {work_dir}/vocab_prompt.txt
- Validator: python3 {work_dir}/validator.py "<arabic sentence>" "<target_bare>"

Style target: {style}

Selection rules:
- Before drafting, compare several possible target groups and premises in
  scratch. Pick the one that would be most rewarding for an adult to read.
- Pick one story-suitable candidate target as the anchor for the strongest tiny
  story, memory, observation, joke, or informative paragraph.
- Add a second target only if it genuinely improves that premise. Never use a
  third target in the Arabic passage.
- The ending should reframe or complete the scene gently; it should not exist
  merely to introduce another due word.
- Do not maximize target count at the cost of coherence. Target coverage has no
  value if the result reads like examples.
- At least one sentence must contain a selected target word. It is fine if the
  other sentences are connector sentences built from support vocabulary.
- Prefer a support-vocabulary anchor that recurs across the passage. Use a due
  target once or twice where it belongs naturally. Skipped targets are fine.
- Keep one recurring lexical anchor across the passage: a concrete object,
  place, person role, image, or topic word should appear in at least two
  sentences.
- The passage needs a beginning, middle, and end. Sentence order must matter.
- The final sentence should have a tiny payoff: a reveal, emotional closure,
  joke, image, or concrete fact.
- If a target is awkward to combine, skip it; it will get single-sentence review.
- Avoid disease words, ordinal words, body parts, abstract adjectives, and
  classroom command forms unless they are clearly the best natural anchor.
- Keep the scene physically plausible and narratively motivated. No sudden giant
  animals, no melting non-melting foods, no random object pairings.

{f'''Previous rejected draft/editor feedback:
{feedback}

Use this feedback to revise the premise or target choice. Do not repeat the same
failure pattern.
''' if feedback else ''}

Validation rules:
- Validate every Arabic sentence before returning. Use a selected target word
  as the validator target when the sentence contains one; otherwise validate
  against a repeated support anchor from the learner vocabulary.
- On validator unknown_words, revise only the offending word or phrase when
  possible, keeping the same passage.
- If a chosen target keeps forcing bad prose, drop that target and revise the
  passage around a better subset.

Return exactly {sentence_count} sentence objects. Include:
- premise: the chosen English premise in one sentence.
- selected_target_lemma_ids: target words you intentionally used."""

        result = _generate_agent_with_tools(
            prompt=prompt,
            system_prompt=PASSAGE_AGENT_SYSTEM_PROMPT,
            json_schema=PASSAGE_AGENT_SCHEMA,
            work_dir=work_dir,
            model=_agent_model_name(model_override),
            tools="Read,Bash",
            max_budget_usd=0.60,
            timeout=300,
        )

    if not isinstance(result, dict):
        raise PassageGenerationError("Agentic passage generation returned non-object JSON")
    return result


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


def _review_passage_cohesion(validated: list[dict[str, Any]]) -> None:
    """Reject passage-shaped bundles that read like unrelated examples."""
    passage = "\n".join(
        f"{idx + 1}. AR: {item['arabic']}\n   EN: {item['english']}"
        for idx, item in enumerate(validated)
    )
    result = generate_completion(
        prompt=f"""Review this Arabic learner passage as a passage, not as separate sentences.

Reject it if the sentences are merely disconnected examples, if there is no
shared scene/topic/progression, if the final sentence has no payoff, or if the
translation is materially wrong.

Passage:
{passage}

Return JSON with strict booleans.""",
        system_prompt=(
            "You are a strict Arabic reading-material editor. Pass only cohesive, "
            "rewarding short passages. Fail lists of unrelated example sentences."
        ),
        json_schema=PASSAGE_QUALITY_SCHEMA,
        temperature=0,
        timeout=120,
        model_override="claude_haiku",
        task_type="maintenance_passage_quality",
    )
    if not isinstance(result, dict):
        raise PassageGenerationError("Passage quality review returned non-object JSON")
    failed = [
        key for key in (
            "cohesive",
            "rewarding",
            "not_disconnected_list",
            "translation_correct",
        )
        if result.get(key) is not True
    ]
    if failed:
        reason = result.get("reason") or ", ".join(failed)
        raise PassageGenerationError(f"Passage failed cohesion review: {reason}")


def _assert_passage_has_lexical_anchor(validated: list[dict[str, Any]]) -> None:
    """Cheap gate for the common failure: valid but disconnected examples.

    For learner micro-passages, requiring one repeated content lemma is a
    useful constraint rather than an aesthetic compromise: it gives the passage
    a visible anchor and forces generation away from three unrelated sentences.
    The stricter style prompt tells Sonnet to satisfy this naturally.
    """
    sentence_sets: list[set[int]] = []
    for item in validated:
        ids = {
            int(mapping.lemma_id)
            for mapping in item["mappings"]
            if mapping.lemma_id
            and not mapping.is_function_word
            and not mapping.is_proper_name
        }
        sentence_sets.append(ids)

    counts = Counter(lid for ids in sentence_sets for lid in ids)
    repeated = {lid for lid, count in counts.items() if count >= 2}
    if not repeated:
        raise PassageGenerationError(
            "Passage has no repeated content-word anchor across sentences"
        )


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
            min_targets=0,
            known_lemma_lookup=story_lemma_lookup,
            comprehensive_lemma_lookup=mapping_lookup,
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
        map_target_id = (
            min(found_ids, key=lambda lid: target_order.index(lid) if lid in target_order else 999)
            if found_ids
            else target_order[0]
        )
        primary_bare = target_bare_by_id[map_target_id]
        mappings = map_tokens_to_lemmas(
            tokenize_display(arabic),
            mapping_lookup,
            target_lemma_id=map_target_id,
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

        primary_id = map_target_id
        if not found_ids:
            content_ids = [
                int(m.lemma_id)
                for m in mappings
                if m.lemma_id and not m.is_function_word and not m.is_proper_name
            ]
            if not content_ids:
                raise PassageGenerationError("Passage sentence has no mapped content words")
            primary_id = content_ids[0]

        target_ids_used.update(found_ids)
        validated.append({
            "arabic": arabic,
            "english": english,
            "transliteration": transliterate_arabic(arabic) or "",
            "mappings": mappings,
            "primary_target_id": primary_id,
        })

    if not target_ids_used:
        raise PassageGenerationError("Passage used no review target words")
    if len(target_ids_used) > PASSAGE_MAX_TARGETS_USED:
        raise PassageGenerationError(
            f"Passage used too many review target words: {sorted(target_ids_used)}"
        )

    _assert_passage_has_lexical_anchor(validated)

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
        _review_passage_cohesion(validated)

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
            targets = _due_maintenance_targets(db, limit=PASSAGE_TARGET_POOL_SIZE)
        if not targets:
            raise PassageGenerationError("No eligible maintenance targets")
        prompt_vocab = eligible_words
    finally:
        db.close()

    last_error: Exception | None = None
    rejection_feedback: str | None = None
    for _attempt in range(max(1, max_generation_attempts)):
        attempt_model = model_override
        if (
            _attempt == max(1, max_generation_attempts) - 1
            and model_override not in ("opus", "claude_opus")
        ):
            attempt_model = "opus"

        draft = generate_maintenance_passage_agentic(
            target_pool=targets,
            known_words=prompt_vocab,
            style=style,
            sentence_count=sentence_count,
            model_override=attempt_model,
            feedback=rejection_feedback,
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
            draft_lines = " | ".join(
                str(s.get("arabic") or "").strip()
                for s in (draft.get("sentences") or [])
                if isinstance(s, dict)
            )
            rejection_feedback = (
                f"Rejected because: {exc}\n"
                f"Previous Arabic sentences: {draft_lines[:1200]}"
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    raise PassageGenerationError(
        f"Passage generation failed after retries: {last_error}"
    )
