"""Generated maintenance passages for review and story reading.

The review flow can display any grouped sentences as a passage card. This
service creates higher-quality cohesive passages and stores them using existing
Story + Sentence rows so they can feed both the story reader and review cards
without a new table.
"""

from __future__ import annotations

import json
import random
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
from app.services.proper_name_lemmas import get_or_create_proper_name_lemma
from app.services.sentence_validator import (
    build_comprehensive_lemma_lookup,
    build_lemma_lookup,
    map_tokens_to_lemmas,
    normalize_alef,
    requires_exact_running_text_alias,
    strip_diacritics,
    strip_punctuation,
    strip_tatweel,
    tokenize_display,
    validate_sentence_multi_target,
    is_function_word_lemma,
)
from app.services.story_service import _create_story_words
from app.services.transliteration import transliterate_arabic


PASSAGE_EXPERIMENT_VERSION = "clustered_short_stories_v2"
PASSAGE_TARGET_POOL_SIZE = 96
PASSAGE_PROMPT_VOCAB_SIZE = 320
PASSAGE_MIN_TARGETS_USED = 2
PASSAGE_MAX_TARGETS_USED = 4
PASSAGE_RECENT_CONTEXT_LIMIT = 24
PASSAGE_TARGET_HISTORY_WINDOW = timedelta(days=30)

PASSAGE_STYLES = (
    "beautiful",
    "poignant",
    "nostalgic",
    "humorous",
    "surprising",
    "informative",
    "suspenseful",
    "tender",
    "wry",
    "dramatic",
    "reflective",
    "adventurous",
)


# These are story shapes, not fill-in-the-blank plots. The least recently used
# shape is selected before each generation call, which gives the writer a
# different compositional problem without prescribing the same characters,
# setting, or ending. Morphology shapes deliberately make a due verb recur in
# contrasting natural forms.
PASSAGE_NARRATIVE_MODES: tuple[dict[str, Any], ...] = (
    {
        "id": "shared_action",
        "instruction": (
            "One person performs an action, another answers with the same action, "
            "and a group completes or changes it. Let the repeated verb carry the arc."
        ),
        "morphology_focus": True,
    },
    {
        "id": "parallel_lives",
        "instruction": (
            "Follow two people in parallel; their similar choices lead to different "
            "consequences that meet in the final sentence."
        ),
        "morphology_focus": True,
    },
    {
        "id": "witness_versions",
        "instruction": (
            "Give two or three compact accounts of the same event. The grammatical "
            "subjects should change, and the last detail resolves the disagreement."
        ),
        "morphology_focus": True,
    },
    {
        "id": "chain_reaction",
        "instruction": (
            "Build a clear cause-and-effect chain in which the same action passes from "
            "one actor to the next. End with a consequence, not a moral."
        ),
        "morphology_focus": True,
    },
    {
        "id": "before_after",
        "instruction": (
            "Contrast one concrete situation before and after a change. Make the change "
            "visible through actions rather than generic nostalgia."
        ),
        "morphology_focus": True,
    },
    {
        "id": "collective_decision",
        "instruction": (
            "A small group faces a practical decision. Show what one member does, what "
            "another does, and what they finally do together."
        ),
        "morphology_focus": True,
    },
    {
        "id": "failed_plan",
        "instruction": (
            "A sensible plan fails for a precise, believable reason; the response to the "
            "failure supplies the payoff. Avoid slapstick."
        ),
        "morphology_focus": True,
    },
    {
        "id": "repeated_request",
        "instruction": (
            "A request or warning passes between people and subtly changes each time. "
            "The final version reveals what everyone misunderstood."
        ),
        "morphology_focus": True,
    },
    {
        "id": "tiny_mystery",
        "instruction": (
            "Open with one concrete anomaly, test two plausible explanations, and reveal "
            "a fair answer using an earlier detail."
        ),
        "morphology_focus": False,
    },
    {
        "id": "object_journey",
        "instruction": (
            "Track one ordinary object through several hands or places. Its final owner "
            "or use should reframe the journey."
        ),
        "morphology_focus": False,
    },
    {
        "id": "dialogue_turn",
        "instruction": (
            "Use a terse exchange in which each line changes the reader's understanding. "
            "The last reply should land the turn without explaining it."
        ),
        "morphology_focus": False,
    },
    {
        "id": "comic_escalation",
        "instruction": (
            "Escalate one realistic inconvenience through three beats. Keep the logic "
            "physical and end on an understated comic consequence."
        ),
        "morphology_focus": False,
    },
    {
        "id": "moral_choice",
        "instruction": (
            "Put a character between two defensible choices. Show the decision in an "
            "action and let the consequence speak; do not state a lesson."
        ),
        "morphology_focus": False,
    },
    {
        "id": "message_with_context",
        "instruction": (
            "Center the passage on a short note, call, sign, or message whose meaning "
            "changes when one missing piece of context arrives."
        ),
        "morphology_focus": False,
    },
    {
        "id": "procedure_discovery",
        "instruction": (
            "Describe someone trying a small practical procedure. A concrete observation "
            "during the attempt produces a useful discovery."
        ),
        "morphology_focus": False,
    },
    {
        "id": "compressed_history",
        "instruction": (
            "Compress a real-seeming change across three moments or generations, using "
            "one recurring object as evidence rather than an empty-house ending."
        ),
        "morphology_focus": False,
    },
    {
        "id": "public_private",
        "instruction": (
            "Contrast what others believe about a person or event with one specific "
            "private fact. Avoid melodrama and generic loss."
        ),
        "morphology_focus": False,
    },
    {
        "id": "useful_fact_arc",
        "instruction": (
            "Build an informative passage as a question, mechanism, consequence, and "
            "memorable application—not four dictionary facts."
        ),
        "morphology_focus": False,
    },
    {
        "id": "sensory_observation",
        "instruction": (
            "Develop one present-tense observation through changing sound, light, motion, "
            "or texture. End on a precise image, not 'it was beautiful.'"
        ),
        "morphology_focus": False,
    },
    {
        "id": "bargain",
        "instruction": (
            "Stage a compact bargain or exchange. Each side wants something intelligible, "
            "and the final price or condition creates the turn."
        ),
        "morphology_focus": False,
    },
    {
        "id": "near_miss",
        "instruction": (
            "A character narrowly misses an opportunity or danger for a reason planted in "
            "the opening sentence. The ending reveals the connection."
        ),
        "morphology_focus": False,
    },
    {
        "id": "role_reversal",
        "instruction": (
            "Begin with a clear helper/learner, host/guest, or expert/novice relation, then "
            "reverse who helps whom through a credible action."
        ),
        "morphology_focus": False,
    },
    {
        "id": "countdown",
        "instruction": (
            "Organize the scene around diminishing time, chances, objects, or distance. "
            "Each sentence must materially change the count or stakes."
        ),
        "morphology_focus": False,
    },
    {
        "id": "circular_return",
        "instruction": (
            "Return in the last sentence to a concrete phrase or object from the first, "
            "but give it a new literal meaning."
        ),
        "morphology_focus": False,
    },
)

