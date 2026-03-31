"""Centralized lemma quality gate — run after every lemma creation path.

Ensures all new lemmas have:
1. Clean bare form (no punctuation artifacts)
2. Non-empty gloss
3. Frequency rank from CAMeL MSA data
4. No duplicate canonical lemmas (same bare form)
"""

import logging
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Lemma
from app.services.sentence_validator import normalize_alef, strip_diacritics

logger = logging.getLogger(__name__)

# Lazy-loaded frequency rank map
_rank_map: Optional[dict[str, int]] = None
_CAMEL_CACHE = Path(__file__).resolve().parent.parent / "data" / "MSA_freq_lists.tsv"

ARABIC_PUNCT = re.compile(r'[،؟؛«»\u060C\u061B\u061F.,:;!?\"\'\-\(\)\[\]{}…]')


def _normalize(text: str) -> str:
    """Full normalization for frequency matching."""
    text = strip_diacritics(text)
    text = text.replace('\u0640', '')  # tatweel
    text = normalize_alef(text)
    return text


def _load_rank_map() -> dict[str, int]:
    """Load CAMeL MSA frequency data. Cached after first call."""
    global _rank_map
    if _rank_map is not None:
        return _rank_map

    if not _CAMEL_CACHE.exists():
        logger.warning(f"CAMeL frequency file not found: {_CAMEL_CACHE}")
        _rank_map = {}
        return _rank_map

    logger.info(f"Loading CAMeL frequency data from {_CAMEL_CACHE}...")
    freq: dict[str, int] = {}
    with open(_CAMEL_CACHE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) != 2:
                continue
            word, count_str = parts
            try:
                count = int(count_str)
            except ValueError:
                continue
            normalized = _normalize(word)
            if normalized in freq:
                freq[normalized] += count
            else:
                freq[normalized] = count

    # Convert to rank map (sorted by count descending)
    sorted_forms = sorted(freq.items(), key=lambda x: -x[1])
    _rank_map = {form: rank for rank, (form, _) in enumerate(sorted_forms, 1)}
    logger.info(f"Loaded {len(_rank_map):,} frequency entries")
    return _rank_map


def clean_bare_form(bare: str) -> str:
    """Strip punctuation artifacts from bare form."""
    bare = ARABIC_PUNCT.sub('', bare)
    bare = bare.replace('«', '').replace('»', '')
    return bare.strip()


def assign_frequency_rank(lemma: Lemma) -> bool:
    """Assign frequency_rank from CAMeL data. Returns True if assigned."""
    rank_map = _load_rank_map()
    if not rank_map:
        return False

    bare = _normalize(lemma.lemma_ar_bare) if lemma.lemma_ar_bare else None
    if not bare:
        return False

    rank = rank_map.get(bare)
    if rank is None and bare.startswith('ال'):
        rank = rank_map.get(bare[2:])
    elif rank is None:
        rank = rank_map.get('ال' + bare)

    if rank is not None:
        lemma.frequency_rank = rank
        return True
    return False


def find_duplicate_canonical(db: Session, bare: str, exclude_id: int | None = None) -> Lemma | None:
    """Find an existing canonical lemma with the same normalized bare form."""
    normalized = _normalize(bare)
    # Efficient query — search by bare form directly instead of loading all canonicals
    candidates = db.query(Lemma).filter(
        Lemma.canonical_lemma_id.is_(None),
        Lemma.word_category != "junk",
        Lemma.lemma_ar_bare == bare,
    ).all()
    # Also try normalized variants
    if not candidates:
        candidates = db.query(Lemma).filter(
            Lemma.canonical_lemma_id.is_(None),
            Lemma.word_category != "junk",
            Lemma.lemma_ar_bare.in_([normalized, "ال" + normalized] if not normalized.startswith("ال") else [normalized, normalized[2:]]),
        ).all()
    for c in candidates:
        if c.lemma_id == exclude_id:
            continue
        return c
    return None


def finalize_new_lemmas(db: Session, lemma_ids: list[int]) -> dict:
    """Run quality checks on newly created lemmas.

    Call this after any lemma creation path. It:
    1. Cleans bare forms (punctuation artifacts)
    2. Warns about empty glosses
    3. Assigns frequency ranks
    4. Flags potential duplicates (does NOT auto-merge — log only)

    Returns summary dict with counts.
    """
    if not lemma_ids:
        return {"cleaned": 0, "ranked": 0, "empty_gloss": 0, "potential_dupes": 0}

    # Phase 1: Read — collect data needed (no dirty state)
    lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
    rank_map = _load_rank_map()  # Slow I/O (~5s first call) — no DB dirty state

    cleaned = 0
    ranked = 0
    empty_gloss = 0
    potential_dupes = []

    # Phase 1b: Check for duplicates BEFORE dirtying the session
    for lemma in lemmas:
        if lemma.canonical_lemma_id is None and lemma.lemma_ar_bare:
            bare = clean_bare_form(lemma.lemma_ar_bare)
            dupe = find_duplicate_canonical(db, bare, exclude_id=lemma.lemma_id)
            if dupe:
                potential_dupes.append((lemma.lemma_id, dupe.lemma_id, bare))
                logger.warning(
                    f"Potential duplicate: new lemma {lemma.lemma_id} ({bare} = {lemma.gloss_en}) "
                    f"vs existing {dupe.lemma_id} ({dupe.lemma_ar_bare} = {dupe.gloss_en})"
                )

    # Phase 2: Write — fast attribute assignments (milliseconds)
    for lemma in lemmas:
        # 1. Clean bare form
        if lemma.lemma_ar_bare:
            new_bare = clean_bare_form(lemma.lemma_ar_bare)
            if new_bare != lemma.lemma_ar_bare:
                lemma.lemma_ar_bare = new_bare
                cleaned += 1

        # 2. Check gloss
        if not lemma.gloss_en:
            empty_gloss += 1
            logger.warning(f"Lemma {lemma.lemma_id} ({lemma.lemma_ar_bare}) has no gloss")

        # 3. Assign frequency rank (uses pre-loaded rank_map, no I/O)
        if lemma.frequency_rank is None and rank_map:
            if assign_frequency_rank(lemma):
                ranked += 1

    summary = {
        "cleaned": cleaned,
        "ranked": ranked,
        "empty_gloss": empty_gloss,
        "potential_dupes": len(potential_dupes),
    }
    if any(v > 0 for v in summary.values()):
        logger.info(f"finalize_new_lemmas: {summary}")
    return summary
