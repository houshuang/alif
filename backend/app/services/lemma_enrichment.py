"""Batch enrichment for newly created Lemma records.

Populates forms_json, etymology_json, transliteration_ala_lc, grammar_features_json,
example_ar/example_en, and root_id.
Designed to run as a background task after import (opens its own DB session).
"""

import logging
import re
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
- "past_3p": past tense 3rd person masculine plural (e.g. كَتَبُوا)
- "past_1s": past tense 1st person singular (e.g. كَتَبْتُ). CRITICAL for weak verbs where the stem changes: قُلْتُ (not قَالْتُ), مَشَيْتُ (not مَشَىتُ), نِمْتُ (not نَامْتُ).
- "past_3fp": past tense 3rd person feminine plural (e.g. كَتَبْنَ)
- "present_3fp": present 3rd person feminine plural (e.g. يَكْتُبْنَ). Important for weak verbs: يَقُلْنَ, يَمْشِينَ.
- "present_3mp": present 3rd person masculine plural (e.g. يَكْتُبُونَ). Important for weak/defective verbs where stem changes: يَمْشُونَ (not يَمْشِيُونَ), يَدْعُونَ.
- "masdar": the verbal noun (e.g. كِتَابَة)
- "active_participle": the active participle (e.g. كَاتِب)
- "passive_participle": the passive participle (e.g. مَكْتُوب)
- "imperative": the imperative 2nd person masculine singular (e.g. اُكْتُبْ)
- "verb_form": the verb form number as Roman numeral (I, II, III, IV, V, VI, VII, VIII, IX, X)

For nouns, return:
- "plural": the most common plural form (broken plural) with full diacritics
- "gender": "m" or "f"
- "sound_f_plural": sound feminine plural (ـات form) if applicable (e.g. كِتَابَات, مُعَلِّمَات). Omit if no sound feminine plural exists.
- "sound_m_plural": sound masculine plural (ـون form) if applicable (e.g. مُعَلِّمُون, مُهَنْدِسُون). Omit if no sound masculine plural exists.
- "dual": dual form if applicable (e.g. كِتَابَان)

For adjectives, return:
- "feminine": the feminine form (e.g. كَبِيرَة)
- "plural": the most common plural form
- "sound_f_plural": sound feminine plural if applicable (e.g. كَبِيرَات)
- "sound_m_plural": sound masculine plural if applicable (e.g. كَبِيرُون)
- "elative": the comparative/superlative form if it exists (e.g. أَكْبَر)

Always include full diacritics on Arabic text. Only include fields you are confident about. \
Return empty object {} if the word doesn't have meaningful forms (particles, pronouns, etc.)."""

