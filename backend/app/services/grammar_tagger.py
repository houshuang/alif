"""Tag sentences with grammar features using LLM."""

from typing import Any

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
    )

    features = result.get("features", [])
    primary = result.get("primary_feature", features[0] if features else None)

    valid_keys = {
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
    features = [f for f in features if f in valid_keys]
    if primary not in valid_keys:
        primary = features[0] if features else None

    return {"features": features, "primary_feature": primary}


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
    )

    valid_keys = {
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
    return [f for f in result.get("features", []) if f in valid_keys]
