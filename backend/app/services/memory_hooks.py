"""Generate memory hooks (mnemonics, cognates, collocations) for words.

Called as a background task when words enter acquisition, or via backfill script.
"""

import logging
import os

from app.database import SessionLocal
from app.models import Lemma

logger = logging.getLogger(__name__)


def memory_hooks_enabled() -> bool:
    """Master switch for mnemonic generation. Disabled 2026-05-22 after a
    calibration study found most auto-generated mnemonics low-quality and the
    quality boundary not reliably gateable (held-out kappa = -0.12). Re-enabled
    2026-07-20 with a redesigned pipeline: recognition-direction full-cover
    generation prompt + independent 4-check storage judge calibrated on 60 user
    ratings (see the 2026-07-20 experiment-log entry). Set
    ALIF_MEMORY_HOOKS_ENABLED=1 to enable."""
    return os.getenv("ALIF_MEMORY_HOOKS_ENABLED", "0") == "1"


# Model for hook generation + judging. gpt-5.6-sol chosen by the 2026-07-20
# eval; falls back to the Claude CLI chain when codex is unavailable.
HOOK_MODEL = os.getenv("ALIF_HOOK_MODEL", "gpt-5.6-sol")

GENERATION_SYSTEM_PROMPT = """You generate memory hooks for Arabic (MSA) vocabulary. The learner speaks: English, Norwegian, Swedish, Danish, Hindi, German, French, Italian, Spanish, Greek, Latin, Indonesian, and some Russian.

CRITICAL DIRECTION: the learner ONLY practices recognition — they SEE or HEAR the Arabic word and must recall the English meaning. Never the reverse. The hook must FIRE when reading the Arabic word: its sound must automatically summon the keyword phrase without effort.

THE FULL-COVER RULE (the single most important criterion, from this learner's own ratings):
The keyword phrase, spoken aloud, must reconstruct (nearly) the ENTIRE Arabic word's sound IN ORDER. Not just the first syllable. Not a loose rhyme. Not scrambled. The keyword must be real, common words in the learner's languages — NEVER the Arabic word itself dressed up as a name or object, and never obscure terms.

GOLD EXAMPLES (this learner's actual favorites):
- zamjara (to growl) → "a ZOMBIE in a JAR, growling" — zam-jar covers the whole word
- muḥāṣar (surrounded) → "a MOO HAZARD: surrounded by cows" — mu-ha-sar fully covered
- ḥasharah (insect) → "you whisper HUSH, SARAH! as she stalks a buzzing insect" — ha-sha-ra in order
- iḥtaḍan (to embrace) → "I HUG DAN" — ih-ta-dan, and the phrase IS the meaning
- iʿtadhar (to apologize) → "I ATE THE RAW onion, then apologized" — meaning as natural consequence

REJECTED BY THIS LEARNER (do not produce these patterns):
- iḥtimāl → "ITTY MALL" — drops the iḥ-; partial cover fails
- namā → "shout the seed's NAMA (name)" — circular, no real sound-alike
- khaddar → "KHADDAR cloth" — the "keyword" is just the Arabic word; anchors nothing
- tawahhaja → "TOW A HEDGE — it suddenly glows" — the meaning is bolted on, nothing in the scene produces it
- "SAM names MA on her name tag" — mundane, fully plausible scenes leave no memory trace
- long elaborate scenes — ONE compact vivid image, max ~15 words

METHOD:
1. Say the transliteration aloud syllable by syllable. Find keyword phrases (any of the learner's languages) that cover ALL of it in order. Generate 4-5 candidates; discard any that miss syllables or use the Arabic word itself.
2. For each survivor, build ONE compact image (max ~15 words) where the keyword ENACTS the meaning — the meaning should be the action or punchline, and the scene should be surprising or absurd enough to stick.
3. Self-score each candidate 1-5 on: cover (whole word in order?), trigger (auto-evoked on reading?), extraction (image hands you the meaning?).
4. Pick the best. If NO candidate scores >=4 on ALL THREE, return null for the entire entry — a missing hook is better than a bad one. Returning null for a third of words is correct behavior.

ALSO GENERATE: cognates (genuine ones in the learner's languages, [] if none; mark direct borrowings prominently), collocations (2-3, Arabic with full diacritics + English only), usage_context (1-2 specific sentences), fun_fact (or null).

Particles, pronouns, function words, proper nouns: return null for the entire entry.

Return JSON: {"candidates": [{"keyword": "...", "mnemonic": "...", "cover": N, "trigger": N, "extraction": N}], "best_index": 0, "mnemonic": "...", "cognates": [...], "collocations": [...], "usage_context": "...", "fun_fact": "..."}"""


