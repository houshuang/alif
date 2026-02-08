"""Sentence generation pipeline.

Orchestrates LLM sentence generation with deterministic validation.
The core loop: generate → validate → retry (up to MAX_RETRIES).
"""

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config import settings
from app.services.llm import (
    AllProvidersFailed,
    SentenceResult,
    generate_sentence,
)
from app.services.sentence_validator import (
    ValidationResult,
    strip_diacritics,
    validate_sentence,
)

MAX_RETRIES = 3
KNOWN_SAMPLE_SIZE = 50


class GeneratedSentence(BaseModel):
    arabic: str
    english: str
    transliteration: str
    target_word: str
    target_translation: str
    validation: dict[str, Any]
    attempts: int


class GenerationError(Exception):
    pass


def _log_generation(
    log_dir: Path,
    target_word: str,
    attempt: int,
    sentence_result: SentenceResult | None,
    validation: ValidationResult | None,
    error: str | None = None,
) -> None:
    """Log a sentence generation attempt."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"sentence_gen_{datetime.now():%Y-%m-%d}.jsonl"
    entry = {
        "ts": datetime.now().isoformat(),
        "event": "sentence_generation",
        "target_word": target_word,
        "attempt": attempt,
        "arabic": sentence_result.arabic if sentence_result else None,
        "valid": validation.valid if validation else False,
        "issues": validation.issues if validation else [error or "no result"],
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def generate_validated_sentence(
    target_arabic: str,
    target_translation: str,
    known_words: list[dict[str, str]],
    difficulty_hint: str = "beginner",
    max_words: int | None = None,
) -> GeneratedSentence:
    """Generate and validate a sentence with retry loop.

    Args:
        target_arabic: Arabic word with diacritics (e.g. "كِتَاب").
        target_translation: English translation.
        known_words: Full list of user's known words as
                     [{"arabic": "...", "english": "..."}].
        difficulty_hint: Difficulty level for prompt.

    Returns:
        GeneratedSentence with validated sentence data.

    Raises:
        GenerationError: If all retries fail.
    """
    # Sample known words for prompt (don't send hundreds)
    sample = (
        random.sample(known_words, KNOWN_SAMPLE_SIZE)
        if len(known_words) > KNOWN_SAMPLE_SIZE
        else known_words
    )

    # Build the known bare forms set for validation
    known_bare = {strip_diacritics(w["arabic"]) for w in known_words}

    target_bare = strip_diacritics(target_arabic)

    retry_feedback: str | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = generate_sentence(
                target_word=target_arabic,
                target_translation=target_translation,
                known_words=sample,
                difficulty_hint=difficulty_hint,
                retry_feedback=retry_feedback,
                max_words=max_words,
            )
        except AllProvidersFailed as e:
            _log_generation(
                settings.log_dir, target_arabic, attempt, None, None, str(e)
            )
            raise GenerationError(f"LLM providers unavailable: {e}") from e

        validation = validate_sentence(
            arabic_text=result.arabic,
            target_bare=target_bare,
            known_bare_forms=known_bare,
        )

        _log_generation(
            settings.log_dir, target_arabic, attempt, result, validation
        )

        if validation.valid:
            return GeneratedSentence(
                arabic=result.arabic,
                english=result.english,
                transliteration=result.transliteration,
                target_word=target_arabic,
                target_translation=target_translation,
                validation={
                    "known_words": validation.known_words,
                    "function_words": validation.function_words,
                    "target_found": validation.target_found,
                },
                attempts=attempt,
            )

        # Build feedback for retry
        retry_feedback = "; ".join(validation.issues)

    raise GenerationError(
        f"Failed to generate valid sentence after {MAX_RETRIES} attempts "
        f"for '{target_arabic}'. Last issues: {retry_feedback}"
    )
