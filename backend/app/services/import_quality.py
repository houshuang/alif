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
    from app.services.sentence_validator import normalize_quranic_to_msa

    if not lemmas:
        return [], []

    # Pre-normalize: convert Quranic presentation forms to MSA before the LLM
    # ever sees them (dagger alef → ا, small waw → strip, etc.). This keeps the
    # classifier focused on prefix/suffix/number issues, not typography.
    indexed = []
    pre_normalized: dict[int, str] = {}
    for i, lem in enumerate(lemmas):
        raw = lem.get("arabic", "")
        normalized = normalize_quranic_to_msa(raw)
        if normalized != raw:
            pre_normalized[i] = normalized
        indexed.append({"idx": i, **lem, "arabic": normalized})

    classifications: dict[int, str] = {}
    cleaned_forms: dict[int, str] = {}
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

Also check if the bare form needs cleaning. Include "clean" when the arabic form has issues:
- ال-prefix baked in when it shouldn't be: المطحونة → مطحونة, الشقة → شقة
- و+ال (conjunction + article) baked in: والمسلمات → مسلمة, والمؤمنين → مؤمن
- Final ه that should be ة (OCR artifact): المطحونه → مطحونة, الجراحه → جراحة
- Reduce plurals to singular dictionary form when plural was imported as lemma: \
والصائمات → صائمة, والحافظين → حافظ (the lemma should be the singular dictionary entry)
- Do NOT clean words where ال is integral: الله, الذي/التي, الآن, اليوم
- Do NOT clean Form VIII/X verbs: التقى, التحق, استقبل
- Do NOT clean proper nouns where ال is part of the name: الرازي, الحاوي

Note: Quranic presentation diacritics (ٱ dagger-alef ـٰ small-waw ۥ sukun ۡ maddah ٓ \
Quranic annotation marks) are normalized automatically before classification — you will \
see MSA-form text even if the source was Mushaf-style. Focus on prefix/suffix/number issues.

Words:
{word_list}

Return JSON: {{"classifications": [{{"idx": 0, "cat": "standard"}}, {{"idx": 1, "cat": "standard", "clean": "مطحونة"}}, ...]}}
Only include "clean" key when the bare form actually needs fixing. Include every word index."""

        try:
            result = generate_completion(prompt, json_mode=True, temperature=0.1, task_type="import_quality", model_override="claude_haiku")
            for item in result.get("classifications", []):
                idx = int(item["idx"])
                cat = item.get("cat", "standard")
                if cat == "junk":
                    junk_indices.add(idx)
                elif cat in VALID_CATEGORIES:
                    classifications[idx] = cat
                else:
                    classifications[idx] = "standard"
                # LLM-suggested bare form cleaning (ال prefix, ه→ة, etc.)
                if item.get("clean"):
                    cleaned_forms[idx] = item["clean"]
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
            # If the LLM didn't suggest a cleaner form but we pre-normalized
            # Quranic → MSA, propagate that as the cleaned bare form so the
            # lemma is stored in MSA typography.
            if i in cleaned_forms:
                lem_copy["cleaned_arabic"] = cleaned_forms[i]
            elif i in pre_normalized:
                lem_copy["cleaned_arabic"] = pre_normalized[i]
            classified.append(lem_copy)

    if rejected:
        logger.info(
            f"Import quality gate: {len(classified)} classified, {len(rejected)} rejected "
            f"({', '.join(r.get('arabic', '?') for r in rejected[:5])})"
        )
    if cleaned_forms:
        logger.info(
            f"Import quality gate cleaned {len(cleaned_forms)} bare forms: "
            + ", ".join(f"{lemmas[i].get('arabic', '?')}→{v}" for i, v in list(cleaned_forms.items())[:5])
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