JUDGE_SYSTEM_PROMPT = """You judge Arabic vocabulary mnemonics for a learner who ONLY practices recognition: they see/hear the Arabic word and must recall the English meaning. Decide if a mnemonic is worth storing.

FOUR CHECKS — the mnemonic must pass ALL four:

1. KNOWN-WORD ANCHOR: the keyword must be real, common words or phrases in the learner's languages (English, Norwegian, Swedish, Danish, German, French, Italian, Spanish, Hindi, Indonesian, Greek, Latin). FAIL if the "keyword" is the Arabic word itself dressed up (e.g. "KHADDAR cloth", "a TALA lock", "NAQAD cash", "a RANA frog"), an invented word, or an obscure term.

2. ENACTED MEANING: keyword and meaning must combine into ONE unified scene in which the meaning is physically enacted or directly expressed — not merely asserted as an afterthought.
   PASS: whispering "HUSH, SARAH!" while she stalks a buzzing INSECT · "I ATE THE RAW onion, then apologized" · a giant TAFFY slab that FLOATS you across the sea.
   FAIL: "TOW A HEDGE — it suddenly glows" (glow merely asserted) · "OUAF, FARAH! — her dog provides coupons, saving money" (nothing connects) · "hey ADA, don't spill the JUICE" for ada=juice (meaning has no role in the scene).

3. AUTOMATIC TRIGGER: spoken aloud, the keyword phrase reconstructs enough of the Arabic word's sound, in order, that reading the Arabic evokes it without effort. Near-covers are fine ("TAFFY" for tafa, "ZAP-DIY" for zabdiyya). FAIL only when scrambled or fragmentary ("ALL MOOSE A-WEIGH" for musawaa).

4. MEMORABLE ODDITY: the scene must be surprising, absurd, or vivid enough to stick. Mundane, fully plausible scenes FAIL even when everything else passes.
   PASS: a MAT of TUNA rock-firm underfoot · a DAM crossing a border to annex land · a ZOMBIE in a JAR growling.
   FAIL: "one smell of the SHAM perfume exposes the fake" · "a DAUB of wax melts down the candle" · "SAM names MA on her name tag" · "ANN finishes the jazz set — accomplished".

CALIBRATION: a false STORE (a mediocre hook shown) is cheap; storing a hook that fails check 1, 2, or 4 is worthless. Be strict on 1, 2, 4. On check 3, lean toward pass when genuinely borderline.

Return JSON: {"known_anchor": bool, "enacted_meaning": bool, "automatic_trigger": bool, "memorable_oddity": bool, "storable": bool, "reason": "one short sentence"}
storable = all four true."""


HOOK_GENERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "mnemonic": {"type": "string"},
                    "cover": {"type": "integer"},
                    "trigger": {"type": "integer"},
                    "extraction": {"type": "integer"},
                },
                "required": ["keyword", "mnemonic", "cover", "trigger", "extraction"],
            },
        },
        "best_index": {"type": ["integer", "null"]},
        "mnemonic": {"type": ["string", "null"]},
        "cognates": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "lang": {"type": "string"},
                    "word": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["lang", "word", "note"],
            },
        },
        "collocations": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {"ar": {"type": "string"}, "en": {"type": "string"}},
                "required": ["ar", "en"],
            },
        },
        "usage_context": {"type": ["string", "null"]},
        "fun_fact": {"type": ["string", "null"]},
    },
    "required": ["candidates", "best_index", "mnemonic", "cognates", "collocations", "usage_context", "fun_fact"],
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "known_anchor": {"type": "boolean"},
        "enacted_meaning": {"type": "boolean"},
        "automatic_trigger": {"type": "boolean"},
        "memorable_oddity": {"type": "boolean"},
        "storable": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["known_anchor", "enacted_meaning", "automatic_trigger", "memorable_oddity", "storable", "reason"],
}


