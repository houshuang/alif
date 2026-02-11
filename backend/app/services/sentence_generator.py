"""Sentence generation pipeline.

Orchestrates LLM sentence generation with deterministic validation.
The core loop: generate → validate → retry (up to MAX_RETRIES).
"""

import json
import random
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SentenceWord
from app.services.llm import (
    AllProvidersFailed,
    SentenceResult,
    generate_sentence,
)
from app.services.sentence_validator import (
    FUNCTION_WORDS,
    ValidationResult,
    strip_diacritics,
    tokenize,
    validate_sentence,
)

MAX_RETRIES = 5
KNOWN_SAMPLE_SIZE = 50
MAX_AVOID_WORDS = 20
MIN_WEIGHT = 0.05
DIVERSITY_SENTENCE_THRESHOLD = 15  # scaffold words in this many+ sentences trigger rejection
ALWAYS_AVOID_NAMES = {"محمد", "احمد", "فاطمة", "علي"}


def get_content_word_counts(db: Session) -> dict[int, int]:
    """Count how many sentences each content lemma appears in as a non-target word.

    Returns {lemma_id: distinct_sentence_count}. Excludes function words
    (lemma_id=NULL) and target word appearances.
    """
    rows = (
        db.query(
            SentenceWord.lemma_id,
            sa_func.count(sa_func.distinct(SentenceWord.sentence_id)),
        )
        .filter(
            SentenceWord.lemma_id.isnot(None),
            SentenceWord.is_target_word == False,  # noqa: E712
        )
        .group_by(SentenceWord.lemma_id)
        .all()
    )
    return {lid: cnt for lid, cnt in rows}


def sample_known_words_weighted(
    known_words: list[dict[str, str]],
    content_word_counts: dict[int, int],
    sample_size: int = KNOWN_SAMPLE_SIZE,
    target_lemma_id: int | None = None,
) -> list[dict[str, str]]:
    """Sample known words with inverse-frequency weighting.

    Words appearing in many existing sentences get lower probability,
    biasing generation toward under-represented vocabulary.
    """
    pool = known_words
    if target_lemma_id is not None:
        pool = [w for w in known_words if w.get("lemma_id") != target_lemma_id]

    if len(pool) <= sample_size:
        return pool

    weighted = []
    for w in pool:
        lid = w.get("lemma_id")
        count = content_word_counts.get(lid, 0) if lid else 0
        weight = max(MIN_WEIGHT, 1.0 / (1 + count))
        jittered = weight * random.uniform(0.5, 1.5)
        weighted.append((jittered, w))

    weighted.sort(key=lambda x: x[0], reverse=True)
    return [w for _, w in weighted[:sample_size]]


def get_avoid_words(
    content_word_counts: dict[int, int],
    known_words: list[dict[str, str]],
) -> list[str] | None:
    """Return Arabic forms of the most over-represented content words.

    Threshold: sentence count >= max(median * 2, 3).
    """
    if not content_word_counts:
        return None

    counts = sorted(content_word_counts.values())
    median = statistics.median(counts)
    threshold = max(median * 2, 3)

    lid_to_arabic: dict[int, str] = {}
    for w in known_words:
        lid = w.get("lemma_id")
        if lid is not None:
            lid_to_arabic[lid] = w["arabic"]

    over_represented = [
        (lid, cnt)
        for lid, cnt in content_word_counts.items()
        if cnt >= threshold and lid in lid_to_arabic
    ]
    over_represented.sort(key=lambda x: x[1], reverse=True)

    result = [lid_to_arabic[lid] for lid, _ in over_represented[:MAX_AVOID_WORDS]]

    # Always include common proper names to avoid overuse
    for w in known_words:
        bare = strip_diacritics(w["arabic"])
        if bare in ALWAYS_AVOID_NAMES and w["arabic"] not in result:
            result.append(w["arabic"])

    return result or None


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


def _check_scaffold_diversity(
    arabic_text: str,
    target_bare: str,
    content_word_counts: dict[int, int],
    lemma_lookup: dict[str, int],
) -> tuple[bool, list[str]]:
    """Check if a sentence's scaffold words are diverse enough.

    Returns (passes, list_of_overused_words_to_avoid).
    Rejects sentences with more than 1 scaffold word appearing in
    DIVERSITY_SENTENCE_THRESHOLD+ existing sentences.
    """
    tokens = tokenize(arabic_text)
    overused: list[str] = []
    for tok in tokens:
        bare = strip_diacritics(tok)
        if bare == target_bare or bare in FUNCTION_WORDS:
            continue
        lid = lemma_lookup.get(bare)
        count = content_word_counts.get(lid, 0) if lid else 0
        if count >= DIVERSITY_SENTENCE_THRESHOLD:
            overused.append(tok)
    return len(overused) <= 1, overused


def generate_validated_sentence(
    target_arabic: str,
    target_translation: str,
    known_words: list[dict[str, str]],
    difficulty_hint: str = "beginner",
    max_words: int | None = None,
    content_word_counts: dict[int, int] | None = None,
    target_lemma_id: int | None = None,
    lemma_lookup: dict[str, int] | None = None,
) -> GeneratedSentence:
    """Generate and validate a sentence with retry loop.

    Args:
        target_arabic: Arabic word with diacritics (e.g. "كِتَاب").
        target_translation: English translation.
        known_words: Full list of user's known words as
                     [{"arabic": "...", "english": "..."}].
        difficulty_hint: Difficulty level for prompt.
        content_word_counts: Per-lemma sentence counts for diversity weighting.
        target_lemma_id: Lemma ID of the target word (excluded from sampling).

    Returns:
        GeneratedSentence with validated sentence data.

    Raises:
        GenerationError: If all retries fail.
    """
    # Sample known words for prompt with diversity weighting when available
    if content_word_counts is not None:
        sample = sample_known_words_weighted(
            known_words, content_word_counts, KNOWN_SAMPLE_SIZE, target_lemma_id
        )
        avoid_words = get_avoid_words(content_word_counts, known_words)
    else:
        sample = (
            random.sample(known_words, KNOWN_SAMPLE_SIZE)
            if len(known_words) > KNOWN_SAMPLE_SIZE
            else known_words
        )
        avoid_words = None

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
                avoid_words=avoid_words,
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
            # Post-validation diversity check
            if content_word_counts and lemma_lookup:
                diverse, overused = _check_scaffold_diversity(
                    result.arabic, target_bare, content_word_counts, lemma_lookup,
                )
                if not diverse:
                    overused_str = "، ".join(overused)
                    retry_feedback = (
                        f"For diversity, avoid using: {overused_str} "
                        "— they already appear in too many sentences. "
                        "Use different vocabulary."
                    )
                    continue

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