PASSAGE_NARRATIVE_MODE_IDS = tuple(mode["id"] for mode in PASSAGE_NARRATIVE_MODES)


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
{{"title_ar": "...", "title_en": "...", "style_tag": "beautiful|poignant|nostalgic|humorous|surprising|informative|suspenseful|tender|wry|dramatic|reflective|adventurous", "sentences": [{{"arabic": "...", "english": "..."}}, ...]}}"""


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
        "narrative_mode": {"type": "string", "enum": list(PASSAGE_NARRATIVE_MODE_IDS)},
        "premise": {"type": "string"},
        "target_plan": {"type": "string"},
        "ending_kind": {"type": "string"},
        "morphology_focus": {"type": "boolean"},
        "morphology_target_lemma_id": {
            "type": ["integer", "null"],
        },
        "selected_target_lemma_ids": {
            "type": "array",
            "minItems": PASSAGE_MIN_TARGETS_USED,
            "maxItems": PASSAGE_MAX_TARGETS_USED,
            "uniqueItems": True,
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
        "narrative_mode",
        "premise",
        "target_plan",
        "ending_kind",
        "morphology_focus",
        "morphology_target_lemma_id",
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
        "specific_not_generic": {"type": "boolean"},
        "repetition_natural": {"type": "boolean"},
        "avoids_stock_ending": {"type": "boolean"},
        "morphology_correct": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": [
        "cohesive",
        "rewarding",
        "not_disconnected_list",
        "translation_correct",
        "specific_not_generic",
        "repetition_natural",
        "avoids_stock_ending",
        "morphology_correct",
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

The most important objective is a satisfying short story that also creates
useful repetition. You receive a large, deliberately ranked due pool so you can
choose a genuinely coherent cluster instead of forcing whichever words happen
to be first. Words you skip will be used by later stories or single sentences.

{ARABIC_STYLE_RULES}

{DIFFICULTY_STYLE_GUIDE}

Passage craft:
- 3-5 connected sentences with a beginning, middle, and end. Sentence order must
  matter; if the sentences could be shuffled, the passage has failed.
- The last sentence matters most. Land a small ending: a turn, a joke, a
  bittersweet observation, a useful final fact, or quiet closure.
- Choose 2-4 target words from the candidate pool that belong together through
  an actor-action-object relation, a shared real situation, a causal chain, or
  a precise topic. Prefer three. Never select a group merely because its glosses
  are vaguely emotional or all share the same broad domain.
- Repeat at least one selected target naturally. The best passages make a due
  verb, object, or idea recur in changed circumstances rather than mentioning
  every target once.
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
- Do not default to an old house, absent grandparent, empty room/garden, lonely
  animal, generic man/boy, or "only the memory remains" ending. These motifs are
  not forbidden forever, but recent_passages.json makes them especially costly
  when they have appeared recently.
- Do not end by attaching "now", "but", "still remains", "in the heart", or
  "was empty" to manufacture pathos. Earn the final sentence through action,
  discovery, dialogue, consequence, or a concrete image.
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
1. Read targets.json, vocab_prompt.txt, and recent_passages.json.
2. Read the assigned narrative shape. It is a compositional constraint, not a
   plot template: invent new actors, setting, stakes, and ending within it.
3. In scratch only, make several possible target clusters/premises. Reject any
   premise that would become disconnected examples, an inventory, or a forced
   parade of due words.
4. Pick the single best premise around 2-4 mutually useful targets. Prefer a
   three-word cluster. Coverage debt and recent passage use are in targets.json;
   avoid recently overused targets when a coherent alternative exists.
5. If morphology_focus is true, choose one verb target with reliable forms_json
   and make it recur in at least three grammatically contrasting forms (for
   example he did / she did / they did, or past / present / plural). The forms
   must belong to the same lemma and make narrative sense. Do not use كَانَ as
   the morphology target.
6. Draft the full passage from that premise.
7. Validate each sentence with validator.py using a selected target bare form if
   one appears there; otherwise use any repeated support-word bare form that
   appears in that sentence. The app will still run its own full validation.
8. On unknown_words, replace those words with allowed vocabulary and re-run the
   validator. Preserve the passage when editing; do not collapse into examples.

Return JSON only. Explain the chosen semantic grouping briefly in target_plan,
name the kind of payoff in ending_kind, and include the premise."""


