"""LLM service using Claude CLI (free via Max plan) with API fallback.

All text generation routes through Claude CLI (`claude -p`), free with Max plan:
  - Sentence generation: claude_sonnet (87% pass rate, better than Gemini's 73%)
  - Quality gate, enrichment, tagging, flags: claude_haiku
  - Story generation: opus (via claude_code.py)

API fallback (when CLI unavailable, e.g. network issues):
  GPT-5.2 → Claude Haiku API

Gemini is used ONLY for vision/OCR tasks (separate code path in ocr_service.py).

Claude CLI requires `claude` installed and authenticated (`claude setup-token`).
"""

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import litellm
from pydantic import BaseModel

from app.config import settings

litellm.set_verbose = False

# Strip CLAUDECODE env var to allow nested invocation from Claude Code sessions
_CLEAN_ENV = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


class LLMError(Exception):
    pass


class AllProvidersFailed(LLMError):
    pass


class SentenceResult(BaseModel):
    arabic: str
    english: str
    transliteration: str


class MultiTargetSentenceResult(BaseModel):
    arabic: str
    english: str
    transliteration: str
    target_words_used: list[str]


class BatchWordSentenceResult(BaseModel):
    target_index: int
    target_word: str
    arabic: str
    english: str
    transliteration: str


# API fallback models — used only when Claude CLI is unavailable.
# Gemini removed from text chain (2026-04-01): now OCR-only via ocr_service.py.
MODELS = [
    {
        "name": "openai",
        "model": "gpt-5.2",
        "key_env": "OPENAI_KEY",
        "key_setting": "openai_key",
    },
    {
        "name": "anthropic",
        "model": "claude-haiku-4-5",
        "key_env": "ANTHROPIC_API_KEY",
        "key_setting": "anthropic_api_key",
        "key_settings": ["anthropic_api_key", "anthropic_key"],
    },
    {
        "name": "opus",
        "model": "claude-opus-4-6",
        "key_env": "ANTHROPIC_API_KEY",
        "key_setting": "anthropic_api_key",
        "key_settings": ["anthropic_api_key", "anthropic_key"],
    },
]


def _get_api_key(model_config: dict) -> str | None:
    """Get API key from settings or environment."""
    for setting_name in model_config.get("key_settings", [model_config["key_setting"]]):
        key = getattr(settings, setting_name, "")
        if key:
            return key
    return os.environ.get(model_config["key_env"], "") or None