FORMS_VALID_KEYS = {
    "gender", "plural", "present", "past_3fs", "past_3p",
    "past_1s", "past_3fp", "present_3fp", "present_3mp",
    "masdar", "active_participle", "passive_participle",
    "imperative", "verb_form", "feminine", "elative",
    "sound_f_plural", "sound_m_plural", "dual",
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


ROOTS_SYSTEM_PROMPT = """You are an Arabic morphology expert. For each Arabic word, extract its consonantal root (جذر).

Rules:
- Return the root in dotted Arabic notation (e.g. ك.ت.ب for كتاب)
- Most roots are 3 consonants (trilateral), some are 4 (quadrilateral)
- For particles, pronouns, and words without a clear root, return null

Return a JSON array: [{"lemma_id": 1, "root": "ك.ت.ب"}]
Use null for root if the word has no meaningful root."""

EXAMPLES_SYSTEM_PROMPT = """You are an Arabic language teaching assistant. Generate very short example sentences (3-5 words) for Arabic vocabulary words. Each sentence should:
- Use fully diacritized Arabic (all tashkeel)
- Be simple enough for a beginner
- Clearly demonstrate the meaning of the target word
- Use common, everyday vocabulary

Return JSON array with objects having keys: lemma_id, example_ar, example_en"""


def _normalize_root(root_str: str | None) -> str | None:
    """Normalize root format to dotted notation."""
    if not root_str:
        return None
    cleaned = re.sub(r'[^\u0600-\u06FF.]', '', root_str)
    if not cleaned:
        return None
    if '.' in cleaned:
        parts = cleaned.split('.')
        if len(parts) < 3 or len(parts) > 4:
            return None
        return cleaned
    chars = [c for c in cleaned if c.strip()]
    if len(chars) < 3 or len(chars) > 4:
        return None
    return '.'.join(chars)


def _generate_roots_batch(lemmas: list[Lemma]) -> dict[int, str]:
    """Extract consonantal roots for a batch of lemmas. Returns {lemma_id: dotted_root_str}."""
    from app.services.llm import generate_completion, AllProvidersFailed
    from app.services.morphology import is_valid_root

    lines = []
    for lemma in lemmas:
        pos_hint = f", pos={lemma.pos}" if lemma.pos else ""
        gloss = f', meaning="{lemma.gloss_en}"' if lemma.gloss_en else ""
        lines.append(f"- lemma_id={lemma.lemma_id}, word={lemma.lemma_ar}{pos_hint}{gloss}")

    try:
        result = generate_completion(
            prompt=f"Extract the Arabic consonantal root for each word:\n\n"
                   + "\n".join(lines)
                   + '\n\nReturn JSON array: [{"lemma_id": 1, "root": "ك.ت.ب"}] (use null for root if no root)',
            system_prompt=ROOTS_SYSTEM_PROMPT,
            json_mode=True,
            temperature=0.1,
            model_override="claude_haiku",
            task_type="enrichment_roots",
        )
    except AllProvidersFailed:
        return {}

    items = result if isinstance(result, list) else result.get("roots", result.get("words", []))
    if not isinstance(items, list):
        return {}

    out = {}
    for item in items:
        lid = item.get("lemma_id")
        raw = item.get("root")
        if lid and raw:
            norm = _normalize_root(raw)
            if norm and is_valid_root(norm):
                out[lid] = norm
    return out


def _generate_examples_batch(lemmas: list[Lemma]) -> dict[int, tuple[str, str]]:
    """Generate example sentences for a batch. Returns {lemma_id: (ar, en)}."""
    from app.services.llm import generate_completion, AllProvidersFailed

    lines = []
    for l in lemmas:
        lines.append(f'- lemma_id={l.lemma_id}, word={l.lemma_ar}, meaning="{l.gloss_en}", pos={l.pos or "unknown"}')

    try:
        result = generate_completion(
            prompt=f"Generate a short (3-5 word) example sentence for each of these Arabic words:\n\n"
                   + "\n".join(lines)
                   + '\n\nReturn JSON array: [{"lemma_id": 1, "example_ar": "...", "example_en": "..."}]',
            system_prompt=EXAMPLES_SYSTEM_PROMPT,
            json_mode=True,
            temperature=0.5,
            model_override="claude_haiku",
            task_type="enrichment_examples",
        )
    except AllProvidersFailed:
        return {}

    items = result if isinstance(result, list) else result.get("examples", result.get("sentences", []))
    if not isinstance(items, list):
        return {}

    out = {}
    for item in items:
        lid = item.get("lemma_id")
        ar = (item.get("example_ar") or "").strip()
        en = (item.get("example_en") or "").strip()
        if lid and ar and en:
            out[lid] = (ar, en)
    return out


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
            model_override="claude_haiku",
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
            model_override="claude_haiku",
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
    """Enrich a batch of lemmas: forms, etymology, roots, grammar tags, examples.

    Opens its own DB session (safe for background tasks).
    Skips fields already populated. Each enrichment step is independent.

    Returns summary dict with counts.
    """
    if not lemma_ids:
        return {"enriched": 0}

    db = SessionLocal()
    summary = {"forms": 0, "etymology": 0, "transliteration": 0, "memory_hooks": 0,
                "roots": 0, "grammar": 0, "examples": 0, "total": len(lemma_ids)}

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

        # ── Step 2: Forms (individual LLM calls — collect results first) ──
        forms_results: dict[int, dict] = {}
        for lemma in lemmas:
            if lemma.forms_json:
                continue
            try:
                forms = _generate_forms(lemma)
                if forms:
                    forms_results[lemma.lemma_id] = forms
                time.sleep(0.3)
            except Exception:
                logger.warning(f"Forms generation failed for lemma {lemma.lemma_id}")

        # ── Step 3: Etymology (batched LLM calls — collect results first) ──
        etym_results: dict[int, dict] = {}
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
                    etym_results.update(etym_map)
                    time.sleep(1)
                except Exception:
                    logger.warning(f"Etymology batch failed for lemmas {[l.lemma_id for l in batch]}")

        # ── Step 4: Batch-apply all LLM results to DB ──
        for lemma in lemmas:
            forms = forms_results.get(lemma.lemma_id)
            if forms:
                lemma.forms_json = forms
                summary["forms"] += 1
            etym = etym_results.get(lemma.lemma_id)
            if etym:
                lemma.etymology_json = etym
                summary["etymology"] += 1
        db.commit()

        # Memory hooks are no longer generated upfront — they're generated on
        # first failure (rating <= 2) to avoid wasting processing on already-known words.

        # ── Step 5: Root association (batched LLM call) ──
        need_roots = [l for l in lemmas if not l.root_id and l.pos in ('noun', 'verb', 'adjective', 'adj', None)]
        if need_roots:
            existing_roots = {r.root: r for r in db.query(Root).all()}
            batch_size = 20
            root_results: dict[int, str] = {}
            for i in range(0, len(need_roots), batch_size):
                batch = need_roots[i:i + batch_size]
                try:
                    root_map = _generate_roots_batch(batch)
                    root_results.update(root_map)
                    time.sleep(1)
                except Exception:
                    logger.warning(f"Root extraction failed for lemmas {[l.lemma_id for l in batch]}")

            for lemma in need_roots:
                root_str = root_results.get(lemma.lemma_id)
                if not root_str:
                    continue
                if root_str in existing_roots:
                    root_obj = existing_roots[root_str]
                else:
                    root_obj = Root(root=root_str)
                    db.add(root_obj)
                    db.flush()
                    existing_roots[root_str] = root_obj
                lemma.root_id = root_obj.root_id
                summary["roots"] += 1
            db.commit()

            # Backfill meanings for newly created roots
            if summary["roots"] > 0:
                try:
                    from app.services.morphology import backfill_root_meanings
                    backfill_root_meanings(db)
                    db.commit()
                except Exception:
                    logger.warning("Root meaning backfill failed")

        # ── Step 6: Grammar features (individual LLM calls) ──
        need_grammar = [l for l in lemmas if not l.grammar_features_json
                        and l.pos in ('noun', 'verb', 'adjective', 'adj')]
        if need_grammar:
            from app.services.grammar_tagger import tag_lemma_grammar
            for lemma in need_grammar:
                try:
                    features = tag_lemma_grammar(lemma.lemma_ar, lemma.pos, lemma.gloss_en)
                    if features:
                        lemma.grammar_features_json = features
                        summary["grammar"] += 1
                    time.sleep(0.3)
                except Exception:
                    logger.warning(f"Grammar tagging failed for lemma {lemma.lemma_id}")
            db.commit()

        # ── Step 7: Example sentences (batched LLM calls) ──
        need_examples = [l for l in lemmas if not l.example_ar
                         and l.pos in ('noun', 'verb', 'adjective', 'adj')]
        if need_examples:
            batch_size = 10
            for i in range(0, len(need_examples), batch_size):
                batch = need_examples[i:i + batch_size]
                try:
                    ex_map = _generate_examples_batch(batch)
                    for lemma in batch:
                        pair = ex_map.get(lemma.lemma_id)
                        if pair:
                            lemma.example_ar, lemma.example_en = pair
                            summary["examples"] += 1
                    time.sleep(1)
                except Exception:
                    logger.warning(f"Example generation failed for lemmas {[l.lemma_id for l in batch]}")
            db.commit()

        logger.info(
            f"Enrichment complete: {summary['forms']} forms, {summary['etymology']} etymology, "
            f"{summary['transliteration']} transliteration, {summary['roots']} roots, "
            f"{summary['grammar']} grammar, {summary['examples']} examples "
            f"(of {summary['total']} lemmas)"
        )

    except Exception:
        logger.exception("Enrichment batch failed")
        db.rollback()
    finally:
        db.close()

    return summary