class PassageGenerationError(RuntimeError):
    pass


def _story_metadata(story: Story) -> dict[str, Any]:
    metadata = parse_json_column(story.metadata_json, default={})
    return metadata if isinstance(metadata, dict) else {}


def _recent_passage_history(
    db: Session,
    limit: int = PASSAGE_RECENT_CONTEXT_LIMIT,
) -> list[dict[str, Any]]:
    """Compact creative history used for rotation and anti-copy context."""
    stories = (
        db.query(Story)
        .filter(Story.format_type == "maintenance_passage")
        .order_by(Story.created_at.desc(), Story.id.desc())
        .limit(limit)
        .all()
    )
    history: list[dict[str, Any]] = []
    for story in stories:
        metadata = _story_metadata(story)
        english_lines = [
            line.strip() for line in (story.body_en or "").splitlines() if line.strip()
        ]
        history.append({
            "story_id": story.id,
            "created_at": story.created_at.isoformat() if story.created_at else None,
            "title_en": story.title_en or "",
            "premise": metadata.get("premise") or "",
            "narrative_mode": metadata.get("narrative_mode"),
            "target_lemma_ids": metadata.get("target_lemma_ids") or [],
            "opening_en": english_lines[0] if english_lines else "",
            "ending_en": english_lines[-1] if english_lines else "",
            "body_en": story.body_en or "",
        })
    return history