def _log_call(
    log_dir: Path,
    model: str,
    success: bool,
    response_time: float,
    error: str | None = None,
    prompt_length: int = 0,
    task_type: str | None = None,
) -> None:
    """Append a log entry for the LLM call."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"llm_calls_{datetime.now():%Y-%m-%d}.jsonl"
    entry = {
        "ts": datetime.now().isoformat(),
        "event": "llm_call",
        "model": model,
        "success": success,
        "response_time_s": round(response_time, 2),
        "error": error,
        "prompt_length": prompt_length,
    }
    if task_type:
        entry["task_type"] = task_type
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _claude_cli_available() -> bool:
    """Check if claude CLI is installed and authenticated."""
    return shutil.which("claude") is not None


def _generate_via_claude_cli(
    prompt: str,
    system_prompt: str,
    model: str,
    json_mode: bool = True,
    json_schema: dict | None = None,
    timeout: int = 120,
    task_type: str | None = None,
) -> dict[str, Any]:
    """Generate completion via Claude CLI (`claude -p`). Free with Max plan.

    Delegates to `limbic.cerebellum.claude_cli.generate` so every call lands
    in the shared cost_log (script="claude-cli", project="alif"). Keeps the
    existing `_log_call` analytics writes for per-day JSONL stats, and
    preserves the legacy return shape: parsed dict in json_mode, else
    `{"content": "..."}`. Raises `LLMError` on any failure.

    When json_schema is provided, uses Claude CLI's --json-schema for
    constrained decoding — the model can ONLY produce valid JSON matching
    the schema. No text parsing needed; result comes back pre-parsed.
    """
    from limbic.cerebellum.claude_cli import ClaudeCLIError, generate as _limbic_generate

    if not _claude_cli_available():
        raise LLMError("claude CLI not available — install with: npm install -g @anthropic-ai/claude-code")

    start = time.time()
    try:
        content, _meta = _limbic_generate(
            prompt=prompt,
            project="alif",
            purpose=task_type or "",
            system=system_prompt,
            model=model,
            schema=json_schema,
            timeout=timeout,
        )
    except ClaudeCLIError as e:
        elapsed = time.time() - start
        _log_call(
            settings.log_dir, f"claude_cli/{model}", False, elapsed,
            error=str(e)[:200], prompt_length=len(prompt), task_type=task_type,
        )
        raise LLMError(str(e)) from e

    elapsed = time.time() - start
    _log_call(
        settings.log_dir, f"claude_cli/{model}", True, elapsed,
        prompt_length=len(prompt), task_type=task_type,
    )

    if not json_mode and not json_schema:
        return {"content": content if isinstance(content, str) else str(content)}

    # With json_schema, limbic returns a pre-parsed dict from structured_output
    if isinstance(content, dict):
        return content

    # json_mode without schema: limbic returned text. Parse it.
    text = (content if isinstance(content, str) else str(content)).strip()

    # Try direct parse first (model returned pure JSON)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Models often wrap JSON in markdown fences with explanation text before/after.
    # Search anywhere in the response, not just at the start.
    fence_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?\s*```', text)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Last resort: find the first { ... } JSON object in the text
    brace_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise LLMError(f"claude CLI returned unparseable JSON: {text[:200]}")


def generate_completion(
    prompt: str,
    system_prompt: str = "",
    json_mode: bool = True,
    json_schema: dict | None = None,
    temperature: float = 0.7,
    timeout: int = 60,
    model_override: str | None = None,
    task_type: str | None = None,
    cli_only: bool = False,
) -> dict[str, Any]:
    """Call LLM with automatic fallback across providers.

    Returns parsed JSON dict when json_mode=True, otherwise raw content string
    wrapped as {"content": "..."}.

    json_schema: when provided with CLI models, uses --json-schema for
    constrained decoding. The model can ONLY produce valid JSON matching the
    schema — no text wrapping, no parsing failures. Implies json_mode=True.

    When model_override is provided (e.g. "claude_sonnet", "claude_haiku", "openai"),
    only that specific model is tried — no fallback to others.

    cli_only: if True and model is a CLI model, don't fall back to API chain on
              CLI failure. Use for tasks where API models perform poorly (e.g.
              Arabic morphology verification — GPT-5.2 is too aggressive).

    task_type: optional label for analytics (e.g. "sentence_gen", "quality_review").
    """
    if json_schema:
        json_mode = True

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    # Claude CLI models — shell out to `claude -p` (free via Max plan)
    # Falls back to API chain if CLI unavailable
    CLAUDE_CLI_MODELS = {
        "claude_sonnet": "sonnet",
        "claude_haiku": "haiku",
    }

    # When no model_override specified, try Claude CLI (haiku) first — it's free
    cli_model = model_override if model_override in CLAUDE_CLI_MODELS else (None if model_override else "claude_haiku")
    if cli_model and cli_model in CLAUDE_CLI_MODELS:
        try:
            return _generate_via_claude_cli(
                prompt=prompt,
                system_prompt=system_prompt,
                model=CLAUDE_CLI_MODELS[cli_model],
                json_mode=json_mode,
                json_schema=json_schema,
                timeout=timeout,
                task_type=task_type,
            )
        except LLMError:
            if cli_only:
                raise AllProvidersFailed(f"Claude CLI failed for {cli_model} and cli_only=True")
            # CLI unavailable — fall through to API chain
            if model_override and model_override in CLAUDE_CLI_MODELS:
                model_override = None  # don't try to find "claude_haiku" in MODELS

    if model_override:
        models_to_try = [m for m in MODELS if m["name"] == model_override]
        if not models_to_try:
            raise LLMError(f"Unknown model override: {model_override}")
    else:
        models_to_try = MODELS

    errors: list[str] = []

    for model_config in models_to_try:
        api_key = _get_api_key(model_config)
        if not api_key:
            continue

        start = time.time()
        try:
            kwargs: dict[str, Any] = {
                "model": model_config["model"],
                "messages": messages,
                "temperature": temperature,
                "timeout": timeout,
                "api_key": api_key,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            response = litellm.completion(**kwargs)
            elapsed = time.time() - start

            content = response.choices[0].message.content
            _log_call(
                settings.log_dir,
                model_config["model"],
                True,
                elapsed,
                prompt_length=len(prompt),
                task_type=task_type,
            )

            if json_mode:
                # Some models wrap JSON in markdown fences
                text = content.strip()
                if text.startswith("```"):
                    match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?\s*```', text)
                    text = match.group(1).strip() if match else text.strip()
                return json.loads(text)
            return {"content": content}

        except Exception as e:
            elapsed = time.time() - start
            error_msg = f"{model_config['name']}: {e}"
            errors.append(error_msg)
            _log_call(
                settings.log_dir,
                model_config["model"],
                False,
                elapsed,
                error=str(e),
                prompt_length=len(prompt),
                task_type=task_type,
            )

    raise AllProvidersFailed(f"All LLM providers failed: {'; '.join(errors)}")


def format_known_words_by_pos(known_words: list[dict]) -> str:
    """Format known words grouped by part of speech for clearer LLM prompts."""
    groups: dict[str, list[str]] = {"NOUNS": [], "VERBS": [], "ADJECTIVES": [], "OTHER": []}
    for w in known_words:
        pos = (w.get("pos") or "").lower()
        entry = f"{w['arabic']} ({w['english']})"
        if pos in ("noun", "noun_prop"):
            groups["NOUNS"].append(entry)
        elif pos == "verb":
            groups["VERBS"].append(entry)
        elif pos in ("adj", "adj_comp"):
            groups["ADJECTIVES"].append(entry)
        else:
            groups["OTHER"].append(entry)
    lines = []
    for label, words in groups.items():
        if words:
            lines.append(f"{label}: {', '.join(words)}")
    return "\n".join(lines) if lines else "\n".join(f"- {w['arabic']} ({w['english']})" for w in known_words)


ARABIC_STYLE_RULES = """\
Arabic naturalness rules:
- Mix VSO and SVO word order. VSO is more formal/classical; SVO more contemporary
- VSO agreement: verb matches person + gender only, NOT number: ذَهَبَ الطُّلَّابُ
- SVO agreement: verb matches person + gender + number: الطُّلَّابُ ذَهَبُوا
- Mix nominal sentences (descriptions/states) with verbal sentences (actions/events)
- NO copula: never insert هُوَ/هِيَ as "is" with indefinite predicates. \
Write مُحَمَّدٌ طَبِيبٌ NOT مُحَمَّدٌ هُوَ طَبِيبٌ
- Separator pronoun (ضمير الفصل) ONLY when both subject AND predicate are definite: \
مُحَمَّدٌ هُوَ المُدِيرُ
- Idafa: first noun has NO ال and NO tanween: كِتَابُ الطَّالِبِ
- Correct i'rab: nominative ضمة, accusative فتحة, genitive كسرة. Tanween on indefinites.
- Use connectors naturally: و (and), فَ (so/immediately), ثُمَّ (then/after delay), لَكِنَّ (but)
- Vary sentence length — mix short and long clauses
- Do NOT translate English syntax literally into Arabic

