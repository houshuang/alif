"""LLM service using LiteLLM with multi-model fallback.

Sentence generation: Gemini Flash (on-demand, fast) or Claude CLI (background, free)
General tasks: Gemini Flash (fast, cheap) → GPT-5.2 fallback → Claude Haiku tertiary
Quality gate: Claude Haiku API, fail-closed (rejects on LLM failure)

Claude CLI models (claude_sonnet, claude_haiku) use `claude -p` from the Max plan (free).
They require the `claude` CLI to be installed and authenticated (`claude setup-token`).
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


MODELS = [
    {
        "name": "gemini",
        "model": "gemini/gemini-3-flash-preview",
        "key_env": "GEMINI_KEY",
        "key_setting": "gemini_key",
    },
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
    timeout: int = 120,
    task_type: str | None = None,
) -> dict[str, Any]:
    """Generate completion via Claude CLI (`claude -p`). Free with Max plan.

    Uses --output-format json for structured responses.
    """
    if not _claude_cli_available():
        raise LLMError("claude CLI not available — install with: npm install -g @anthropic-ai/claude-code")

    cmd = [
        "claude", "-p",
        "--tools", "",
        "--output-format", "json",
        "--model", model,
        "--no-session-persistence",
    ]
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        _log_call(settings.log_dir, f"claude_cli/{model}", False, elapsed,
                  error=f"timeout after {timeout}s", prompt_length=len(prompt), task_type=task_type)
        raise LLMError(f"claude CLI timed out after {timeout}s")

    elapsed = time.time() - start

    if proc.returncode != 0:
        _log_call(settings.log_dir, f"claude_cli/{model}", False, elapsed,
                  error=proc.stderr[:200], prompt_length=len(prompt), task_type=task_type)
        raise LLMError(f"claude CLI exited {proc.returncode}: {proc.stderr[:200]}")

    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError:
        _log_call(settings.log_dir, f"claude_cli/{model}", False, elapsed,
                  error="invalid JSON response", prompt_length=len(prompt), task_type=task_type)
        raise LLMError(f"claude CLI returned invalid JSON: {proc.stdout[:200]}")

    if response.get("is_error"):
        _log_call(settings.log_dir, f"claude_cli/{model}", False, elapsed,
                  error=response.get("result", "unknown"), prompt_length=len(prompt), task_type=task_type)
        raise LLMError(f"claude CLI error: {response.get('result', 'unknown')}")

    _log_call(settings.log_dir, f"claude_cli/{model}", True, elapsed,
              prompt_length=len(prompt), task_type=task_type)

    content = response.get("result", "")

    if json_mode:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Claude CLI sometimes appends extra data — try to extract first JSON object/array
            decoder = json.JSONDecoder()
            return decoder.raw_decode(text)[0]
    return {"content": content}


def generate_completion(
    prompt: str,
    system_prompt: str = "",
    json_mode: bool = True,
    temperature: float = 0.7,
    timeout: int = 60,
    model_override: str | None = None,
    task_type: str | None = None,
) -> dict[str, Any]:
    """Call LLM with automatic fallback across providers.

    Returns parsed JSON dict when json_mode=True, otherwise raw content string
    wrapped as {"content": "..."}.

    When model_override is provided (e.g. "gemini", "openai", "anthropic"),
    only that specific model is tried — no fallback to others.

    task_type: optional label for analytics (e.g. "sentence_gen", "quality_review").
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    # Claude CLI models — shell out to `claude -p` (free via Max plan)
    # Falls back to default API chain if CLI unavailable (e.g. inside Docker)
    CLAUDE_CLI_MODELS = {
        "claude_sonnet": "sonnet",
        "claude_haiku": "haiku",
    }
    if model_override and model_override in CLAUDE_CLI_MODELS:
        try:
            return _generate_via_claude_cli(
                prompt=prompt,
                system_prompt=system_prompt,
                model=CLAUDE_CLI_MODELS[model_override],
                json_mode=json_mode,
                timeout=timeout,
                task_type=task_type,
            )
        except LLMError:
            # CLI unavailable — fall through to default API chain
            model_override = None

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
                    text = re.sub(r"^```(?:json)?\s*", "", text)
                    text = re.sub(r"\s*```$", "", text)
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
- Do NOT combine unrelated facts into one sentence"""

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


def generate_sentence(
    target_word: str,
    target_translation: str,
    known_words: list[dict[str, str]],
    difficulty_hint: str = "beginner",
    retry_feedback: str | None = None,
    max_words: int | None = None,
    avoid_words: list[str] | None = None,
    model_override: str = "gemini",
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

    prompt = f"""Create a natural MSA sentence for a {difficulty_hint} Arabic learner.

TARGET WORD (must appear in the sentence):
- {target_word} ({target_translation})

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
    count: int = 3,
    difficulty_hint: str = "beginner",
    model_override: str = "gemini",
    avoid_words: list[str] | None = None,
    rejected_words: list[str] | None = None,
    max_words: int | None = None,
) -> list[SentenceResult]:
    """Generate multiple sentences for a target word in a single LLM call.

    Returns up to `count` SentenceResult objects (may be fewer if parsing fails).
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
    prompt = f"""Create {count} different natural MSA sentences for a {difficulty_hint} Arabic learner.

TARGET WORD (must appear in every sentence):
- {target_word} ({target_translation})

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
    model_override: str = "gemini",
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


# --- Sentence Quality Review (Gemini Flash) ---

class SentenceReviewResult(BaseModel):
    natural: bool
    translation_correct: bool
    reason: str


def review_sentences_quality(
    sentences: list[dict[str, str]],
) -> list[SentenceReviewResult]:
    """Review sentences for naturalness and translation accuracy using Gemini Flash.

    Args:
        sentences: List of {"arabic": "...", "english": "..."} dicts.

    Returns:
        List of SentenceReviewResult, one per input sentence.
        On LLM failure, returns all-fail results (fail closed).
    """
    if not sentences:
        return []

    prompt = """Review each Arabic sentence for a language learning app. For each:
1. Is the Arabic grammatically correct and comprehensible?
2. Is the English translation accurate?

Only reject sentences with:
- Grammar errors (wrong gender agreement, incorrect verb forms, broken syntax)
- Translation errors (English doesn't match Arabic meaning, singular/plural mismatch)
- Nonsensical or incomprehensible meaning (word salad, contradictory clauses)

Do NOT reject sentences just because:
- The scenario is unusual (a boy putting a book in a kitchen is fine)
- The style is simple or textbook-like (these are for language learners)
- Word choices are slightly informal or formal

Respond with JSON array:
[{"id": 1, "natural": true/false, "translation_correct": true/false, "reason": "..."}]

Sentences:
"""
    for i, s in enumerate(sentences, 1):
        prompt += f'{i}. Arabic: {s["arabic"]}\n   English: {s["english"]}\n\n'

    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt=(
                "You are an expert Arabic linguist reviewing sentences for a "
                "language learning app. Focus on grammar correctness and translation "
                "accuracy. Accept simple or textbook-style sentences — they are "
                "appropriate for learners. Only reject sentences with clear errors."
            ),
            json_mode=True,
            temperature=0.0,
            model_override="claude_haiku",
            task_type="quality_review",
        )
    except (AllProvidersFailed, LLMError):
        return [SentenceReviewResult(natural=False, translation_correct=False, reason="quality review unavailable") for _ in sentences]

    # Parse — result may be a list directly or {"reviews": [...]}
    items = result if isinstance(result, list) else result.get("reviews", result.get("sentences", []))
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