def _select_narrative_mode(
    recent_passages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Choose among the least-used recent shapes; do not create a fixed cycle."""
    counts = Counter(
        item.get("narrative_mode")
        for item in recent_passages
        if item.get("narrative_mode") in PASSAGE_NARRATIVE_MODE_IDS
    )
    minimum = min(
        (counts.get(mode["id"], 0) for mode in PASSAGE_NARRATIVE_MODES),
        default=0,
    )
    least_used = [
        mode for mode in PASSAGE_NARRATIVE_MODES
        if counts.get(mode["id"], 0) == minimum
    ]
    return dict(random.choice(least_used or list(PASSAGE_NARRATIVE_MODES)))


def _target_usage_history(
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[int, dict[str, Any]]:
    """Recent target coverage debt, independent of ordinary sentence exposure."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - PASSAGE_TARGET_HISTORY_WINDOW
    stories = (
        db.query(Story)
        .filter(
            Story.format_type == "maintenance_passage",
            Story.created_at >= cutoff,
        )
        .all()
    )
    usage: dict[int, dict[str, Any]] = {}
    for story in stories:
        metadata = _story_metadata(story)
        created = story.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        for raw_id in metadata.get("target_lemma_ids") or []:
            try:
                lemma_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            item = usage.setdefault(
                lemma_id,
                {"count_30d": 0, "count_7d": 0, "last_at": None},
            )
            item["count_30d"] += 1
            if created and created >= now - timedelta(days=7):
                item["count_7d"] += 1
            if created and (item["last_at"] is None or created > item["last_at"]):
                item["last_at"] = created
    return usage


_STOCK_ENDING_PATTERNS = (
    "is empty now",
    "was empty now",
    "is gone now",
    "only the memory remains",
    "only the memories remain",
    "remains in the heart",
    "still remains in the heart",
    "is no longer with us",
    "but the house is empty",
    "but the garden is empty",
)

_SIMILARITY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "his", "i", "in",
    "is", "it", "its", "not", "of", "on", "one", "she", "that", "the",
    "their", "them", "then", "there", "they", "this", "to", "was", "were",
    "with", "would",
}


def _english_content_tokens(text: str) -> set[str]:
    token = ""
    tokens: set[str] = set()
    for char in text.lower():
        if "a" <= char <= "z" or char == "'":
            token += char
            continue
        if token and token not in _SIMILARITY_STOPWORDS and len(token) > 2:
            tokens.add(token)
        token = ""
    if token and token not in _SIMILARITY_STOPWORDS and len(token) > 2:
        tokens.add(token)
    return tokens


def _assert_not_recent_plot_echo(
    generated_body_en: str,
    recent_passages: list[dict[str, Any]],
) -> None:
    """Reject the two observed collapse modes: stock pathos and near-remakes."""
    lowered = " ".join(generated_body_en.lower().split())
    if any(pattern in lowered for pattern in _STOCK_ENDING_PATTERNS):
        raise PassageGenerationError("Passage uses a stock empty/remaining-memory ending")

    tired_motifs = sum(
        marker in lowered
        for marker in ("old house", "grandfather", "empty", "memory", "years ago", "now")
    )
    if tired_motifs >= 3:
        raise PassageGenerationError("Passage recombines too many overused pathos motifs")

    current = _english_content_tokens(generated_body_en)
    if len(current) < 5:
        return
    for recent in recent_passages:
        prior = _english_content_tokens(str(recent.get("body_en") or ""))
        if len(prior) < 5:
            continue
        similarity = len(current & prior) / len(current | prior)
        if similarity >= 0.42:
            raise PassageGenerationError(
                f"Passage is too similar to recent story {recent.get('story_id')} "
                f"(content Jaccard {similarity:.2f})"
            )


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
        if is_function_word_lemma(
            lemma.lemma_ar_bare, lemma.function_word_override
        ):
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
            "grammar_features_json": lemma.grammar_features_json,
            "thematic_domain": lemma.thematic_domain,
            "frequency_rank": lemma.frequency_rank,
        })
    return words