Sentence completeness — CRITICAL:
- NEVER start a nominal sentence with an indefinite noun (نكرة). Use definite or verb-first:
  ✓ الوَلَدُ يَلْعَبُ (The boy plays) — definite subject
  ✓ يَلْعَبُ وَلَدٌ فِي الحَدِيقَةِ (A boy plays in the park) — verb-first OK with indefinite
  ✓ هُنَاكَ وَلَدٌ فِي الحَدِيقَةِ (There is a boy in the park) — existential OK
  ✗ وَلَدٌ يَلْعَبُ (A boy plays) — bare indefinite start, sounds like a caption
  ✗ مَطْبَخٌ وَاسِعٌ وَنَافِذَةٌ هُنَا (A kitchen and a window here) — catalog fragment
- Every sentence must express a complete thought — an action, state, or relation. \
Avoid catalog-style adjective stacking or noun lists.

Redundant pronouns — CRITICAL:
- In VSO order, the verb conjugation already encodes the subject. Do NOT add a pronoun:
  ✓ ذَهَبَتْ إِلَى المَدْرَسَةِ (She went to school)
  ✗ ذَهَبَتْ هِيَ إِلَى المَدْرَسَةِ (She went she to school — redundant)
  ✗ تَسْكُنُ هِيَ بِجَانِبِ (She lives she next to — redundant)
- Use explicit pronouns only for emphasis/contrast: ذَهَبَ هُوَ وَلَمْ تَذْهَبْ هِيَ

Semantic coherence in compound sentences:
- When joining clauses with و/ثُمَّ/لَكِنَّ, they MUST be logically related:
  ✓ فِي الكُوَيْتِ مَطَرٌ فَأَخَذْتُ مِظَلَّةً (In Kuwait it is raining, so I took an umbrella)
  ✗ فِي الكُوَيْتِ مَطَرٌ وَالبَاصُ بَعِيدٌ (In Kuwait it is raining, and the bus is far — unrelated)
- Do NOT combine unrelated facts into one sentence

Semantic plausibility — CRITICAL:
- Every sentence must be something a real Arabic speaker could plausibly say or write \
in some real context (news, conversation, story, instruction, poetry). Before writing each \
sentence, ask: "Would this appear in any natural Arabic text?" If no, rewrite.
- Do NOT invent proper names or epithets by combining content words. \
Names like الأميرة حب التوت ("Princess Mulberry Love") or الجندي قمر الليل ("Soldier Night Moon") \
are unacceptable — they read as a workaround to satisfy vocabulary constraints, not as Arabic.
- Proper names: use ONLY proper names that appear in the provided vocabulary list. \
If no suitable name is available, use a generic definite noun instead \
(الوَلَد، المُعَلِّم، الفَتَاة، الرَّجُل، الطِّفْل، الجَارَة).
- Do NOT force unrelated target words into one sentence. If two target words cannot \
naturally co-occur in any realistic scenario, produce a sentence using only one of them \
and omit the other — it is better to drop a target than to write nonsense.
- Reject abstract-noun stacking that reads like a dictionary entry: \
"In the balcony, watering, and a very nice color" is not a sentence. \
Every clause needs an actor and an action/state with concrete meaning."""

DIFFICULTY_STYLE_GUIDE = """\
Style by difficulty level:
- very simple / simple: prefer SVO. Short nominal sentences. Basic connectors (و). \
Simple tenses. No embedded clauses. Only modern everyday vocabulary.
- beginner: mix SVO and VSO. Simple idafa. Introduce فَ and ثُمَّ. One clause per sentence. \
Do NOT use classical/archaic particles (لَعَلَّ، كَأَنَّ، إِذَا، لَوْلَا، حَيْثُ). \
Do NOT use overly formal register (يا سادة، أيها). \
Keep language modern and conversational.
- intermediate: more VSO. Relative clauses (الَّذِي/الَّتِي). \
Negation with لَمْ/لَنْ. Idafa chains. Dialogue with قَالَ.
- advanced: VSO default. Embedded clauses. Classical particles (إِنَّ، لَعَلَّ، كَأَنَّ). \
Formal register approaching classical style."""

SENTENCE_SYSTEM_PROMPT = f"""\
You are an Arabic language tutor creating MSA (fusha) sentences for reading practice. \
Write sentences a native speaker would find natural — not textbook constructions.

{ARABIC_STYLE_RULES}

{DIFFICULTY_STYLE_GUIDE}

Vocabulary constraint:
- Use ONLY the provided vocabulary + target word + common function words
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، هذا، هذه، \
هو، هي، أنا، أنت، ما، لا، أن، إن، كان، ليس، هل، لم، لن، قد، الذي، التي
- Include full diacritics (tashkeel) on ALL Arabic words with correct i'rab
- Include Arabic punctuation: use ؟ for questions, . for statements, ، between clauses
- Transliteration: ALA-LC standard with macrons for long vowels

Sentence structure variety:
- Do NOT default to هَلْ questions — only use هَلْ when the target word specifically requires a question
- Vary starters: verbal (verb-first), nominal (definite noun/adjective), prepositional (فِي، مِنَ), time expressions
- Prefer pronouns (أنا، هو، هي، نحن) and generic definite nouns (المعلم، الولد، الفتاة) as subjects
- Use proper names sparingly — avoid محمد/أحمد/فاطمة/علي unless they are in the vocabulary list

Respond with JSON only: {{"arabic": "...", "english": "...", "transliteration": "..."}}"""


BATCH_SENTENCE_SYSTEM_PROMPT = f"""\
You are an Arabic language tutor creating MSA (fusha) sentences for reading practice. \
You generate multiple varied sentences for a target word. Each sentence must sound natural, \
not like a textbook exercise.

{ARABIC_STYLE_RULES}

{DIFFICULTY_STYLE_GUIDE}

Vocabulary constraint:
- Use ONLY words from the provided vocabulary, the target word, and common function words
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، ك، هذا، هذه، \
ذلك، تلك، هو، هي، أنا، أنت، نحن، هم، ما، لا، أن، إن، كان، كانت، ليس، هل، لم، \
لن، قد، الذي، التي، كل، بعض، هنا، هناك، الآن، جدا، فقط، أيضا، أو، ثم، لكن
- Do NOT invent or use Arabic content words not in the vocabulary list
- Include full diacritics (tashkeel) on ALL Arabic words with correct i'rab
- Include Arabic punctuation: use ؟ for questions, . for statements, ، between clauses
- Match the word count range specified in the user prompt
- Each sentence should use a DIFFERENT syntactic structure (vary VSO/SVO, nominal/verbal, question/statement)
- Transliteration: ALA-LC standard with macrons for long vowels

