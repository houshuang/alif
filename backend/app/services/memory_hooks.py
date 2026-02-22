"""Generate memory hooks (mnemonics, cognates, collocations) for words.

Called as a background task when words enter acquisition, or via backfill script.
"""

import logging

from app.database import SessionLocal
from app.models import Lemma

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You generate memory hooks for Arabic (MSA) vocabulary using the keyword mnemonic method (Atkinson & Raugh 1975). The learner speaks: English, Norwegian, Swedish, Danish, Hindi, German, French, Italian, Spanish, Greek, Latin, Indonesian, and some Russian.

BUILD THE MNEMONIC IN 4 STEPS:

STEP 1 — KEYWORD CANDIDATES: List 3-5 words/phrases from any of the learner's languages that SOUND LIKE all or part of the Arabic transliteration. Each must be concrete and visualizable. Prioritize first-syllable match. Multi-word phrases OK.

STEP 2 — PICK BEST: Choose the keyword with the best combination of (a) phonetic overlap and (b) ease of visualization. If a candidate also relates semantically to the meaning, prefer it.

STEP 3 — INTERACTIVE SCENE: Build ONE scene where the keyword and the word's meaning INTERACT — they must DO something to each other. Critical rules:
  - The meaning must be the ACTION or CENTRAL ELEMENT of the scene, not a label
  - Use "you" as the actor (self-reference aids memory)
  - 1-2 sentences max, specific and vivid
  - The keyword must appear in CAPS so the sound link is visible

STEP 4 — VERIFY: Re-read the scene. If someone hears the Arabic word, recalls the keyword, and remembers this scene — can they extract the meaning? If not, revise.

GOOD EXAMPLES:
  "kitab (book) — you see a CAT open a TAB on her laptop and start reading a BOOK, so engrossed she knocks her coffee over"
  WHY: cat+tab = sound link, BOOK = central action, interactive (cat reads book), self-reference nearby

  "husn (beauty) — you're in a HOOSEGOW jail cell, but the sunset through the bars is so BEAUTIFUL the guards stop to stare"
  WHY: hoosegow = sound link, BEAUTY = central quality that drives the action

  "raghm (despite) — a RAGTIME musician keeps playing DESPITE the rain pouring on his piano"
  WHY: rag = sound link, DESPITE = shown through consequence (playing through opposition)

BAD EXAMPLES:
  "husn — a hoosegow (jail) for ugly thoughts" — no interaction, meaning is a label, not extractable
  "it means knowledge, think of a scholar" — no sound link at all
  "picture a cat and a book on a table" — separate images, no interaction (no better than rote)

ABSTRACT WORDS (prepositions, conjunctions, abstract nouns like "freedom", "despite", "situation"):
  - Concretize through CONSEQUENCE: show what the concept DOES in concrete terms
  - Or use a VERBAL mnemonic: a sentence that links the sound-alike to the definition naturally
  - Example: "hurriyya (freedom) — you're in a HURRY to escape, sprinting through the gate into FREEDOM"

RETURN ONLY the final mnemonic text in the "mnemonic" field (not the intermediate steps).

ALSO GENERATE:

2. cognates: Words in the learner's OTHER languages from this Arabic root or sharing origin. Arabic has lent extensively to: Hindi/Urdu, Indonesian/Malay, Spanish (800 years Moorish rule), and lesser extent French, Italian, English. If the word IS a direct borrowing, mark prominently. Format: [{"lang": "Hindi", "word": "किताब (kitab)", "note": "direct borrowing — you already know this!"}]. Return [] if no cognates.

3. collocations: 2-3 common Arabic phrases. Format: [{"ar": "Arabic with full diacritics ONLY", "en": "English ONLY"}]. No transliteration in either field.

4. usage_context: 1-2 specific sentences ("in news headlines about...", "on restaurant menus"). Not generic.

5. fun_fact: One genuinely surprising fact. Return null if nothing truly interesting.

SHORTCUTS:
- Direct borrowing in Hindi/Urdu or Indonesian: note in cognates with "direct borrowing — you already know this!" and keep mnemonic brief.
- Particles, pronouns, basic function words: return null for the ENTIRE entry.
- Proper nouns: return null for the entire entry.

Return JSON: {"mnemonic": "...", "cognates": [...], "collocations": [...], "usage_context": "...", "fun_fact": "..."}"""

PREMIUM_SYSTEM_PROMPT = """You generate memory hooks for Arabic (MSA) vocabulary. This word is HARD for the learner — previous mnemonics didn't stick. Use the overgenerate-and-rank method to produce a superior mnemonic.

The learner speaks: English, Norwegian, Swedish, Danish, Hindi, German, French, Italian, Spanish, Greek, Latin, Indonesian, and some Russian.

GENERATE 3 CANDIDATE MNEMONICS, then pick the best:

For each candidate:
1. Pick a different keyword (English/Norwegian/etc. word that sounds like the Arabic transliteration)
2. Build an interactive scene where keyword and meaning DO something to each other
3. Use "you" as actor, meaning as the ACTION/central element, keyword in CAPS