def _call_hooks_llm(prompt: str, system_prompt: str, schema: dict, task_type: str) -> dict | None:
    """Codex gpt-5.6-sol first (2026-07-20 eval winner), Claude CLI chain fallback."""
    from app.services.codex_cli import generate_via_codex_cli, CodexCLIError

    try:
        return generate_via_codex_cli(
            prompt=prompt, system_prompt=system_prompt, json_mode=True,
            json_schema=schema, timeout=180, model=HOOK_MODEL,
        )
    except CodexCLIError as e:
        logger.info(f"codex hook call failed ({task_type}), falling back to Claude: {e}")

    from app.services.llm import generate_completion, AllProvidersFailed

    try:
        return generate_completion(
            prompt=prompt, system_prompt=system_prompt, json_mode=True,
            temperature=0.8, model_override="claude_sonnet", task_type=task_type,
        )
    except AllProvidersFailed as e:
        logger.warning(f"All providers failed for {task_type}: {e}")
        return None


def judge_memory_hook(word_info: str, mnemonic: str) -> tuple[bool, str]:
    """Independent storage judge: 4 checks calibrated on 60 user ratings
    (2026-07-20). Judge failure counts as rejection — verification failure
    is never success."""
    prompt = f"""Judge this mnemonic:

{word_info}

Mnemonic: {mnemonic}"""
    result = _call_hooks_llm(prompt, JUDGE_SYSTEM_PROMPT, JUDGE_SCHEMA, "memory_hook_judge")
    if not isinstance(result, dict) or "known_anchor" not in result:
        return False, "judge_unavailable"
    # Decision rule from the 2026-07-20 threshold analysis over 60 user labels:
    # anchor AND enacted AND trigger — 85% show-recall, bad-leak 32%→18%.
    # memorable_oddity vetoed too many user favorites (48% recall); it is
    # recorded in the reason but does not block.
    storable = bool(
        result.get("known_anchor")
        and result.get("enacted_meaning")
        and result.get("automatic_trigger")
    )
    reason = str(result.get("reason", ""))[:200]
    if not result.get("memorable_oddity"):
        reason = f"[not odd] {reason}"[:200]
    return storable, reason


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


QUALITY_MIN_COMPONENT_SCORE = 4
_SCORE_ALIASES = {
    "sound_match": ("sound_match", "cover", "sound", "phonetic_match", "phonetic_overlap"),
    "interaction": ("interaction", "trigger", "imagery", "interactive_scene"),
    "extraction": ("extraction", "meaning_extraction", "meaning", "extractability"),
}


