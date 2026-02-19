"""Batch enrichment for newly created Lemma records.

Populates forms_json, etymology_json, memory_hooks_json, and transliteration_ala_lc.
Designed to run as a background task after import (opens its own DB session).
"""

import logging
import time

from app.database import SessionLocal
from app.models import Lemma, Root

logger = logging.getLogger(__name__)

# ── Reused prompts from backfill scripts ──────────────────────────────

FORMS_SYSTEM_PROMPT = """\
You are an Arabic morphology expert. Given an Arabic word with its POS and meaning, \
return its key morphological forms as JSON.

For verbs, return:
- "present": the present/imperfect 3rd person masculine singular (e.g. يَكْتُبُ)
- "past_3fs": past tense 3rd person feminine singular (e.g. كَتَبَتْ)
- "past_3p": past tense 3rd person plural (e.g. كَتَبُوا)
- "masdar": the verbal noun (e.g. كِتَابَة)
- "active_participle": the active participle (e.g. كَاتِب)
- "passive_participle": the passive participle (e.g. مَكْتُوب)
- "imperative": the imperative 2nd person masculine singular (e.g. اُكْتُبْ)
- "verb_form": the verb form number as Roman numeral (I, II, III, IV, V, VI, VII, VIII, IX, X)

For nouns, return:
- "plural": the most common plural form with full diacritics
- "gender": "m" or "f"

For adjectives, return:
- "feminine": the feminine form (e.g. كَبِيرَة)
- "plural": the most common plural form
- "elative": the comparative/superlative form if it exists (e.g. أَكْبَر)

Always include full diacritics on Arabic text. Only include fields you are confident about. \
Return empty object {} if the word doesn't have meaningful forms (particles, pronouns, etc.)."""

FORMS_VALID_KEYS = {
    "gender", "plural", "present", "past_3fs", "past_3p",
    "masdar", "active_participle", "passive_participle",
    "imperative", "verb_form", "feminine", "elative",
}

ETYMOLOGY_SYSTEM_PROMPT = """You are an Arabic etymology and morphology expert. For each word, generate structured etymology data that helps a language learner understand word origins.

There are TWO types of words:

1. NATIVE ARABIC WORDS (have a consonantal root):
- root_meaning: the core semantic field of the consonantal root (2-5 words)
- pattern: the morphological pattern (wazan) in Arabic transliteration (e.g. "maf'al", "fa'ala", "taf'īl", "maf'ūl", "fi'āla", "fu'ūl"). Use standard pattern notation with f-'-l representing the root consonants.
- pattern_meaning: what this pattern generally produces (e.g. "place of doing X", "one who does X", "the act of doing X")
- derivation: a short formula showing how root + pattern = meaning (e.g. "maktab = place of writing = office/desk")
- semantic_field: 2-4 related concepts (e.g. "literacy, education, correspondence")
- related_loanwords: English or other European words borrowed from this Arabic root, if any. Return empty array [] if none.
- cultural_note: brief cultural context if relevant, otherwise null

2. LOANWORDS and FOREIGN-ORIGIN WORDS (pizza, chocolate, cinema, tea, computer, etc.):
- root_meaning: null
- pattern: null
- pattern_meaning: null
- derivation: "From [source language] '[original word]' ([meaning])" — trace the borrowing path if it went through intermediate languages
- semantic_field: 2-4 related concepts
- related_loanwords: cognates in other languages borrowed from the same source. Return [] if none.
- cultural_note: when/how the word entered Arabic, or interesting cultural context. null if nothing notable.

ONLY return null for the whole entry for closed-class function words.

Return JSON array: [{"lemma_id": 1, "etymology": {...}}]"""


def _generate_forms(lemma: Lemma) -> dict | None:
    """Generate forms_json for a single lemma via LLM."""
    from app.services.llm import generate_completion, AllProvidersFailed

    parts = [f"Arabic: {lemma.lemma_ar}"]
    if lemma.pos:
        parts.append(f"POS: {lemma.pos}")
    if lemma.gloss_en:
        parts.append(f"English: {lemma.gloss_en}")

    try:
        result = generate_completion(
            prompt="Return the morphological forms for this Arabic word:\n\n" + "\n".join(parts),
            system_prompt=FORMS_SYSTEM_PROMPT,
            json_mode=True,
            temperature=0.1,
            task_type="enrichment_forms",
        )
    except AllProvidersFailed:
        return None

    cleaned = {}
    for k, v in result.items():
        if k in FORMS_VALID_KEYS and isinstance(v, str) and v.strip():
            cleaned[k] = v.strip()
    return cleaned if cleaned else None