def _due_maintenance_targets(
    db: Session,
    limit: int = PASSAGE_TARGET_POOL_SIZE,
) -> list[dict[str, Any]]:
    """Rank a wide due pool by coverage debt, usefulness, and overdue pressure.

    The old path took the eight oldest rows and then repeatedly offered the same
    story-friendly noun at the top. This keeps up to 96 choices visible to the
    writer while strongly preferring lemmas that have not recently received a
    passage. It is deterministic apart from the later narrative-mode choice.
    """
    now = datetime.now(timezone.utc)
    target_usage = _target_usage_history(db, now=now)
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
    due_rows: list[dict[str, Any]] = []
    for lemma, ulk in rows:
        if lemma.word_category == "proper_name":
            continue
        if is_function_word_lemma(
            lemma.lemma_ar_bare, lemma.function_word_override
        ):
            continue
        due = _due_dt(ulk)
        if due and due <= now:
            card = parse_json_column(ulk.fsrs_card_json, default={})
            stability = float(card.get("stability") or 0.0) if isinstance(card, dict) else 0.0
            usage = target_usage.get(lemma.lemma_id, {})
            last_at = usage.get("last_at")
            days_since = (
                max(0.0, (now - last_at).total_seconds() / 86400)
                if last_at else None
            )
            overdue_days = max(0.0, (now - due).total_seconds() / 86400)
            word = {
                "lemma_id": lemma.lemma_id,
                "arabic": lemma.lemma_ar,
                "arabic_bare": lemma.lemma_ar_bare,
                "english": lemma.gloss_en or "",
                "pos": lemma.pos or "",
                "state": ulk.knowledge_state,
                "forms_json": lemma.forms_json,
                "grammar_features_json": lemma.grammar_features_json,
                "thematic_domain": lemma.thematic_domain,
                "frequency_rank": lemma.frequency_rank,
                "due_at": due.isoformat(),
                "stability_days": round(stability, 2),
                "overdue_days": round(overdue_days, 2),
                "passage_uses_7d": int(usage.get("count_7d") or 0),
                "passage_uses_30d": int(usage.get("count_30d") or 0),
                "days_since_passage_target": (
                    round(days_since, 2) if days_since is not None else None
                ),
            }
            word["story_score"] = _passage_target_story_score(word)
            due_rows.append(word)

    due_rows.sort(key=lambda word: (
        int(word["passage_uses_7d"]),
        int(word["passage_uses_30d"]),
        -int(word["story_score"]),
        -min(float(word["overdue_days"]), 45.0),
        int(word["frequency_rank"] or 10**9),
        int(word["lemma_id"]),
    ))
    return due_rows[:limit]


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
            int(w.get("passage_uses_7d") or 0),
            int(w.get("passage_uses_30d") or 0),
            -_passage_target_story_score(w),
            -min(float(w.get("overdue_days") or 0.0), 45.0),
            int(w.get("frequency_rank") or 10**9),
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
            "grammar_features_json": w.get("grammar_features_json"),
            "thematic_domain": w.get("thematic_domain"),
            "frequency_rank": w.get("frequency_rank"),
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
            "forms_json": w.get("forms_json"),
            "grammar_features_json": w.get("grammar_features_json"),
            "thematic_domain": w.get("thematic_domain"),
            "frequency_rank": w.get("frequency_rank"),
            "stability_days": w.get("stability_days"),
            "overdue_days": w.get("overdue_days"),
            "passage_uses_7d": w.get("passage_uses_7d", 0),
            "passage_uses_30d": w.get("passage_uses_30d", 0),
            "days_since_passage_target": w.get("days_since_passage_target"),
            "story_score": _passage_target_story_score(w),
        }
        for w in words
    ]


def _generate_agent_api_fallback(
    kwargs: dict[str, Any],
    cli_error: Exception | str,
) -> dict[str, Any]:
    """Schema-guided fallback with the agent files inlined into the prompt."""
    work_dir = Path(kwargs["work_dir"])
    targets_text = (work_dir / "targets.json").read_text(encoding="utf-8")
    vocab_text = (work_dir / "vocab_prompt.txt").read_text(encoding="utf-8")
    recent_path = work_dir / "recent_passages.json"
    recent_text = (
        recent_path.read_text(encoding="utf-8")
        if recent_path.exists()
        else "[]"
    )
    fallback_prompt = f"""{kwargs['prompt']}

Tool access is unavailable. Here are the referenced file contents.

TARGETS.JSON:
{targets_text}

VOCAB_PROMPT.TXT:
{vocab_text}

RECENT_PASSAGES.JSON:
{recent_text}

Perform the same planning and vocabulary checks internally. Return only the
requested JSON. Every sentence object MUST contain both an `arabic` string and
an `english` translation string. Do not return validator helper fields. The
application will independently validate every token.

EXACT OUTPUT JSON SCHEMA:
{json.dumps(kwargs['json_schema'], ensure_ascii=False, indent=2)}"""
    try:
        return generate_completion(
            prompt=fallback_prompt,
            system_prompt=kwargs["system_prompt"],
            json_schema=kwargs["json_schema"],
            temperature=0.45,
            timeout=kwargs.get("timeout", 300),
            model_override="openai",
            task_type="maintenance_passage_api_fallback",
        )
    except Exception as fallback_exc:
        raise RuntimeError(
            f"Claude tool session failed ({cli_error}); API fallback failed "
            f"({fallback_exc})"
        ) from fallback_exc