Sentence structure variety:
- Do NOT default to هَلْ questions — only use هَلْ when the target word specifically requires a question
- Vary starters across sentences: verbal (verb-first), nominal (definite noun/adjective), prepositional, time expressions
- Prefer pronouns (أنا، هو، هي، نحن) and generic definite nouns (المعلم، الولد، الفتاة) as subjects
- Use proper names sparingly — avoid محمد/أحمد/فاطمة/علي unless they are in the vocabulary list
- Never start more than one sentence in a batch with the same word.

Respond with JSON: {{"sentences": [{{"arabic": "...", "english": "...", "transliteration": "..."}}, ...]}}"""


def _format_target_example_block(
    target_example_ar: str | None,
    target_example_en: str | None,
) -> str:
    """Render an EXAMPLE block to anchor the lemma's correct sense.

    Returns an empty string when either side is missing — we only show the
    block when we have both Arabic and English so the LLM gets unambiguous
    sense grounding. (Phase 4: enrich prompt with already-existing examples
    to prevent wrong-sense awkward sentences like the استَوَى/dust case.)
    """
    if not target_example_ar or not target_example_en:
        return ""
    return (
        f"Example of correct usage:\n"
        f"- {target_example_ar.strip()} → {target_example_en.strip()}\n"
    )


def generate_sentence(
    target_word: str,
    target_translation: str,
    known_words: list[dict[str, str]],
    difficulty_hint: str = "beginner",
    retry_feedback: str | None = None,
    max_words: int | None = None,
    avoid_words: list[str] | None = None,
    model_override: str = "claude_sonnet",
    target_example_ar: str | None = None,
    target_example_en: str | None = None,
) -> SentenceResult:
    """Generate a single Arabic sentence featuring the target word.

    Args:
        target_word: Arabic word (with diacritics) to include.
        target_translation: English meaning of target word.
        known_words: List of {"arabic": ..., "english": ...} dicts.
        difficulty_hint: "beginner", "intermediate", or "advanced".
        retry_feedback: Feedback from a previous failed attempt.
        max_words: Maximum word count for the sentence (for cognitive load management).
        avoid_words: Arabic words to avoid for diversity.
        target_example_ar: Optional canonical Arabic example sentence (anchors
            the correct sense for polysemous lemmas). Omitted from prompt when
            None.
        target_example_en: Optional English translation of the canonical
            example.

    Returns:
        SentenceResult with arabic, english, transliteration.
    """
    known_list = format_known_words_by_pos(known_words)

    word_count_range = "6-12"
    if max_words:
        min_words = max(5, max_words - 3)
        word_count_range = f"{min_words}-{max_words}"

    length_instruction = f"The sentence should be natural and meaningful, {word_count_range} words long."

    avoid_instruction = ""
    if avoid_words:
        avoid_str = "، ".join(avoid_words)
        avoid_instruction = f"\nDo NOT use these overused words — using them will cause rejection: {avoid_str}\nChoose different vocabulary instead.\n"

    example_block = _format_target_example_block(target_example_ar, target_example_en)

    prompt = f"""Create a natural MSA sentence for a {difficulty_hint} Arabic learner.

TARGET WORD (must appear in the sentence):
- {target_word} ({target_translation})
{example_block}
KNOWN WORDS (you may use these):
{known_list}

Do NOT use Arabic content words outside the lists above (function words are fine).
{length_instruction}
Include full diacritics on all Arabic text.
{avoid_instruction}"""

    if retry_feedback:
        prompt += f"\nPREVIOUS ATTEMPT FAILED: {retry_feedback}\nPlease fix and try again.\n"

    prompt += '\nRespond with JSON: {"arabic": "...", "english": "...", "transliteration": "..."}'

    result = generate_completion(
        prompt=prompt,
        system_prompt=SENTENCE_SYSTEM_PROMPT,
        json_mode=True,
        temperature=0.5,
        model_override=model_override,
        task_type="sentence_gen",
    )

    return SentenceResult(
        arabic=result.get("arabic", ""),
        english=result.get("english", ""),
        transliteration=result.get("transliteration", ""),
    )


def generate_sentences_batch(
    target_word: str,
    target_translation: str,
    known_words: list[dict[str, str]],
    count: int = 5,
    difficulty_hint: str = "beginner",
    model_override: str = "claude_sonnet",
    avoid_words: list[str] | None = None,
    rejected_words: list[str] | None = None,
    max_words: int | None = None,
    target_example_ar: str | None = None,
    target_example_en: str | None = None,
    rerank: bool = True,
    rerank_top_k: int = 2,
) -> list[SentenceResult]:
    """Generate multiple sentences for a target word in a single LLM call,
    then optionally rerank by naturalness via a Haiku quality gate.

    Default ``count`` was bumped from 3 to 5 in Phase 4 of the awkward-sentence
    work so the Haiku reranker has a real choice to make. When ``rerank=True``
    (default), the candidates are scored by ``rerank_sentences_by_naturalness``
    and only the top ``rerank_top_k`` GOOD ones are returned. If the reranker
    rejects all candidates, returns ``[]`` — caller decides whether to retry or
    fall back to single-sentence generation.

    Returns up to ``count`` SentenceResult objects when ``rerank=False`` (legacy
    behavior), or up to ``rerank_top_k`` when reranking is enabled.
    """
    known_list = format_known_words_by_pos(known_words)

    avoid_instruction = ""
    if avoid_words:
        avoid_str = "، ".join(avoid_words)
        avoid_instruction = f"\nDo NOT use these overused words — using them will cause rejection: {avoid_str}\nChoose different vocabulary instead."

    rejected_instruction = ""
    if rejected_words:
        rejected_str = "، ".join(rejected_words)
        rejected_instruction = (
            f"\nPREVIOUS ATTEMPTS FAILED because you used words NOT in the vocabulary list: {rejected_str}\n"
            f"Do NOT use these words. Use ONLY words from the VOCABULARY list above."
        )

    if max_words:
        min_words = max(5, max_words - 3)
        word_range = f"{min_words}-{max_words}"
    else:
        word_range = "6-10"

    example_block = _format_target_example_block(target_example_ar, target_example_en)

    prompt = f"""Create {count} different natural MSA sentences for a {difficulty_hint} Arabic learner.