def _score(candidate: dict, name: str) -> int | None:
    for key in _SCORE_ALIASES[name]:
        value = candidate.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _best_candidate(hooks: dict) -> dict | None:
    candidates = hooks.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None

    index = hooks.get("best_index")
    try:
        idx = int(index)
    except (TypeError, ValueError):
        idx = None

    if idx is not None:
        if 0 <= idx < len(candidates) and isinstance(candidates[idx], dict):
            return candidates[idx]
        one_based = idx - 1
        if 0 <= one_based < len(candidates) and isinstance(candidates[one_based], dict):
            return candidates[one_based]

    scored: list[tuple[int, dict]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        scores = [_score(candidate, name) for name in ("sound_match", "interaction", "extraction")]
        if any(s is None for s in scores):
            continue
        scored.append((sum(scores), candidate))
    if not scored:
        return None
    return max(scored, key=lambda item: item[0])[1]


def _has_direct_borrowing(hooks: dict) -> bool:
    cognates = hooks.get("cognates")
    if not isinstance(cognates, list):
        return False
    for cognate in cognates:
        if not isinstance(cognate, dict):
            continue
        text = " ".join(
            str(cognate.get(k, ""))
            for k in ("lang", "word", "note")
        ).lower()
        if "direct borrowing" in text or "you already know" in text:
            return True
    return False


def hook_quality_reason(hooks: dict) -> tuple[bool, str]:
    """Return whether hooks are worth storing, plus a short reason."""
    if _has_direct_borrowing(hooks):
        return True, "direct_borrowing"

    candidate = _best_candidate(hooks)
    if candidate is None:
        return False, "missing_candidate_scores"

    scores = {
        name: _score(candidate, name)
        for name in ("sound_match", "interaction", "extraction")
    }
    if any(value is None for value in scores.values()):
        return False, "incomplete_candidate_scores"
    weak = [
        name
        for name, value in scores.items()
        if value < QUALITY_MIN_COMPONENT_SCORE
    ]
    if weak:
        return False, "weak_" + "_".join(weak)
    return True, "strong_candidate"


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


def prepare_hooks_for_storage(hooks: dict) -> tuple[dict | None, str]:
    """Validate, quality-gate, and strip generation metadata before storage."""
    if not validate_hooks(hooks):
        return None, "invalid_structure"
    quality_ok, reason = hook_quality_reason(hooks)
    if not quality_ok:
        return None, reason
    if reason == "strong_candidate":
        candidate = _best_candidate(hooks)
        mnemonic = candidate.get("mnemonic") if isinstance(candidate, dict) else None
        if isinstance(mnemonic, str) and mnemonic.strip():
            hooks["mnemonic"] = mnemonic.strip()
    hooks.pop("candidates", None)
    hooks.pop("best_index", None)
    hooks.pop("quality", None)
    hooks.pop("quality_reason", None)
    return hooks, reason


def generate_memory_hooks(lemma_id: int) -> None:
    """Background task: generate memory hooks for a single word.

    Opens its own DB session so it can run in a background thread.
    Idempotent — skips if hooks already exist. Generation and judging run
    while the session holds no dirty state (write happens last, Rule 10).
    """
    if not memory_hooks_enabled():
        return

    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            return
        if lemma.memory_hooks_json:
            return  # already populated

        _generate_judge_and_store(db, lemma, task_type="memory_hooks")
    except Exception:
        logger.exception(f"Error generating memory hooks for lemma {lemma_id}")
        db.rollback()
    finally:
        db.close()


def _generate_judge_and_store(db, lemma, task_type: str, failed_note: str = "") -> None:
    """Generate → self-gate → independent judge → store.

    Judge-approved hooks get ``approved_at``/``approved_by`` stamped — the
    frontend displays ONLY approved mnemonics. Rejected hooks are stored
    WITHOUT the stamp: cognates/collocations remain usable, the mnemonic stays
    hidden, and the populated row keeps lazy-generation idempotent (no retry
    loop burning tokens on the same hard word).
    """
    from datetime import datetime, timezone

    word_info = _build_word_info(lemma)
    prompt = f"""Generate a recognition-direction memory hook for this Arabic word:

{word_info}{failed_note}

Follow the METHOD. Full cover in order, compact image, meaning as the punchline.
Return null (entire entry) if no candidate reaches 4/5 on cover, trigger, AND extraction."""

    result = _call_hooks_llm(prompt, GENERATION_SYSTEM_PROMPT, HOOK_GENERATION_SCHEMA, task_type)
    if result is None or not isinstance(result, dict) or not result:
        return

    hooks = result.get("hooks", result) if "hooks" in result else result

    storage_hooks, reason = prepare_hooks_for_storage(hooks)
    if storage_hooks is None:
        logger.info(f"Discarded memory hooks for lemma {lemma.lemma_id}: {reason}")
        return

    mnemonic = storage_hooks.get("mnemonic")
    if mnemonic:
        storable, judge_reason = judge_memory_hook(word_info, mnemonic)
        if storable:
            storage_hooks["approved_at"] = datetime.now(timezone.utc).isoformat()
            storage_hooks["approved_by"] = f"judge:{HOOK_MODEL}"
        else:
            logger.info(
                f"Hook judge rejected mnemonic for lemma {lemma.lemma_id} "
                f"({lemma.lemma_ar_bare}): {judge_reason}"
            )

    lemma.memory_hooks_json = storage_hooks
    db.commit()
    logger.info(
        f"Stored memory hooks for lemma {lemma.lemma_id} "
        f"({lemma.lemma_ar_bare}, quality={reason}, "
        f"approved={'yes' if storage_hooks.get('approved_at') else 'no'})"
    )


def regenerate_memory_hooks_premium(lemma_id: int) -> None:
    """Background task: regenerate hooks for a word whose mnemonic didn't stick.

    Triggered when a word lapses or repeatedly fails. Same judged pipeline as
    generate_memory_hooks, with the failed mnemonic excluded. Overwrites
    existing hooks.
    """
    if not memory_hooks_enabled():
        return

    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            return

        old_mnemonic = ""
        if lemma.memory_hooks_json and isinstance(lemma.memory_hooks_json, dict):
            old_mnemonic = lemma.memory_hooks_json.get("mnemonic", "")
        failed_note = ""
        if old_mnemonic:
            failed_note = (
                "\n\nThe previous mnemonic FAILED (the learner lapsed). "
                f'Do NOT reuse it:\n  "{old_mnemonic}"'
            )

        _generate_judge_and_store(
            db, lemma, task_type="memory_hooks_premium", failed_note=failed_note
        )
    except Exception:
        logger.exception(f"Error generating premium hooks for lemma {lemma_id}")
        db.rollback()
    finally:
        db.close()
