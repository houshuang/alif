"""LLM service using LiteLLM with multi-model fallback.

Primary: Gemini Flash (fast, cheap)
Fallback: GPT (reliable)
Tertiary: Claude Haiku (quality)
"""

import json
import os
import re
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
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def generate_completion(
    prompt: str,
    system_prompt: str = "",
    json_mode: bool = True,
    temperature: float = 0.7,
    timeout: int = 60,
    model_override: str | None = None,
) -> dict[str, Any]:
    """Call LLM with automatic fallback across providers.

    Returns parsed JSON dict when json_mode=True, otherwise raw content string
    wrapped as {"content": "..."}.

    When model_override is provided (e.g. "gemini", "openai", "anthropic"),
    only that specific model is tried — no fallback to others.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

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
            )

    raise AllProvidersFailed(f"All LLM providers failed: {'; '.join(errors)}")


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
- Do NOT translate English syntax literally into Arabic"""

DIFFICULTY_STYLE_GUIDE = """\
Style by difficulty level:
- very simple / simple: prefer SVO. Short nominal sentences. Basic connectors (و). \
Simple tenses. No embedded clauses.
- beginner: mix SVO and VSO. Simple idafa. Introduce فَ and ثُمَّ. One clause per sentence.
- intermediate: more VSO. Relative clauses (الَّذِي/الَّتِي). Questions with هَلْ. \
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
- Each sentence should be 4-8 words long
- Each sentence should use a DIFFERENT syntactic structure (vary VSO/SVO, nominal/verbal, question/statement)
- Transliteration: ALA-LC standard with macrons for long vowels

Respond with JSON: {{"sentences": [{{"arabic": "...", "english": "...", "transliteration": "..."}}, ...]}}"""


def generate_sentence(
    target_word: str,
    target_translation: str,
    known_words: list[dict[str, str]],
    difficulty_hint: str = "beginner",
    retry_feedback: str | None = None,
    max_words: int | None = None,
    avoid_words: list[str] | None = None,
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
    known_list = "\n".join(
        f"- {w['arabic']} ({w['english']})" for w in known_words
    )

    word_count_range = "5-12"
    if max_words:
        word_count_range = f"3-{max_words}"

    length_instruction = f"The sentence should be natural and meaningful, {word_count_range} words long."
    if max_words and max_words <= 5:
        length_instruction = (
            f"The sentence MUST be very short: {word_count_range} words only. "
            "Keep it as simple as possible to minimize cognitive load."
        )

    avoid_instruction = ""
    if avoid_words:
        avoid_str = "، ".join(avoid_words)
        avoid_instruction = f"\nFor variety, try NOT to use these overused words (pick other vocabulary instead): {avoid_str}\n"

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
        temperature=0.8,
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
) -> list[SentenceResult]:
    """Generate multiple sentences for a target word in a single LLM call.

    Returns up to `count` SentenceResult objects (may be fewer if parsing fails).
    """
    known_list = "\n".join(
        f"- {w['arabic']} ({w['english']})" for w in known_words
    )

    avoid_instruction = ""
    if avoid_words:
        avoid_str = "، ".join(avoid_words)
        avoid_instruction = f"\nFor variety, try NOT to use these overused words (pick other vocabulary instead): {avoid_str}"

    rejected_instruction = ""
    if rejected_words:
        rejected_str = "، ".join(rejected_words)
        rejected_instruction = (
            f"\nPREVIOUS ATTEMPTS FAILED because you used words NOT in the vocabulary list: {rejected_str}\n"
            f"Do NOT use these words. Use ONLY words from the VOCABULARY list above."
        )

    prompt = f"""Create {count} different natural MSA sentences for a {difficulty_hint} Arabic learner.

TARGET WORD (must appear in every sentence):
- {target_word} ({target_translation})

VOCABULARY (you may ONLY use these Arabic content words, plus function words):
{known_list}

IMPORTANT: Do NOT use any Arabic content words that are not in the list above.
Each sentence should be 4-8 words, with a different structure or context.
Include full diacritics on all Arabic text.
{avoid_instruction}{rejected_instruction}
Respond with JSON: {{"sentences": [{{"arabic": "...", "english": "...", "transliteration": "..."}}, ...]}}"""

    result = generate_completion(
        prompt=prompt,
        system_prompt=BATCH_SENTENCE_SYSTEM_PROMPT,
        json_mode=True,
        temperature=0.5,
        model_override=model_override,
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