TARGET WORD (must appear in every sentence):
- {target_word} ({target_translation})
{example_block}
VOCABULARY (you may ONLY use these Arabic content words, plus function words):
{known_list}

IMPORTANT: Do NOT use any Arabic content words that are not in the list above.
Each sentence should be {word_range} words, with a different structure or context.
Include full diacritics on all Arabic text.
{avoid_instruction}{rejected_instruction}
Respond with JSON: {{"sentences": [{{"arabic": "...", "english": "...", "transliteration": "..."}}, ...]}}"""

    result = generate_completion(
        prompt=prompt,
        system_prompt=BATCH_SENTENCE_SYSTEM_PROMPT,
        json_mode=True,
        temperature=0.5,
        model_override=model_override,
        task_type="sentence_gen_batch",
    )

    sentences: list[SentenceResult] = []
    if isinstance(result, list):
        raw_list = result
    elif isinstance(result, dict):
        raw_list = result.get("sentences", [])
    else:
        return sentences
    if not isinstance(raw_list, list):
        return sentences

    for item in raw_list[:count]:
        if not isinstance(item, dict):
            continue
        arabic = item.get("arabic", "").strip()
        english = item.get("english", "").strip()
        transliteration = item.get("transliteration", "").strip()
        if arabic and english:
            sentences.append(SentenceResult(
                arabic=arabic,
                english=english,
                transliteration=transliteration,
            ))

    if rerank and sentences:
        try:
            return rerank_sentences_by_naturalness(
                sentences,
                target_word=target_word,
                target_translation=target_translation,
                top_k=rerank_top_k,
            )
        except (LLMError, AllProvidersFailed) as exc:
            # Fail-open: rerank is a bonus quality filter; if Haiku times out or
            # produces malformed output, fall back to the unranked candidates so
            # we don't regress availability. CLAUDE.md §13 — we explicitly log
            # so silent fallback doesn't mask repeated failures.
            _log_call(
                settings.log_dir, "claude_cli/haiku-rerank", False,
                response_time=0.0, error=f"rerank failed: {str(exc)[:160]}",
                prompt_length=0, task_type="sentence_rerank",
            )
            return sentences

    return sentences


# ---------------------------------------------------------------------------
# Phase 4 candidate ranker — Haiku-scored naturalness filter
# ---------------------------------------------------------------------------

# Categories shared with /tmp/claude/awkward/simulate_naturalness_v2.py
_RERANK_CATEGORIES = [
    "CONTEXT_DEPENDENT",
    "CONTINUATION",
    "STORY_SPECIFIC",
    "DIALOGUE_FRAGMENT",
    "FORCED_COMBINATION",
    "WRONG_SENSE",
    "NO_VERB",
    "OK",
]

_RERANK_PROMPT_HEADER = """You are an editor selecting Arabic sentences to use as STANDALONE flashcard examples for an MSA learner. The learner sees one sentence with no other context.

You are deliberately STRICT. The pool of candidate sentences is huge, so when in doubt, REJECT.

Reject (verdict: BAD) when ANY of the following apply:

1. CONTEXT_DEPENDENT — the sentence has a 3rd-person pronoun ("he/she/it/they/them/his/her" — هو/هي/هم/ها/ه/ـها), demonstrative used as subject ("this/that" — هذا/هذه/ذلك), or definite-article noun (الـ X) that points to a specific previously-mentioned referent the learner cannot resolve.
   - GOOD: "The fox learns from experience" (proverbial, "the fox" is generic)
   - GOOD: "She felt great satisfaction after completing the homework" (subject pronoun + clear context within sentence)
   - BAD: "She must be moving at great speed" (who is she?)
   - BAD: "Take from this bag what you need" (which bag?)
   - BAD: "After that, he stopped to listen" (after what? who?)

2. CONTINUATION — opens with و (and), ف (so), ثم (then), لكن (but), or فلما/وعندما/فإذا at the start, suggesting it's a continuation of prior text.
   - Exception: if the و/ف is part of the verb itself (e.g., وَعَدَ "promised", فَهِمَ "understood"), not a connector.

3. STORY_SPECIFIC — references a named character, event, or object specific to a narrative the learner doesn't know.

4. DIALOGUE_FRAGMENT — line of quoted speech with insufficient narrative frame to make sense.

5. FORCED_COMBINATION — words individually fit but the topic combination is bizarre or non-sequitur.
   - BAD: "I drank cold licorice then hung the coat in the closet" (no semantic link between clauses)

6. WRONG_SENSE — a polysemous word is used in a sense that doesn't fit the context. The TARGET_WORD line below names the intended sense; reject if a candidate uses a different sense.
   - BAD: "The teacher talked about justice, then the school bell buzzed" (دَوَّى = "to buzz of insects" is wrong sense for a bell)
   - BAD: "Art and heritage leveled among the participants" (استَوَى = "to settle/sit straight" is wrong sense — "leveled among" is nonsensical)

7. NO_VERB / NOT_A_SENTENCE — sentence fragments, headings, lists.

Otherwise verdict: GOOD.

