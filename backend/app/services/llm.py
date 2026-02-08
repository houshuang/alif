"""LLM service using LiteLLM with multi-model fallback.

Primary: Gemini Flash (fast, cheap)
Fallback: GPT-4o-mini (reliable)
"""

import json
import os
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


# Model configs in priority order
MODELS = [
    {
        "name": "gemini",
        "model": "gemini/gemini-2.5-flash",
        "key_env": "GEMINI_KEY",
        "key_setting": "gemini_key",
    },
    {
        "name": "openai",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_KEY",
        "key_setting": "openai_key",
    },
]


def _get_api_key(model_config: dict) -> str | None:
    """Get API key from settings or environment."""
    key = getattr(settings, model_config["key_setting"], "")
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
) -> dict[str, Any]:
    """Call LLM with automatic fallback across providers.

    Returns parsed JSON dict when json_mode=True, otherwise raw content string
    wrapped as {"content": "..."}.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    errors: list[str] = []

    for model_config in MODELS:
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
                return json.loads(content)
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


SENTENCE_SYSTEM_PROMPT = """\
You are an Arabic language tutor creating MSA (Modern Standard Arabic) sentences \
for reading practice. Create natural, meaningful sentences using specific vocabulary.

Rules:
- Write grammatically correct MSA (fusha)
- Use only the vocabulary provided (known words + target word + common function words)
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، هذا، هذه، \
هو، هي، أنا، أنت، ما، لا، أن، إن، كان، ليس، هل، لم، لن، قد، الذي، التي
- Include full diacritics (tashkeel) on all Arabic words
- The transliteration must use ALA-LC standard with macrons for long vowels

Respond with JSON only: {"arabic": "...", "english": "...", "transliteration": "..."}"""


def generate_sentence(
    target_word: str,
    target_translation: str,
    known_words: list[dict[str, str]],
    difficulty_hint: str = "beginner",
    retry_feedback: str | None = None,
    max_words: int | None = None,
) -> SentenceResult:
    """Generate a single Arabic sentence featuring the target word.

    Args:
        target_word: Arabic word (with diacritics) to include.
        target_translation: English meaning of target word.
        known_words: List of {"arabic": ..., "english": ...} dicts.
        difficulty_hint: "beginner", "intermediate", or "advanced".
        retry_feedback: Feedback from a previous failed attempt.
        max_words: Maximum word count for the sentence (for cognitive load management).

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

    prompt = f"""Create a natural MSA sentence for a {difficulty_hint} Arabic learner.

TARGET WORD (must appear in the sentence):
- {target_word} ({target_translation})

KNOWN WORDS (you may use these):
{known_list}

Do NOT use Arabic content words outside the lists above (function words are fine).
{length_instruction}
Include full diacritics on all Arabic text.
"""

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
