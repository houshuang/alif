"""Greek-flavored sentence validation primitives.

Mirrors the shape of Alif's ``backend/app/services/sentence_validator.py`` but
much smaller — Greek doesn't need clitic stripping, tashkeel handling, alef
normalization, or ``ال``-peeling. The tokenizer in ``languages/el.py`` already
splits elisions (τ'άλλο → ``τ'``, ``άλλο``), and the bare form is just
``_strip_accents_monotonic`` applied to the simplemma output.

Used by ``material_generator.py``:

- ``tokenize_display(text, language_code)`` — provider-driven, returns positions.
- ``build_lemma_lookup(db, language_code)`` — ``{lemma_bare: lemma_id}``.
- ``map_tokens_to_lemmas(...)`` — simplemma-lemmatize each token, then look up.
- ``validate_sentence(...)`` — count known/unknown/unmapped against the lookup.

The eventual ``alif_core`` extraction is expected to define a Mapping/Validation
protocol; this module's dataclasses match Alif's so callers transfer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Lemma
from app.services.languages import get_provider

log = logging.getLogger(__name__)


@dataclass
class Mapping:
    position: int
    surface_form: str
    lemma_id: Optional[int]
    is_target: bool = False
    alternative_lemma_ids: list[int] = field(default_factory=list)


@dataclass
class ValidationResult:
    valid: bool
    total_content_tokens: int = 0
    known_count: int = 0
    function_words: list[str] = field(default_factory=list)
    unknown_words: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    target_present: bool = False
    issues: list[str] = field(default_factory=list)


_PUNCT_RE = re.compile(r"^[\W_]+$", re.UNICODE)


def normalize_bare(form: str, language_code: str) -> str:
    """Provider-driven normalization. Falls back to lowercase if provider absent.

    Caller should treat this as the lookup key — matches ``Lemma.lemma_bare``
    storage convention enforced at insert time.
    """
    if not form:
        return ""
    try:
        provider = get_provider(language_code)
    except Exception:
        return form.lower()
    try:
        return provider.normalize_bare(form)
    except Exception:
        return form.lower()


def tokenize_display(text: str, language_code: str) -> list[tuple[int, str]]:
    """Return ``[(position, surface_form), ...]`` skipping pure-punctuation tokens.

    Position is the provider's running index (matches ``PageWord.position``
    semantics — including punctuation in the count). Callers typically only
    care about content tokens.
    """
    provider = get_provider(language_code)
    tokens = provider.tokenize(text)
    out: list[tuple[int, str]] = []
    for tok in tokens:
        if tok.is_punctuation:
            continue
        if _PUNCT_RE.match(tok.surface):
            continue
        out.append((tok.position, tok.surface))
    return out


def build_lemma_lookup(db: Session, language_code: str) -> dict[str, int]:
    """All ``lemma_bare → lemma_id`` for one language.

    Variant chain redirects are NOT collapsed here — callers that care about
    canonical IDs run them through ``canonical_resolution.resolve_canonical_*``
    at write time. The picker / sentence_review_service / material_generator
    already do this.
    """
    rows = (
        db.query(Lemma.lemma_id, Lemma.lemma_bare)
        .filter(Lemma.language_code == language_code)
        .all()
    )
    lookup: dict[str, int] = {}
    for lemma_id, lemma_bare in rows:
        if not lemma_bare:
            continue
        lookup.setdefault(lemma_bare, lemma_id)
    return lookup


def _lemmatize_to_bare(surface: str, language_code: str) -> str:
    try:
        provider = get_provider(language_code)
        cand = provider.lemmatize(surface)
        return cand.lemma_bare or normalize_bare(surface, language_code)
    except Exception:
        return normalize_bare(surface, language_code)


def map_tokens_to_lemmas(
    tokens: list[tuple[int, str]],
    lemma_lookup: dict[str, int],
    language_code: str,
    target_lemma_id: int,
    target_bare: str,
) -> list[Mapping]:
    """For each content token, lemmatize via simplemma and look up.

    Returns one ``Mapping`` per token. ``lemma_id is None`` means we couldn't
    find a matching DB lemma — caller will discard the sentence (the picker
    requires every SentenceWord to point at a real lemma).
    """
    out: list[Mapping] = []
    for position, surface in tokens:
        bare_via_lemmatizer = _lemmatize_to_bare(surface, language_code)
        bare_via_surface = normalize_bare(surface, language_code)

        lemma_id = lemma_lookup.get(bare_via_lemmatizer)
        if lemma_id is None and bare_via_surface != bare_via_lemmatizer:
            lemma_id = lemma_lookup.get(bare_via_surface)

        is_target = lemma_id == target_lemma_id or bare_via_lemmatizer == target_bare
        if is_target:
            lemma_id = target_lemma_id

        out.append(Mapping(
            position=position,
            surface_form=surface,
            lemma_id=lemma_id,
            is_target=is_target,
        ))
    return out


def validate_sentence(
    text: str,
    target_bare: str,
    known_bare_forms: set[str],
    function_word_bares: set[str],
    language_code: str,
) -> ValidationResult:
    """Deterministic pre-LLM gate: every content token must lemmatize to a
    known bare form (or be the target). Returns ``valid=True`` only if so.

    ``known_bare_forms`` should be derived from the active vocabulary lookup
    keys passed in by the caller. ``function_word_bares`` come from
    ``lemma_quality.FUNCTION_WORD_SETS[language_code]``.
    """
    tokens = tokenize_display(text, language_code)
    result = ValidationResult(valid=False)

    if not tokens:
        result.issues.append("empty_sentence")
        return result

    for position, surface in tokens:
        bare = _lemmatize_to_bare(surface, language_code)
        if bare in function_word_bares or normalize_bare(surface, language_code) in function_word_bares:
            result.function_words.append(surface)
            continue
        result.total_content_tokens += 1
        if bare == target_bare or normalize_bare(surface, language_code) == target_bare:
            result.target_present = True
            result.known_count += 1
            continue
        if bare in known_bare_forms or normalize_bare(surface, language_code) in known_bare_forms:
            result.known_count += 1
            continue
        result.unknown_words.append(surface)

    if not result.target_present:
        result.issues.append("target_missing")
        return result
    if result.unknown_words:
        result.issues.append(f"unknown_words: {result.unknown_words[:5]}")
        return result

    result.valid = True
    return result