def _generate_agent_with_tools(**kwargs) -> dict[str, Any]:
    from limbic.cerebellum.claude_cli import ClaudeCLIError, generate as _limbic_generate
    from app.services.llm import (
        claude_cli_temporarily_disabled,
        mark_claude_cli_unavailable_from_error,
    )

    # Tool use improves vocabulary compliance, but a spent Claude Max quota
    # must not starve an overnight batch. All fallback drafts still traverse
    # every local validator and quality gate before storage.
    if claude_cli_temporarily_disabled():
        return _generate_agent_api_fallback(kwargs, "Claude CLI quota cooldown")

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
        mark_claude_cli_unavailable_from_error(exc)
        return _generate_agent_api_fallback(kwargs, exc)
    return result


def generate_maintenance_passage_agentic(
    target_pool: list[dict[str, Any]],
    known_words: list[dict[str, Any]],
    style: str | None = None,
    sentence_count: int = 4,
    model_override: str = "claude_sonnet",
    feedback: str | None = None,
    recent_passages: list[dict[str, Any]] | None = None,
    narrative_mode: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use a tool-enabled Sonnet session to choose and validate a cohesive passage."""
    if not target_pool:
        raise PassageGenerationError("No maintenance targets available")

    from app.services.sentence_self_correct import _write_batch_files, _write_validator_script

    style = style if style in PASSAGE_STYLES else random.choice(PASSAGE_STYLES)
    sentence_count = max(3, min(5, sentence_count))
    target_pool = _rank_targets_for_passage(target_pool)[:PASSAGE_TARGET_POOL_SIZE]
    recent_passages = list(recent_passages or [])[:PASSAGE_RECENT_CONTEXT_LIMIT]
    narrative_mode = dict(narrative_mode or _select_narrative_mode(recent_passages))
    mode_id = str(narrative_mode["id"])
    morphology_focus = bool(narrative_mode.get("morphology_focus"))

    with tempfile.TemporaryDirectory(prefix="alif-maint-passage-") as work_dir:
        _write_batch_files(
            _agent_rows(known_words),
            work_dir,
            _agent_targets(target_pool),
            prompt_sample_size=PASSAGE_PROMPT_VOCAB_SIZE,
        )
        _write_validator_script(work_dir)
        recent_path = Path(work_dir) / "recent_passages.json"
        recent_path.write_text(
            json.dumps(recent_passages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        prompt = f"""Create one cohesive {sentence_count}-sentence maintenance passage.

Files:
- Candidate due/review target pool: {work_dir}/targets.json
- Supporting learner vocabulary: {work_dir}/vocab_prompt.txt
- Recent passage titles, openings, endings, modes, and targets to avoid echoing:
  {recent_path}
- Validator: python3 {work_dir}/validator.py "<arabic sentence>" "<target_bare>"

Style target: {style}
Assigned narrative shape: {mode_id}
Shape instruction: {narrative_mode['instruction']}
Morphology focus required: {str(morphology_focus).lower()}

Selection rules:
- Read the full target pool before drafting. It is ordered by recent passage
  coverage debt, story suitability, overdue pressure, and frequency—not random.
- Compare at least five possible 2-4 word clusters in scratch. Pick words that
  can occupy different roles in one causal scene (actor/action/object/result),
  not merely words sharing a broad theme.
- Prefer three selected targets. Two is acceptable for an unusually strong
  premise; four is acceptable only when every word belongs naturally.
- Make one selected target recur at least twice. Repetition must change or
  deepen its role rather than copy a clause.
- The ending should reframe or complete the scene gently; it should not exist
  merely to introduce another due word.
- Do not maximize target count at the cost of coherence. Target coverage has no
  value if the result reads like examples.
- Every selected target must appear in the final Arabic. Connector sentences
  may use only support vocabulary.
- Prefer a support-vocabulary anchor that recurs across the passage. Use a due
  target repeatedly where it belongs naturally. Skipped pool words are fine.
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
- Recent passages are negative creative context. Do not reuse their premise,
  central object/animal, opening move, or kind of ending. In particular avoid
  old-house/grandparent/empty-now/remains-in-the-heart pathos unless the shape
  makes something genuinely different and concrete.

Morphology rule:
{f'''- Choose one selected verb target with forms_json.
- Use that SAME lemma in at least three contrasting, correct surface forms
  across the passage: change person, gender, number, or tense as the story
  naturally requires (for example he acted / she acted / they acted).
- Set morphology_target_lemma_id to that verb's lemma ID. Do not count a
  derived noun as an inflected verb form.''' if morphology_focus else '''- Do not force a morphology exercise. Set morphology_target_lemma_id to null.
- Natural inflectional variety is welcome, but story quality comes first.'''}

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
- target_plan: why the chosen targets form one real scene.
- narrative_mode: exactly "{mode_id}".
- morphology_focus: exactly {str(morphology_focus).lower()}.
- ending_kind: a short label for the payoff, not an explanation.
- selected_target_lemma_ids: the 2-4 target words intentionally used."""

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
    if result.get("narrative_mode") != mode_id:
        raise PassageGenerationError("Agentic passage changed the assigned narrative mode")
    if result.get("morphology_focus") is not morphology_focus:
        raise PassageGenerationError("Agentic passage changed morphology-focus assignment")
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


def _review_passage_cohesion(
    validated: list[dict[str, Any]],
    *,
    generated: dict[str, Any] | None = None,
) -> None:
    """Reject passage-shaped bundles that read like unrelated examples."""
    passage = "\n".join(
        f"{idx + 1}. AR: {item['arabic']}\n   EN: {item['english']}"
        for idx, item in enumerate(validated)
    )
    generated = generated or {}
    result = generate_completion(
        prompt=f"""Review this Arabic learner passage as a passage, not as separate sentences.

Reject it if the sentences are merely disconnected examples, if there is no
shared scene/topic/progression, if the final sentence has no payoff, or if the
translation is materially wrong.

Assigned narrative mode: {generated.get('narrative_mode') or 'legacy'}
Stated target plan: {generated.get('target_plan') or ''}
Morphology focus: {bool(generated.get('morphology_focus'))}
Morphology target lemma ID: {generated.get('morphology_target_lemma_id')}

Also reject generic filler prose, mechanical target repetition, a stock
empty-house/remaining-memory ending, or incorrect person/number/tense changes.

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
            "specific_not_generic",
            "repetition_natural",
            "avoids_stock_ending",
            "morphology_correct",
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
    proper_names: set[str] | None = None,
    experiment_version: str | None = None,
    recent_passages: list[dict[str, Any]] | None = None,
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
    proper_name_norms = {
        normalize_alef(strip_tatweel(strip_diacritics(strip_punctuation(name or ""))))
        for name in (proper_names or set())
    }
    proper_name_norms = {name for name in proper_name_norms if name}

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
            proper_names=proper_name_norms,
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
            proper_names=proper_name_norms,
        )
        for mapping in mappings:
            if (
                mapping.lemma_id is None
                and requires_exact_running_text_alias(mapping.surface_form)
            ):
                raise PassageGenerationError(
                    "Passage sentence has unresolved exact-running-text "
                    f"identity: {mapping.surface_form}"
                )
            if mapping.is_proper_name and mapping.lemma_id is None:
                mapping.lemma_id = get_or_create_proper_name_lemma(
                    db,
                    mapping.surface_form,
                    source="passage",
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
                if not lemma.gloss_en and lemma.word_category != "proper_name"
            ]
            if glossless:
                raise PassageGenerationError(f"Passage sentence has glossless lemmas: {glossless}")

        # Target spelling is only a candidate signal. Recompute target identity
        # from resolved mappings so an inflected/homographic surface cannot leave
        # stale target metadata behind.
        mapped_target_ids = [lid for lid in target_order if lid in mapped_ids]
        content_ids = [
            int(m.lemma_id)
            for m in mappings
            if m.lemma_id and not m.is_function_word and not m.is_proper_name
        ]
        if not content_ids:
            raise PassageGenerationError("Passage sentence has no mapped content words")
        primary_id = mapped_target_ids[0] if mapped_target_ids else content_ids[0]

        target_ids_used.update(mapped_target_ids)
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

    target_occurrences: Counter[int] = Counter()
    target_surface_forms: dict[int, set[str]] = {
        lemma_id: set() for lemma_id in target_ids_used
    }
    for item in validated:
        for mapping in item["mappings"]:
            if mapping.lemma_id not in target_ids_used:
                continue
            lemma_id = int(mapping.lemma_id)
            target_occurrences[lemma_id] += 1
            normalized_surface = normalize_alef(
                strip_tatweel(
                    strip_diacritics(strip_punctuation(mapping.surface_form or ""))
                )
            )
            if normalized_surface:
                target_surface_forms[lemma_id].add(normalized_surface)

    if experiment_version:
        declared_target_ids = {
            int(lemma_id)
            for lemma_id in (generated.get("selected_target_lemma_ids") or [])
        }
        if not (
            PASSAGE_MIN_TARGETS_USED
            <= len(declared_target_ids)
            <= PASSAGE_MAX_TARGETS_USED
        ):
            raise PassageGenerationError("Passage must declare 2-4 selected target words")
        missing_targets = declared_target_ids - target_ids_used
        if missing_targets:
            raise PassageGenerationError(
                f"Passage omitted selected targets after mapping: {sorted(missing_targets)}"
            )
        if max(target_occurrences.values(), default=0) < 2:
            raise PassageGenerationError("Passage does not naturally repeat a selected target")

        morphology_focus = bool(generated.get("morphology_focus"))
        morphology_target_raw = generated.get("morphology_target_lemma_id")
        morphology_target_id = (
            int(morphology_target_raw)
            if isinstance(morphology_target_raw, int)
            else None
        )
        if morphology_focus:
            if morphology_target_id not in declared_target_ids:
                raise PassageGenerationError("Morphology target is not a selected target")
            if target_occurrences[morphology_target_id] < 2:
                raise PassageGenerationError("Morphology target did not recur")
            if len(target_surface_forms.get(morphology_target_id, set())) < 2:
                raise PassageGenerationError(
                    "Morphology target did not use contrasting surface forms"
                )
        elif morphology_target_id is not None:
            raise PassageGenerationError("Non-morphology passage declared a morphology target")

    _assert_passage_has_lexical_anchor(validated)

    body_ar = "\n".join(item["arabic"] for item in validated)
    body_en = "\n".join(item["english"] for item in validated)
    if experiment_version:
        _assert_not_recent_plot_echo(body_en, recent_passages or [])

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
        _review_passage_cohesion(validated, generated=generated)

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
            "experiment_version": experiment_version,
            "style_tag": generated.get("style_tag"),
            "narrative_mode": generated.get("narrative_mode"),
            "premise": generated.get("premise"),
            "target_plan": generated.get("target_plan"),
            "ending_kind": generated.get("ending_kind"),
            "morphology_focus": bool(generated.get("morphology_focus")),
            "morphology_target_lemma_id": generated.get("morphology_target_lemma_id"),
            "target_lemma_ids": sorted(target_ids_used),
            "target_occurrence_counts": {
                str(lemma_id): target_occurrences[lemma_id]
                for lemma_id in sorted(target_ids_used)
            },
            "target_surface_form_counts": {
                str(lemma_id): len(target_surface_forms.get(lemma_id, set()))
                for lemma_id in sorted(target_ids_used)
            },
            "sentence_count": len(validated),
            "proper_names": sorted(proper_name_norms),
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
        proper_names=proper_name_norms,
        proper_name_source="passage",
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
    experiment_version: str = PASSAGE_EXPERIMENT_VERSION,
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
        recent_passages = _recent_passage_history(db)
        narrative_mode = _select_narrative_mode(recent_passages)
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

        try:
            draft = generate_maintenance_passage_agentic(
                target_pool=targets,
                known_words=prompt_vocab,
                style=style,
                sentence_count=sentence_count,
                model_override=attempt_model,
                feedback=rejection_feedback,
                recent_passages=recent_passages,
                narrative_mode=narrative_mode,
            )
            selected_ids = {
                int(lemma_id)
                for lemma_id in (draft.get("selected_target_lemma_ids") or [])
            }
            selected_targets = [
                word for word in targets if int(word["lemma_id"]) in selected_ids
            ]
            if len(selected_targets) != len(selected_ids):
                raise PassageGenerationError("Draft selected a target outside the due pool")
            if not (
                PASSAGE_MIN_TARGETS_USED
                <= len(selected_targets)
                <= PASSAGE_MAX_TARGETS_USED
            ):
                raise PassageGenerationError("Draft must select 2-4 due targets")
        except Exception as exc:
            last_error = exc
            rejection_feedback = f"Generation/planning failed because: {exc}"
            continue

        db = SessionLocal()
        try:
            return store_maintenance_passage(
                db,
                draft,
                target_words=selected_targets,
                eligible_words=prompt_vocab,
                quality_gate=True,
                experiment_version=experiment_version,
                recent_passages=recent_passages,
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