Be strict — when uncertain about anaphor or topic coherence, choose BAD. The cost of a false positive is low; the learner has many other sentences."""


_RERANK_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["GOOD", "BAD"]},
                    "category": {"type": "string", "enum": _RERANK_CATEGORIES},
                    "explanation": {"type": "string"},
                },
                "required": ["index", "verdict", "category", "explanation"],
            },
        },
    },
    "required": ["verdicts"],
}


def rerank_sentences_by_naturalness(
    sentences: list[SentenceResult],
    target_word: str,
    target_translation: str,
    top_k: int = 2,
) -> list[SentenceResult]:
    """Score candidate sentences with the Haiku v2 naturalness gate, return top GOOD.

    Single Haiku CLI call rates all sentences in one shot. Phase 4 of the
    awkward-sentence prevention work; targets the long-tail wrong-sense and
    forced-combination cases that no per-lemma fix scales to.

    Behavior:
      - All N candidates scored in one prompt
      - Returns up to ``top_k`` sentences whose verdict is GOOD, preserving
        the LLM's reported order so callers see the most natural picks first
      - If fewer than ``top_k`` are GOOD, returns however many are GOOD
      - If all are BAD, returns ``[]`` — caller is expected to either retry
        generation or fall back to single-sentence generation
      - Raises LLMError on parse failure / no verdicts; ``generate_sentences_batch``
        catches this and falls back to the unranked candidates so Phase 4 quality
        work never regresses availability
    """
    if not sentences:
        return []

    numbered = "\n".join(
        f"[{i}] Arabic: {s.arabic}\n    English: {s.english}"
        for i, s in enumerate(sentences)
    )
    prompt = f"""{_RERANK_PROMPT_HEADER}

TARGET_WORD: {target_word} ({target_translation})

Score each of the {len(sentences)} candidate sentences below. Return ONE verdict object per candidate (use the same `index` you see in brackets). Use category OK when the verdict is GOOD; otherwise pick the matching reject category.

Candidates:
{numbered}"""

    result = generate_completion(
        prompt=prompt,
        system_prompt=(
            "You are an expert Arabic linguist filtering candidate flashcard "
            "sentences. Be deliberately strict — the pool is large, so when in "
            "doubt, mark BAD. Return one verdict per candidate, keyed by index."
        ),
        json_schema=_RERANK_SCHEMA,
        temperature=0.0,
        model_override="claude_haiku",
        task_type="sentence_rerank",
        cli_only=True,
    )

    verdicts = result.get("verdicts", []) if isinstance(result, dict) else []
    if not isinstance(verdicts, list) or not verdicts:
        # Treat empty/malformed as a soft failure so the caller's except path
        # falls back to unranked candidates rather than dropping everything.
        raise LLMError("rerank returned no verdicts")

    good_indices: list[int] = []
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        idx = v.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(sentences):
            continue
        if str(v.get("verdict", "")).upper() == "GOOD":
            good_indices.append(idx)

    # Preserve original LLM ordering; dedupe in case Haiku repeats an index.
    seen: set[int] = set()
    ordered: list[int] = []
    for idx in good_indices:
        if idx in seen:
            continue
        seen.add(idx)
        ordered.append(idx)

    return [sentences[i] for i in ordered[:top_k]]


# ---------------------------------------------------------------------------
# Multi-word batch generation — one CLI call for many target words
# ---------------------------------------------------------------------------

BATCH_MULTI_WORD_SYSTEM_PROMPT = f"""\
You are an Arabic language tutor creating MSA (fusha) sentences for reading practice. \
You receive a list of target words and create sentences for each one. Each sentence \
targets exactly one word from the list and must sound natural.

{ARABIC_STYLE_RULES}

{DIFFICULTY_STYLE_GUIDE}

Vocabulary constraint:
- Use ONLY words from the provided vocabulary, the target words, and common function words
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، ك، هذا، هذه، \
ذلك، تلك، هو، هي، أنا، أنت، نحن، هم، ما، لا، أن، إن، كان، كانت، ليس، هل، لم، \
لن، قد، الذي، التي، كل، بعض، هنا، هناك، الآن، جدا، فقط، أيضا، أو، ثم، لكن
- Do NOT invent or use Arabic content words not in the vocabulary list
- Include full diacritics (tashkeel) on ALL Arabic words with correct i'rab
- Include Arabic punctuation: use ؟ for questions, . for statements, ، between clauses
- Match the word count range specified in the user prompt
- Each sentence should use a DIFFERENT syntactic structure (vary VSO/SVO, nominal/verbal, question/statement)
- Transliteration: ALA-LC standard with macrons for long vowels

Sentence structure variety:
- Do NOT default to هَلْ questions — only use هَلْ when the target word specifically requires a question
- Vary starters across sentences: verbal (verb-first), nominal (definite noun/adjective), prepositional, time expressions
- Prefer pronouns (أنا، هو، هي، نحن) and generic definite nouns (المعلم، الولد، الفتاة) as subjects
- Use proper names sparingly — avoid محمد/أحمد/فاطمة/علي unless they are in the vocabulary list
- Never start more than one sentence in a batch with the same word.

Respond with JSON: {{"sentences": [{{"target_index": 0, "target_word": "...", "arabic": "...", "english": "...", "transliteration": "..."}}, ...]}}"""


def generate_sentences_for_words(
    target_words: list[dict[str, str]],
    known_words: list[dict[str, str]],
    count_per_word: int = 2,
    difficulty_hint: str = "simple",
    model_override: str = "claude_sonnet",
    avoid_words: list[str] | None = None,
    max_words: int | None = None,
    timeout: int = 180,
) -> list[BatchWordSentenceResult]:
    """Generate sentences for multiple target words in a single LLM call.

    Each sentence targets exactly one word. Returns tagged results so the
    caller can route each sentence to the correct word's pipeline.
    """
    known_list = format_known_words_by_pos(known_words)

    target_list = "\n".join(
        f"{i + 1}. {tw['arabic']} ({tw['english']})"
        for i, tw in enumerate(target_words)
    )

    avoid_instruction = ""
    if avoid_words:
        avoid_str = "، ".join(avoid_words)
        avoid_instruction = f"\nDo NOT use these overused words — using them will cause rejection: {avoid_str}\nChoose different vocabulary instead."

    if max_words:
        min_words = max(5, max_words - 3)
        word_range = f"{min_words}-{max_words}"
    else:
        word_range = "6-10"

    total_sentences = count_per_word * len(target_words)
    prompt = f"""Create {count_per_word} different natural MSA sentences for EACH of the following target words.
