"""LLM quality gate for word imports.

Filters out junk lemmas (transliterations, abbreviations, letter names,
partial words) before they enter the learning system.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def filter_useful_lemmas(
    lemmas: list[dict[str, Any]],
    batch_size: int = 50,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """LLM batch filter: keep only useful standalone Arabic words.

    Each lemma dict must have at minimum: "arabic" (bare form) and "english" (gloss).
    Any extra keys are preserved.

    Returns (useful, rejected) tuples.
    """
    from app.services.llm import generate_completion, AllProvidersFailed

    if not lemmas:
        return [], []

    # Build id→lemma mapping for lookup
    indexed = []
    for i, lem in enumerate(lemmas):
        indexed.append({"idx": i, **lem})

    all_junk_indices: set[int] = set()

    for batch_start in range(0, len(indexed), batch_size):
        batch = indexed[batch_start:batch_start + batch_size]
        word_list = "\n".join(
            f"  {item['idx']}: {item['arabic']} ({item.get('english', '')})"
            for item in batch
        )

        prompt = f"""Given these Arabic lemmas being imported into a vocabulary learning app, identify which are NOT useful standalone words for an early MSA learner.

Flag these types:
- Transliterations of English/foreign words (e.g. سي = "c", واي = "wi", توب = "top")
- Abbreviations or single letter names
- Partial words or fragments
- Proper nouns (except countries, major cities, or important cultural terms)

Words:
{word_list}

Return JSON: {{"junk_indices": [list of index numbers that should be removed]}}
Only flag words you are confident are junk. When in doubt, keep the word."""

        try:
            result = generate_completion(prompt, json_mode=True, temperature=0.1)
            junk_indices = result.get("junk_indices", [])
            all_junk_indices.update(int(x) for x in junk_indices)
        except AllProvidersFailed:
            logger.warning("LLM unavailable for import quality check, passing all words through")
            return lemmas, []
        except Exception:
            logger.exception("Error in import quality check batch")
            continue

    useful = []
    rejected = []
    for i, lem in enumerate(lemmas):
        if i in all_junk_indices:
            rejected.append(lem)
        else:
            useful.append(lem)

    if rejected:
        logger.info(
            f"Import quality gate: {len(useful)} useful, {len(rejected)} rejected "
            f"({', '.join(r.get('arabic', '?') for r in rejected[:5])})"
        )

    return useful, rejected
