"""Generate memory hooks (mnemonics, cognates, collocations) for words.

Called as a background task when words enter acquisition, or via backfill script.
"""

import logging

from app.database import SessionLocal
from app.models import Lemma

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a creative Arabic language learning assistant. Generate memorable hooks that help a multilingual learner remember Arabic (MSA/fusha) vocabulary.

The learner speaks: English, Norwegian, Hindi, German, French, Italian, Spanish, Greek, Latin, Indonesian, and some Russian.

For each word, generate:

1. mnemonic: A creative memory aid connecting the Arabic SOUND (transliteration) to its meaning. Use sound-alikes, visual imagery, or mini-stories. Must be vivid and short (1-2 sentences). The best mnemonics create an absurd or emotional image.
   GOOD: "kitāb (book) — imagine a CAT on a TAB(le) reading a book"
   GOOD: "madrasa (school) — a MAD RASCal who won't go to school"
   BAD: "kitāb means book" (just a definition)

2. cognates: Words in the learner's OTHER languages that come from this Arabic word or root, or share the same origin. Search ALL the learner's languages — Arabic has lent extensively to Hindi/Urdu, Indonesian/Malay, Spanish (800 years of Moorish rule), and to a lesser extent French, Italian, German, and English. Each entry: {"lang": "Hindi", "word": "किताब (kitāb)", "note": "directly borrowed"}. Return empty array [] if no cognates exist in any language.

3. collocations: 2-3 common Arabic phrases or expressions using this word. Full diacritics on Arabic. Natural English translations. Pick phrases a learner would actually encounter.

4. usage_context: 1-2 sentences about when/where you'd encounter this word in real life. Be specific ("in restaurant menus", "in news headlines about politics") not generic ("in daily life").

5. fun_fact: One genuinely surprising fact — historical origin, cultural significance, or linguistic quirk. Return null if nothing interesting.

For particles, pronouns, and basic function words: return null for the entire entry.
For proper nouns: return null for the entire entry.

Return JSON: {"mnemonic": "...", "cognates": [...], "collocations": [...], "usage_context": "...", "fun_fact": "..."}"""


def _normalize_collocations(collocations):
    """Normalize collocations to [{ar, en}] format. Accepts strings or dicts."""
    if not isinstance(collocations, list):
        return None
    result = []
    for c in collocations:
        if isinstance(c, dict) and "ar" in c and "en" in c:
            result.append(c)
        elif isinstance(c, str) and c.strip():
            result.append({"ar": c.strip(), "en": ""})
    return result if result else []


def validate_hooks(hooks: dict) -> bool:
    """Check that the hooks dict has valid structure. Auto-normalizes collocations."""
    if not isinstance(hooks, dict):
        return False
    if not hooks.get("mnemonic") or not isinstance(hooks["mnemonic"], str):
        return False
    # Normalize collocations in-place
    if "collocations" in hooks and hooks["collocations"] is not None:
        hooks["collocations"] = _normalize_collocations(hooks["collocations"])
        if hooks["collocations"] is None:
            return False
    if "cognates" in hooks and hooks["cognates"] is not None:
        if not isinstance(hooks["cognates"], list):
            return False
        normalized = []
        for c in hooks["cognates"]:
            if isinstance(c, dict) and ("lang" in c or "language" in c) and "word" in c:
                if "language" in c and "lang" not in c:
                    c["lang"] = c.pop("language")
                normalized.append(c)
            elif isinstance(c, str):
                normalized.append({"lang": "?", "word": c, "note": ""})
        hooks["cognates"] = normalized
    return True


def generate_memory_hooks(lemma_id: int) -> None:
    """Background task: generate memory hooks for a single word.

    Opens its own DB session so it can run in a background thread.
    Idempotent — skips if hooks already exist.
    """
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            return
        if lemma.memory_hooks_json:
            return  # already populated

        root_obj = lemma.root
        root_info = f", root={root_obj.root}" if root_obj else ""
        root_meaning = f", root_meaning=\"{root_obj.core_meaning_en}\"" if root_obj and root_obj.core_meaning_en else ""
        etymology_hint = ""
        if lemma.etymology_json and isinstance(lemma.etymology_json, dict):
            deriv = lemma.etymology_json.get("derivation", "")
            if deriv:
                etymology_hint = f", etymology=\"{deriv}\""

        prompt = f"""Generate memory hooks for this Arabic word:

word={lemma.lemma_ar}, bare={lemma.lemma_ar_bare}, transliteration={lemma.transliteration_ala_lc or "unknown"}, pos={lemma.pos or "unknown"}, meaning="{lemma.gloss_en or "unknown"}"{root_info}{root_meaning}{etymology_hint}

Return JSON object with keys: mnemonic, cognates, collocations, usage_context, fun_fact.
Return null (not a JSON object) if the word is a particle/pronoun/function word."""

        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.7,
            )
        except AllProvidersFailed as e:
            logger.warning(f"Memory hooks LLM failed for lemma {lemma_id}: {e}")
            return

        if result is None:
            return

        # Handle null response (function words)
        if not isinstance(result, dict) or not result:
            return

        # Sometimes LLM wraps in extra layer
        hooks = result.get("hooks", result) if "hooks" in result else result

        if not validate_hooks(hooks):
            logger.warning(f"Invalid memory hooks structure for lemma {lemma_id}")
            return

        lemma.memory_hooks_json = hooks
        db.commit()
        logger.info(f"Generated memory hooks for lemma {lemma_id} ({lemma.lemma_ar_bare})")
    except Exception:
        logger.exception(f"Error generating memory hooks for lemma {lemma_id}")
        db.rollback()
    finally:
        db.close()