Each sentence must contain exactly ONE target word from the list.

TARGET WORDS:
{target_list}

VOCABULARY (you may ONLY use these Arabic content words, plus the target words, plus function words):
{known_list}

IMPORTANT: Do NOT use any Arabic content words that are not in the lists above.
Each sentence should be {word_range} words, with a different structure or context.
For each target word, make the {count_per_word} sentences as different as possible in structure and vocabulary.
Include full diacritics on all Arabic text.
{avoid_instruction}
Respond with JSON: {{"sentences": [{{"target_index": <0-based index>, "target_word": "<Arabic>", "arabic": "...", "english": "...", "transliteration": "..."}}, ...]}}
You should return exactly {total_sentences} sentences ({count_per_word} per target word)."""

    result = generate_completion(
        prompt=prompt,
        system_prompt=BATCH_MULTI_WORD_SYSTEM_PROMPT,
        json_mode=True,
        temperature=0.5,
        timeout=timeout,
        model_override=model_override,
        task_type="sentence_gen_batch_words",
    )

    # Parse results
    sentences: list[BatchWordSentenceResult] = []
    if isinstance(result, list):
        raw_list = result
    elif isinstance(result, dict):
        raw_list = result.get("sentences", [])
    else:
        return sentences
    if not isinstance(raw_list, list):
        return sentences

    # Build bare-form lookup for cross-check
    from app.services.sentence_validator import strip_diacritics
    target_bares = [strip_diacritics(tw["arabic"]) for tw in target_words]

    for item in raw_list:
        if not isinstance(item, dict):
            continue
        arabic = item.get("arabic", "").strip()
        english = item.get("english", "").strip()
        transliteration = item.get("transliteration", "").strip()
        if not arabic or not english:
            continue

        target_index = item.get("target_index")
        target_word = item.get("target_word", "").strip()

        # Validate target_index
        if target_index is not None and isinstance(target_index, int):
            if 0 <= target_index < len(target_words):
                # Cross-check: does target_word match?
                expected_bare = target_bares[target_index]
                actual_bare = strip_diacritics(target_word) if target_word else ""
                if actual_bare and actual_bare != expected_bare:
                    # Try to find the right index by matching target_word
                    matched_idx = None
                    for j, tb in enumerate(target_bares):
                        if actual_bare == tb:
                            matched_idx = j
                            break
                    if matched_idx is not None:
                        target_index = matched_idx
                    # else: trust the index, word might have diacritic variation
            else:
                target_index = None

        # Fallback: match by target_word if index is missing/invalid
        if target_index is None and target_word:
            actual_bare = strip_diacritics(target_word)
            for j, tb in enumerate(target_bares):
                if actual_bare == tb:
                    target_index = j
                    break

        if target_index is None:
            continue  # can't map this sentence to any target

        sentences.append(BatchWordSentenceResult(
            target_index=target_index,
            target_word=target_words[target_index]["arabic"],
            arabic=arabic,
            english=english,
            transliteration=transliteration,
        ))

    return sentences


MULTI_TARGET_SYSTEM_PROMPT = f"""\
You are an Arabic language tutor creating MSA (fusha) sentences for reading practice. \
Each sentence must include AT LEAST 2 of the specified target words. \
Write sentences a native speaker would find natural — not textbook constructions.

{ARABIC_STYLE_RULES}

{DIFFICULTY_STYLE_GUIDE}

Vocabulary constraint:
- Use ONLY words from the provided vocabulary, the target words, and common function words
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، ك، هذا، هذه، \
ذلك، تلك، هو، هي، أنا، أنت، نحن، هم، ما، لا، أن، إن، كان، كانت، ليس، هل، لم، \
لن، قد، الذي، التي، كل، بعض، هنا، هناك، الآن، جدا، فقط، أيضا، أو، ثم، لكن
- Do NOT invent or use Arabic content words not in the vocabulary list
- Include full diacritics (tashkeel) on ALL Arabic words with correct i'rab
- Include Arabic punctuation: use ؟ for questions, . for statements، ، between clauses
- Each sentence should use a DIFFERENT syntactic structure (vary VSO/SVO, nominal/verbal, question/statement)
- Transliteration: ALA-LC standard with macrons for long vowels
- Vary which target word combinations you use across sentences

Sentence structure variety:
- Do NOT default to هَلْ questions — only use هَلْ when the target word specifically requires a question
- Vary starters across sentences: verbal (verb-first), nominal (definite noun/adjective), prepositional, time expressions
- Prefer pronouns (أنا، هو، هي، نحن) and generic definite nouns (المعلم، الولد، الفتاة) as subjects
- Use proper names sparingly — avoid محمد/أحمد/فاطمة/علي unless they are in the vocabulary list
- Never start more than one sentence in a batch with the same word.

For each sentence, list which target words appear in it.

Respond with JSON: {{"sentences": [{{"arabic": "...", "english": "...", "transliteration": "...", "target_words_used": ["word1", "word2"]}}, ...]}}"""


def generate_sentences_multi_target(
    target_words: list[dict[str, str]],
    known_words: list[dict[str, str]],
    count: int = 4,
    difficulty_hint: str = "beginner",
    model_override: str = "claude_sonnet",
    avoid_words: list[str] | None = None,
    max_words: int | None = None,
) -> list[MultiTargetSentenceResult]:
    """Generate sentences that each include 2+ target words from the given set.

    Args:
        target_words: List of {"arabic": ..., "english": ...} for target words.
        known_words: List of {"arabic": ..., "english": ...} for known vocab.
        count: Number of sentences to generate.
        difficulty_hint: Difficulty level.
        model_override: LLM model to use.
        avoid_words: Words to avoid for diversity.
        max_words: Max word count per sentence.

    Returns:
        List of MultiTargetSentenceResult objects.
    """
    known_list = format_known_words_by_pos(known_words)
    target_list = "\n".join(
        f"- {w['arabic']} ({w['english']})" for w in target_words
    )

    avoid_instruction = ""
    if avoid_words:
        avoid_str = "، ".join(avoid_words)
        avoid_instruction = f"\nDo NOT use these overused words — using them will cause rejection: {avoid_str}\nChoose different vocabulary instead."

    if max_words:
        min_words = max(5, max_words - 3)
        word_range = f"{min_words}-{max_words}"
    else:
        word_range = "6-12"

    prompt = f"""Create {count} different natural MSA sentences for a {difficulty_hint} Arabic learner.