Then SELF-EVALUATE each candidate on 3 criteria (1-5 scale):
  - Sound match: how closely does the keyword sound like the Arabic word?
  - Interaction: do keyword and meaning actively interact, or just coexist?
  - Meaning extraction: if you recall the image, can you extract the definition?

Pick the candidate with the highest total score.

ALSO GENERATE cognates, collocations, usage_context, fun_fact (same rules as standard prompt).

Return JSON: {"candidates": [{"keyword": "...", "mnemonic": "...", "sound_match": N, "interaction": N, "extraction": N}], "best_index": N, "mnemonic": "THE WINNING MNEMONIC TEXT", "cognates": [...], "collocations": [...], "usage_context": "...", "fun_fact": "..."}

SHORTCUTS:
- Particles, pronouns, basic function words: return null for the ENTIRE entry.
- Proper nouns: return null for the entire entry."""


def _build_word_info(lemma: "Lemma") -> str:
    """Build the word info string used in prompts."""
    root_obj = lemma.root
    root_info = f", root={root_obj.root}" if root_obj else ""
    root_meaning = f', root_meaning="{root_obj.core_meaning_en}"' if root_obj and root_obj.core_meaning_en else ""
    etymology_hint = ""
    if lemma.etymology_json and isinstance(lemma.etymology_json, dict):
        deriv = lemma.etymology_json.get("derivation", "")
        if deriv:
            etymology_hint = f', etymology="{deriv}"'
    return (
        f'word={lemma.lemma_ar}, bare={lemma.lemma_ar_bare}, '
        f'transliteration={lemma.transliteration_ala_lc or "unknown"}, '
        f'pos={lemma.pos or "unknown"}, '
        f'meaning="{lemma.gloss_en or "unknown"}"'
        f'{root_info}{root_meaning}{etymology_hint}'
    )


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
    Uses overgenerate-and-rank (3 candidates, self-evaluate, pick best)
    with Sonnet for quality. Since hooks are always background tasks
    and Claude CLI is free, there's no cost to better quality.
    """
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            return
        if lemma.memory_hooks_json:
            return  # already populated

        word_info = _build_word_info(lemma)
        prompt = f"""Generate memory hooks for this Arabic word:

{word_info}

Generate 3 candidate mnemonics with different keywords, self-evaluate, pick the best.
Return JSON with keys: candidates, best_index, mnemonic, cognates, collocations, usage_context, fun_fact.
Return null if the word is a particle/pronoun/function word."""

        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=PREMIUM_SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.8,
                model_override="claude_sonnet",
                task_type="memory_hooks",
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

        # Strip the candidates/best_index metadata before storing
        hooks.pop("candidates", None)
        hooks.pop("best_index", None)

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


def regenerate_memory_hooks_premium(lemma_id: int) -> None:
    """Background task: regenerate hooks using overgenerate-and-rank.

    Triggered when a word lapses or repeatedly fails — the existing
    mnemonic didn't stick. Generates 3 candidates, self-evaluates,
    picks the best. Uses Sonnet (stronger model) for better quality.
    Always overwrites existing hooks.
    """
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            return

        old_mnemonic = ""
        if lemma.memory_hooks_json and isinstance(lemma.memory_hooks_json, dict):
            old_mnemonic = lemma.memory_hooks_json.get("mnemonic", "")

        word_info = _build_word_info(lemma)
        failed_note = ""
        if old_mnemonic:
            failed_note = f'\n\nThe previous mnemonic FAILED (the learner lapsed). Do NOT reuse it:\n  "{old_mnemonic}"'

        prompt = f"""Generate premium memory hooks for this HARD Arabic word:

{word_info}{failed_note}

Generate 3 candidate mnemonics with different keywords, self-evaluate, pick the best.
Return JSON with keys: candidates, best_index, mnemonic, cognates, collocations, usage_context, fun_fact.
Return null if the word is a particle/pronoun/function word."""

        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=PREMIUM_SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.8,
                model_override="claude_sonnet",
                task_type="memory_hooks_premium",
            )
        except AllProvidersFailed as e:
            logger.warning(f"Premium memory hooks LLM failed for lemma {lemma_id}: {e}")
            return

        if result is None or not isinstance(result, dict) or not result:
            return

        hooks = result.get("hooks", result) if "hooks" in result else result

        # Strip the candidates/best_index metadata before storing
        hooks.pop("candidates", None)
        hooks.pop("best_index", None)

        if not validate_hooks(hooks):
            logger.warning(f"Invalid premium hooks structure for lemma {lemma_id}")
            return

        lemma.memory_hooks_json = hooks
        db.commit()
        logger.info(
            f"Regenerated premium memory hooks for lemma {lemma_id} "
            f"({lemma.lemma_ar_bare})"
        )
    except Exception:
        logger.exception(f"Error generating premium hooks for lemma {lemma_id}")
        db.rollback()
    finally:
        db.close()
