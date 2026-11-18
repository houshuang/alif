"""Check one tapped word's stored entry against the sentence it was tapped in.

Snap's tap-a-word lookup resolves a surface to a vocabulary entry without
context, and the resolver can land on a same-skeleton neighbour (أمضت → ماضِي
"past" instead of أَمْضَى "to spend (time)", الفتى → أَلْف) or on an entry whose
gloss lacks the meaning used here (حِصَّة "class period" for "share"). One LLM
call classifies the match; the router applies the verdict.
"""

from __future__ import annotations

import logging
import re

from app.services.preread_service import _MAX_GLOSS_CHARS, _is_widening, _vocalized_key

logger = logging.getLogger(__name__)

VERDICTS = ("fits", "broader", "different", "name")

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "lemma_ar": {"type": "string"},
        "gloss_en": {"type": "string"},
        "pos": {"type": "string"},
    },
    "required": ["verdict"],
}


def check_lookup_sense(sentence: str, surface: str, entry: dict) -> dict | None:
    """Classify how ``surface`` in ``sentence`` relates to the matched ``entry``
    ({lemma_ar, gloss_en, pos}). Returns {verdict, gloss_en?, lemma_ar?, pos?},
    or None when the call fails or its answer does not pass validation."""
    from app.services.llm import generate_completion

    prompt = (
        "An adult learner of Arabic tapped a word while reading. Their app matched "
        "it to an entry in their vocabulary. Decide whether a reader relying on that "
        "entry would understand the word in this sentence.\n\n"
        f"SENTENCE: {sentence[:1500]}\n"
        f"TAPPED WORD: {surface}\n"
        f"MATCHED ENTRY: {entry.get('lemma_ar')} ({entry.get('pos') or '?'}) — "
        f"{entry.get('gloss_en') or ''}\n\n"
        "Answer with one verdict:\n"
        '- "fits": the entry is the word used here and its gloss covers this use '
        "(a rewording or a near synonym still fits).\n"
        '- "broader": the same word (same vocalized citation form and part of '
        "speech), but the gloss lacks or misstates the meaning used here (قَرْن "
        'stored as "horn" where the sentence means "century"). Give gloss_en: a '
        "short dictionary gloss that puts the meaning used here first and keeps "
        'correct stored meanings, e.g. "century; horn". It must stay generally '
        "usable, not tied to this sentence.\n"
        '- "different": the tapped word is a different word that only shares '
        "letters with the entry: another verb form, another vocalization, another "
        "part of speech, a different root after removing prefixes and suffixes, or "
        "an unrelated homograph (أمضت is أَمْضَى \"to spend (time)\", not ماضٍ "
        '"past"; ابنها is اِبْن "son", not أَب "father"). Give lemma_ar: the fully '
        "vocalized citation form of the word used here (singular noun, past-tense "
        "3ms verb, no ال, no attached pronouns), pos, and gloss_en: a short general "
        "dictionary gloss that includes the meaning used here.\n"
        '- "name": the tapped word is (part of) a proper name here.\n'
        "Glosses are plain English with no Arabic script, under 80 characters."
    )
    try:
        res = generate_completion(
            prompt=prompt,
            system_prompt="You are an experienced Arabic lexicographer and reading teacher.",
            json_schema=_SCHEMA,
            temperature=0.0,
            model_override="claude_sonnet",
            task_type="snap_lookup_sense",
        )
    except Exception as e:
        logger.warning("lookup sense check failed: %s", e)
        return None
    return validate_verdict(res, entry)


def validate_verdict(res: object, entry: dict) -> dict | None:
    """Keep only well-formed verdicts: a widening must keep part of the stored
    meaning, and a different word must have a different citation form."""
    if not isinstance(res, dict) or res.get("verdict") not in VERDICTS:
        return None
    verdict = res["verdict"]
    if verdict in ("fits", "name"):
        return {"verdict": verdict}
    gloss = (res.get("gloss_en") or "").strip()
    if not gloss or len(gloss) > _MAX_GLOSS_CHARS or re.search(r"[\u0600-\u06ff]", gloss):
        return None
    if verdict == "broader":
        if not _is_widening(entry.get("gloss_en") or "", gloss):
            return None
        return {"verdict": "broader", "gloss_en": gloss}
    new_ar = (res.get("lemma_ar") or "").strip()
    if not new_ar or _vocalized_key(new_ar) == _vocalized_key(entry.get("lemma_ar") or ""):
        return None
    return {
        "verdict": "different",
        "lemma_ar": new_ar,
        "gloss_en": gloss,
        "pos": (res.get("pos") or "").strip() or None,
    }