TARGET WORDS (each sentence MUST include at least 2 of these):
{target_list}

VOCABULARY (you may ONLY use these Arabic content words, plus the target words, plus function words):
{known_list}

IMPORTANT: Do NOT use any Arabic content words that are not in the lists above.
Each sentence MUST naturally include at least 2 of the target words.
Vary which target word combinations you use across sentences.
Each sentence should be {word_range} words, with a different structure or context.
Include full diacritics on all Arabic text.
{avoid_instruction}
Respond with JSON: {{"sentences": [{{"arabic": "...", "english": "...", "transliteration": "...", "target_words_used": ["word1", "word2"]}}, ...]}}"""

    result = generate_completion(
        prompt=prompt,
        system_prompt=MULTI_TARGET_SYSTEM_PROMPT,
        json_mode=True,
        temperature=0.5,
        model_override=model_override,
        task_type="sentence_gen_multi",
    )

    sentences: list[MultiTargetSentenceResult] = []
    if isinstance(result, list):
        raw_list = result
    elif isinstance(result, dict):
        raw_list = result.get("sentences", [])
    else:
        return sentences
    if not isinstance(raw_list, list):
        return sentences

    for item in raw_list[:count]:
        if not isinstance(item, dict):
            continue
        arabic = item.get("arabic", "").strip()
        english = item.get("english", "").strip()
        transliteration = item.get("transliteration", "").strip()
        target_words_used = item.get("target_words_used", [])
        if not isinstance(target_words_used, list):
            target_words_used = []
        if arabic and english:
            sentences.append(MultiTargetSentenceResult(
                arabic=arabic,
                english=english,
                transliteration=transliteration,
                target_words_used=target_words_used,
            ))

    return sentences


# --- Sentence Quality Review (Claude Haiku via CLI) ---

class SentenceReviewResult(BaseModel):
    natural: bool
    translation_correct: bool
    reason: str


def review_sentences_quality(
    sentences: list[dict[str, str]],
) -> list[SentenceReviewResult]:
    """Review sentences for naturalness and translation accuracy using Claude Haiku.

    Args:
        sentences: List of {"arabic": "...", "english": "..."} dicts.

    Returns:
        List of SentenceReviewResult, one per input sentence.
        On LLM failure, returns all-fail results (fail closed).
    """
    if not sentences:
        return []

    schema = {
        "type": "object",
        "properties": {
            "reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "natural": {"type": "boolean"},
                        "translation_correct": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "natural", "translation_correct", "reason"],
                },
            },
        },
        "required": ["reviews"],
    }

    prompt = """Review each Arabic sentence for a language learning app. For each:
1. Is the Arabic grammatically correct AND something a real Arabic speaker could plausibly say?
2. Is the English translation accurate?

REJECT (natural=false) for any of:
- Grammar errors (wrong gender agreement, incorrect verb forms, broken syntax, wrong i'rab)
- Translation errors (English doesn't match Arabic meaning, singular/plural mismatch)
- Nonsensical meaning (word salad, contradictory clauses)
- Invented proper names or epithets built from content words \
(e.g. الأميرة حب التوت "Princess Mulberry Love" — a name fabricated to satisfy a word list)
- Implausible scenarios no native speaker would write in any context \
(news, conversation, story, instruction, poetry)
- Catalog-style fragments: abstract nouns listed without an actor or action \
(e.g. "In the balcony, watering, and a very nice color")

ACCEPT (natural=true) even if:
- The scenario is simple or textbook-like (these are for learners)
- Word choices are slightly formal or informal
- Vocabulary is basic

Respond with JSON: {"reviews": [{"id": 1, "natural": true, "translation_correct": true, "reason": "..."}, ...]}

Sentences:
"""
    for i, s in enumerate(sentences, 1):
        prompt += f'{i}. Arabic: {s["arabic"]}\n   English: {s["english"]}\n\n'

    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt=(
                "You are an expert Arabic linguist reviewing sentences for a "
                "language learning app. Judge naturalness by asking: could a real "
                "Arabic speaker plausibly say this in any context? Accept simple or "
                "textbook-style sentences (appropriate for learners). Reject "
                "grammatical but implausible sentences, invented names built from "
                "content words, and catalog fragments without an actor or action."
            ),
            json_schema=schema,
            temperature=0.0,
            model_override="claude_haiku",
            task_type="quality_review",
        )
    except (AllProvidersFailed, LLMError):
        return [SentenceReviewResult(natural=False, translation_correct=False, reason="quality review unavailable") for _ in sentences]

    items = result.get("reviews", []) if isinstance(result, dict) else []
    if not isinstance(items, list):
        return [SentenceReviewResult(natural=False, translation_correct=False, reason="quality review parse error") for _ in sentences]

    reviews: list[SentenceReviewResult] = []
    for i in range(len(sentences)):
        if i < len(items) and isinstance(items[i], dict):
            item = items[i]
            reviews.append(SentenceReviewResult(
                natural=bool(item.get("natural", True)),
                translation_correct=bool(item.get("translation_correct", True)),
                reason=str(item.get("reason", "")),
            ))
        else:
            reviews.append(SentenceReviewResult(natural=False, translation_correct=False, reason="quality review incomplete"))
    return reviews
