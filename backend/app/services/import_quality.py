"""LLM quality gate for word imports.

Filters out junk lemmas (transliterations, abbreviations, letter names,
partial words) and classifies words by category (standard, proper_name,
onomatopoeia) before they enter the learning system.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"standard", "proper_name", "onomatopoeia"}


def classify_lemmas(
    lemmas: list[dict[str, Any]],
    batch_size: int = 50,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """LLM batch classify: categorize each word and filter junk.

    Each lemma dict must have at minimum: "arabic" (bare form) and "english" (gloss).
    Any extra keys are preserved.

    Returns (classified, rejected) where classified dicts have a "word_category" key:
      - "standard" for normal vocabulary
      - "proper_name" for personal/place names
      - "onomatopoeia" for animal sounds, sound effects
    Rejected dicts are junk (transliterations, fragments, abbreviations).
    """
    from app.services.llm import generate_completion, AllProvidersFailed

    if not lemmas:
        return [], []

    indexed = []
    for i, lem in enumerate(lemmas):
        indexed.append({"idx": i, **lem})

    classifications: dict[int, str] = {}
    junk_indices: set[int] = set()

    for batch_start in range(0, len(indexed), batch_size):
        batch = indexed[batch_start:batch_start + batch_size]
        word_list = "\n".join(
            f"  {item['idx']}: {item['arabic']} ({item.get('english', '')})"
            for item in batch
        )

        prompt = f"""Classify these Arabic lemmas for a vocabulary learning app.

For each word, assign ONE category:
- "standard" — normal MSA vocabulary word (verbs, nouns, adjectives, function words, etc.)
- "proper_name" — personal names (أسامة، محمد، فاطمة), place names (القاهرة، دمشق), or name-derived words that are primarily used as names
- "onomatopoeia" — sound effects, animal sounds (ماو، طق، نقيق), non-lexical exclamations
- "junk" — transliterations of English/foreign words, abbreviations, single letters, meaningless fragments

Guidelines:
- Possessive forms (عندي, بيتي) → "standard"
- Conjugated verbs (تحب, يسكن) → "standard"
- Countries and cities (مصر, باريس) → "standard" (these are useful vocabulary, not personal names)
- Personal/character names → "proper_name"
- Animal noises, sounds → "onomatopoeia"
- Be conservative: when in doubt, classify as "standard"

Words:
{word_list}

Return JSON: {{"classifications": [{{"idx": 0, "cat": "standard"}}, ...]}}
Include every word index in the response."""

        try:
            result = generate_completion(prompt, json_mode=True, temperature=0.1)
            for item in result.get("classifications", []):
                idx = int(item["idx"])
                cat = item.get("cat", "standard")
                if cat == "junk":
                    junk_indices.add(idx)
                elif cat in VALID_CATEGORIES:
                    classifications[idx] = cat
                else:
                    classifications[idx] = "standard"
        except AllProvidersFailed:
            logger.warning("LLM unavailable for import quality check, passing all words through")
            return lemmas, []
        except Exception:
            logger.exception("Error in import quality classify batch")
            continue

    classified = []
    rejected = []
    for i, lem in enumerate(lemmas):
        if i in junk_indices:
            rejected.append(lem)
        else:
            lem_copy = dict(lem)
            lem_copy["word_category"] = classifications.get(i, "standard")
            classified.append(lem_copy)

    if rejected:
        logger.info(
            f"Import quality gate: {len(classified)} classified, {len(rejected)} rejected "
            f"({', '.join(r.get('arabic', '?') for r in rejected[:5])})"
        )

    cats = {}
    for c in classified:
        cat = c.get("word_category", "standard")
        cats[cat] = cats.get(cat, 0) + 1
    if any(k != "standard" for k in cats):
        logger.info(f"Import classifications: {cats}")

    return classified, rejected


def filter_useful_lemmas(
    lemmas: list[dict[str, Any]],
    batch_size: int = 50,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """LLM batch filter: keep only useful standalone Arabic words.

    Backward-compatible wrapper around classify_lemmas(). The word_category
    key is preserved on the returned dicts for callers that want it.

    Returns (useful, rejected) tuples.
    """
    return classify_lemmas(lemmas, batch_size=batch_size)