def _generate_etymology_batch(lemmas: list[Lemma], roots_by_id: dict) -> dict[int, dict]:
    """Generate etymology for a batch of lemmas. Returns {lemma_id: etymology_dict}."""
    from app.services.llm import generate_completion, AllProvidersFailed

    lines = []
    for lemma in lemmas:
        root = roots_by_id.get(lemma.root_id)
        pos_hint = f", pos={lemma.pos}" if lemma.pos else ""
        gloss = f', meaning="{lemma.gloss_en}"' if lemma.gloss_en else ""
        root_info = f", root={root.root}" if root else ""
        root_meaning = f', root_meaning="{root.core_meaning_en}"' if root and root.core_meaning_en else ""
        lines.append(
            f"- lemma_id={lemma.lemma_id}, word={lemma.lemma_ar_bare}{pos_hint}{gloss}{root_info}{root_meaning}"
        )

    prompt = f"""Generate etymology data for each Arabic word:

{chr(10).join(lines)}

Return JSON array: [{{"lemma_id": 1, "etymology": {{"root_meaning": "...", "pattern": "...", "pattern_meaning": "...", "derivation": "...", "semantic_field": "...", "related_loanwords": [...], "cultural_note": null}}}}]

Use null for etymology if the word has no meaningful root derivation (particles, pronouns, etc.)."""

    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt=ETYMOLOGY_SYSTEM_PROMPT,
            json_mode=True,
            temperature=0.3,
            task_type="enrichment_etymology",
        )
    except AllProvidersFailed:
        return {}

    items = result if isinstance(result, list) else result.get("words", result.get("etymologies", []))
    if not isinstance(items, list):
        return {}

    out = {}
    for item in items:
        lid = item.get("lemma_id")
        etym = item.get("etymology")
        if lid and isinstance(etym, dict) and etym.get("derivation"):
            out[lid] = etym
    return out


def enrich_lemmas_batch(lemma_ids: list[int]) -> dict:
    """Enrich a batch of lemmas with forms, etymology, and memory hooks.

    Opens its own DB session (safe for background tasks).
    Skips fields already populated. Each enrichment step is independent.

    Returns summary dict with counts.
    """
    if not lemma_ids:
        return {"enriched": 0}

    db = SessionLocal()
    summary = {"forms": 0, "etymology": 0, "transliteration": 0, "memory_hooks": 0, "total": len(lemma_ids)}

    try:
        lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
        if not lemmas:
            return summary

        # ── Step 1: Transliteration (deterministic, instant) ──
        from app.services.transliteration import transliterate_lemma

        for lemma in lemmas:
            if lemma.transliteration_ala_lc:
                continue
            if lemma.lemma_ar and any("\u0610" <= c <= "\u065f" or c == "\u0670" for c in lemma.lemma_ar):
                try:
                    lemma.transliteration_ala_lc = transliterate_lemma(lemma.lemma_ar)
                    summary["transliteration"] += 1
                except Exception:
                    pass
        db.commit()

        # ── Step 2: Forms (individual LLM calls) ──
        for lemma in lemmas:
            if lemma.forms_json:
                continue
            try:
                forms = _generate_forms(lemma)
                if forms:
                    lemma.forms_json = forms
                    summary["forms"] += 1
                    db.commit()
                time.sleep(0.3)
            except Exception:
                logger.warning(f"Forms generation failed for lemma {lemma.lemma_id}")
                db.rollback()

        # ── Step 3: Etymology (batched LLM calls) ──
        need_etymology = [l for l in lemmas if not l.etymology_json]
        if need_etymology:
            root_ids = {l.root_id for l in need_etymology if l.root_id}
            roots_by_id = {}
            if root_ids:
                for root in db.query(Root).filter(Root.root_id.in_(root_ids)).all():
                    roots_by_id[root.root_id] = root

            batch_size = 10
            for i in range(0, len(need_etymology), batch_size):
                batch = need_etymology[i:i + batch_size]
                try:
                    etym_map = _generate_etymology_batch(batch, roots_by_id)
                    for lemma in batch:
                        etym = etym_map.get(lemma.lemma_id)
                        if etym:
                            lemma.etymology_json = etym
                            summary["etymology"] += 1
                    db.commit()
                    time.sleep(1)
                except Exception:
                    logger.warning(f"Etymology batch failed for lemmas {[l.lemma_id for l in batch]}")
                    db.rollback()

        # ── Step 4: Memory hooks (reuse existing service) ──
        from app.services.memory_hooks import generate_memory_hooks

        for lemma in lemmas:
            if lemma.memory_hooks_json:
                continue
            try:
                generate_memory_hooks(lemma.lemma_id)
                summary["memory_hooks"] += 1
                time.sleep(0.3)
            except Exception:
                logger.warning(f"Memory hooks failed for lemma {lemma.lemma_id}")

        logger.info(
            f"Enrichment complete: {summary['forms']} forms, {summary['etymology']} etymology, "
            f"{summary['transliteration']} transliteration, {summary['memory_hooks']} memory hooks "
            f"(of {summary['total']} lemmas)"
        )

    except Exception:
        logger.exception("Enrichment batch failed")
        db.rollback()
    finally:
        db.close()

    return summary
