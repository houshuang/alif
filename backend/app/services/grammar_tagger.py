"""Tag sentences with grammar features using LLM."""

from typing import Any
from collections.abc import Sequence

from app.services.llm import generate_completion

GRAMMAR_TAG_SYSTEM_PROMPT = """\
You are an Arabic grammar analyzer. Given an Arabic sentence, identify which \
grammatical features are present.

Return JSON with:
- "features": list of feature keys present in the sentence
- "primary_feature": the single most prominent grammatical feature

Valid feature keys:
singular, dual, plural_sound, plural_broken,
masculine, feminine,
past, present, imperative,
form_1, form_2, form_3, form_4, form_5, form_6, form_7, form_8, form_9, form_10,
definite_article, proclitic_prepositions, attached_pronouns,
active_participle, passive_participle, masdar, diminutive, nisba,
idafa, comparative, superlative, passive, negation,
standalone_prepositions, subject_pronouns, tanwin_patterns, exception, emphatic_negation, oath_formula, vocative,
nominal_sentence, verbal_sentence, kaana_sisters, inna_sisters, relative_clauses, conditional, hal_clause,
weak_hollow, weak_defective, weak_assimilated

Only include features that are clearly present. Be conservative."""

VALID_GRAMMAR_FEATURE_KEYS = {
    "singular", "dual", "plural_sound", "plural_broken",
    "masculine", "feminine",
    "past", "present", "imperative",
    "form_1", "form_2", "form_3", "form_4", "form_5",
    "form_6", "form_7", "form_8", "form_9", "form_10",
    "definite_article", "proclitic_prepositions", "attached_pronouns",
    "active_participle", "passive_participle", "masdar", "diminutive", "nisba",
    "idafa", "comparative", "superlative", "passive", "negation",
    "standalone_prepositions", "subject_pronouns", "tanwin_patterns",
    "exception", "emphatic_negation", "oath_formula", "vocative",
    "nominal_sentence", "verbal_sentence", "kaana_sisters", "inna_sisters",
    "relative_clauses", "conditional", "hal_clause",
    "weak_hollow", "weak_defective", "weak_assimilated",
}

_LEMMA_GRAMMAR_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "lemmas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_id": {"type": "integer"},
                    "features": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["lemma_id", "features"],
            },
        },
    },
    "required": ["lemmas"],
}


def _clean_features(features: Any) -> list[str]:
    if not isinstance(features, list):
        return []
    return [
        feature for feature in features
        if isinstance(feature, str) and feature in VALID_GRAMMAR_FEATURE_KEYS
    ]


def _item_get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def tag_sentence_grammar(arabic_text: str) -> dict[str, Any]:
    """Use LLM to identify grammar features in a sentence.

    Returns dict with "features" (list of keys) and "primary_feature" (str).
    """
    prompt = f"Analyze the grammatical features in this Arabic sentence:\n\n{arabic_text}"

    result = generate_completion(
        prompt=prompt,
        system_prompt=GRAMMAR_TAG_SYSTEM_PROMPT,
        json_mode=True,
        temperature=0.2,
        task_type="grammar_tag",
        model_override="claude_haiku",
    )

    features = result.get("features", [])
    primary = result.get("primary_feature", features[0] if features else None)

    features = _clean_features(features)
    if primary not in VALID_GRAMMAR_FEATURE_KEYS:
        primary = features[0] if features else None

    return {"features": features, "primary_feature": primary}


def tag_lemmas_grammar_batch(lemmas: Sequence[Any]) -> dict[int, list[str]]:
    """Identify inherent grammar features for multiple lemmas in one LLM call."""
    if not lemmas:
        return {}

    lines = []
    requested_ids: set[int] = set()
    for lemma in lemmas:
        lemma_id = _item_get(lemma, "lemma_id")
        lemma_ar = _item_get(lemma, "lemma_ar")
        if not isinstance(lemma_id, int) or not lemma_ar:
            continue
        requested_ids.add(lemma_id)
        parts = [f"lemma_id={lemma_id}", f"Arabic: {lemma_ar}"]
        pos = _item_get(lemma, "pos")
        gloss_en = _item_get(lemma, "gloss_en")
        if pos:
            parts.append(f"POS: {pos}")
        if gloss_en:
            parts.append(f"English: {gloss_en}")
        lines.append("- " + ", ".join(parts))

    if not lines:
        return {}

    prompt = (
        "For each Arabic lemma below, identify the grammatical features the "
        "word inherently carries.\n\n"
        + "\n".join(lines)
        + '\n\nReturn JSON: {"lemmas": [{"lemma_id": 1, "features": [...]}]}.'
    )

    result = generate_completion(
        prompt=prompt,
        system_prompt=GRAMMAR_TAG_SYSTEM_PROMPT,
        json_schema=_LEMMA_GRAMMAR_BATCH_SCHEMA,
        temperature=0.2,
        task_type="grammar_tag",
        model_override="claude_haiku",
    )

    if isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        items = result.get("lemmas", result.get("words", result.get("items", [])))
    else:
        items = []
    if not isinstance(items, list):
        return {}

    out: dict[int, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        lemma_id = item.get("lemma_id")
        if not isinstance(lemma_id, int) or lemma_id not in requested_ids:
            continue
        features = _clean_features(item.get("features"))
        if features:
            out[lemma_id] = features
    return out


def tag_lemma_grammar(
    lemma_ar: str, pos: str | None, gloss_en: str | None
) -> list[str]:
    """Use LLM to identify grammar features inherent to a lemma.

    Returns list of feature keys (e.g. ["feminine", "plural_broken"]).
    """
    parts = [f"Arabic: {lemma_ar}"]
    if pos:
        parts.append(f"POS: {pos}")
    if gloss_en:
        parts.append(f"English: {gloss_en}")

    prompt = (
        "What grammatical features does this Arabic word inherently carry?\n\n"
        + "\n".join(parts)
        + "\n\nReturn JSON: {\"features\": [...]}"
    )

    result = generate_completion(
        prompt=prompt,
        system_prompt=GRAMMAR_TAG_SYSTEM_PROMPT,
        json_mode=True,
        temperature=0.2,
        task_type="grammar_tag",
        model_override="claude_haiku",
    )

    return _clean_features(result.get("features", []))
