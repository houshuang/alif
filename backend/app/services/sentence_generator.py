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
    MultiTargetSentenceResult,
    SentenceResult,
    generate_sentence,
    generate_sentences_multi_target,
    review_sentences_quality,
)
from app.services.sentence_validator import (
    FUNCTION_WORDS,
    MultiTargetValidationResult,
    ValidationResult,
    strip_diacritics,
    tokenize,
    validate_sentence,
    validate_sentence_multi_target,
)

MAX_RETRIES = 7
KNOWN_SAMPLE_SIZE = 500
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
    validation_words: list[dict[str, str]] | None = None,
    model_override: str = "gemini",
) -> GeneratedSentence:
    """Generate and validate a sentence with retry loop.

    Args:
        target_arabic: Arabic word with diacritics (e.g. "كِتَاب").
        target_translation: English translation.
        known_words: Words shown to GPT as allowed vocabulary.
        difficulty_hint: Difficulty level for prompt.
        content_word_counts: Per-lemma sentence counts for diversity weighting.
        target_lemma_id: Lemma ID of the target word (excluded from sampling).
        validation_words: Broader set of acceptable words for validation
                         (e.g. includes encountered). If None, uses known_words.

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
    # Use lemma_lookup keys (includes inflected forms from forms_json) if available,
    # otherwise fall back to validation_words
    if lemma_lookup:
        known_bare = set(lemma_lookup.keys())
    else:
        validate_from = validation_words if validation_words is not None else known_words
        known_bare = {strip_diacritics(w["arabic"]) for w in validate_from}

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
                model_override=model_override,
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

            # Gemini quality review
            reviews = review_sentences_quality(
                [{"arabic": result.arabic, "english": result.english}]
            )
            if reviews and (not reviews[0].natural or not reviews[0].translation_correct):
                retry_feedback = (
                    f"Quality review rejected: {reviews[0].reason}. "
                    "Generate a more natural, meaningful sentence."
                )
                _log_generation(
                    settings.log_dir, target_arabic, attempt, result, validation,
                    error=f"quality_review_failed: {reviews[0].reason}",
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


class MultiTargetGeneratedSentence(BaseModel):
    arabic: str
    english: str
    transliteration: str
    target_lemma_ids: list[int]
    primary_target_lemma_id: int
    target_bares_found: dict[str, bool]
    attempts: int


def group_words_for_multi_target(
    word_lemmas: list[dict],
    max_group_size: int = 4,
    min_group_size: int = 2,
) -> list[list[dict]]:
    """Group words into sets for multi-target sentence generation.

    Avoids putting words with the same root in the same group (would
    produce confusing sentences). Each dict must have at least:
    {"lemma_id": int, "lemma_ar": str, "gloss_en": str, "root_id": int|None}

    Returns list of groups, each a list of word dicts.
    """
    if len(word_lemmas) < min_group_size:
        return []

    remaining = list(word_lemmas)
    random.shuffle(remaining)
    groups: list[list[dict]] = []

    while len(remaining) >= min_group_size:
        group: list[dict] = []
        group_root_ids: set[int | None] = set()
        skipped: list[dict] = []

        for word in remaining:
            if len(group) >= max_group_size:
                skipped.append(word)
                continue
            root_id = word.get("root_id")
            if root_id is not None and root_id in group_root_ids:
                skipped.append(word)
                continue
            group.append(word)
            if root_id is not None:
                group_root_ids.add(root_id)

        if len(group) >= min_group_size:
            groups.append(group)
        remaining = skipped

    return groups


MULTI_TARGET_MAX_RETRIES = 3


def generate_validated_sentences_multi_target(
    target_words: list[dict],
    known_words: list[dict[str, str]],
    existing_sentence_counts: dict[int, int] | None = None,
    count: int = 4,
    difficulty_hint: str = "beginner",
    max_words: int | None = None,
    content_word_counts: dict[int, int] | None = None,
    avoid_words: list[str] | None = None,
    validation_words: list[dict[str, str]] | None = None,
    lemma_lookup: dict[str, int] | None = None,
    model_override: str = "gemini",
) -> list[MultiTargetGeneratedSentence]:
    """Generate and validate sentences targeting multiple words.

    Args:
        target_words: List of dicts with lemma_id, lemma_ar, gloss_en.
        known_words: Words shown to GPT as allowed vocabulary.
        existing_sentence_counts: {lemma_id: count} to determine primary target.
        count: Number of sentences to generate.
        difficulty_hint: Difficulty level.
        max_words: Max word count per sentence.
        content_word_counts: For diversity weighting.
        avoid_words: Words to avoid.
        validation_words: Broader set for validation (e.g. includes encountered).
                         If None, uses known_words.

    Returns:
        List of validated MultiTargetGeneratedSentence objects.
    """
    # Build target bare forms -> lemma_id mapping
    target_bares: dict[str, int] = {}
    for tw in target_words:
        bare = strip_diacritics(tw["lemma_ar"])
        target_bares[bare] = tw["lemma_id"]

    # Include target bares in known set for validation
    if lemma_lookup:
        known_bare = set(lemma_lookup.keys())
    else:
        validate_from = validation_words if validation_words is not None else known_words
        known_bare = {strip_diacritics(w["arabic"]) for w in validate_from}
    all_bare = known_bare | set(target_bares.keys())

    # Build LLM target list
    llm_targets = [
        {"arabic": tw["lemma_ar"], "english": tw.get("gloss_en", "")}
        for tw in target_words
    ]

    # Sample known words for prompt
    if content_word_counts is not None:
        sample = sample_known_words_weighted(
            known_words, content_word_counts, KNOWN_SAMPLE_SIZE
        )
    else:
        sample = (
            random.sample(known_words, KNOWN_SAMPLE_SIZE)
            if len(known_words) > KNOWN_SAMPLE_SIZE
            else known_words
        )

    valid_sentences: list[MultiTargetGeneratedSentence] = []
    counts = existing_sentence_counts or {}

    for attempt in range(1, MULTI_TARGET_MAX_RETRIES + 1):
        try:
            results = generate_sentences_multi_target(
                target_words=llm_targets,
                known_words=sample,
                count=count,
                difficulty_hint=difficulty_hint,
                avoid_words=avoid_words,
                max_words=max_words,
                model_override=model_override,
            )
        except AllProvidersFailed:
            break

        for res in results:
            validation = validate_sentence_multi_target(
                arabic_text=res.arabic,
                target_bares=target_bares,
                known_bare_forms=all_bare,
            )
            if not validation.valid:
                continue

            # Determine which target lemma_ids were found
            found_ids = [
                target_bares[bare]
                for bare, found in validation.targets_found.items()
                if found
            ]
            if not found_ids:
                continue

            # Primary target = the one with fewest existing sentences
            primary = min(found_ids, key=lambda lid: counts.get(lid, 0))

            valid_sentences.append(MultiTargetGeneratedSentence(
                arabic=res.arabic,
                english=res.english,
                transliteration=res.transliteration,
                target_lemma_ids=found_ids,
                primary_target_lemma_id=primary,
                target_bares_found=validation.targets_found,
                attempts=attempt,
            ))

        if valid_sentences:
            break

    # Batch quality review
    if valid_sentences:
        to_review = [{"arabic": s.arabic, "english": s.english} for s in valid_sentences]
        reviews = review_sentences_quality(to_review)
        valid_sentences = [
            s for s, r in zip(valid_sentences, reviews)
            if r.natural and r.translation_correct
        ]

    return valid_sentences
