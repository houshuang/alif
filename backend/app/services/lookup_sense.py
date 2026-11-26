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

from app.services.preread_service import (
    _MAX_GLOSS_CHARS,
    _is_widening,
    _vocalized_key,
    candidate_line,
    sibling_choice,
)

logger = logging.getLogger(__name__)

VERDICTS = ("fits", "broader", "different", "name", "sibling")

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "lemma_ar": {"type": "string"},
        "gloss_en": {"type": "string"},
        "pos": {"type": "string"},
        "candidate_id": {"type": "integer"},
    },
    "required": ["verdict"],
}


def check_lookup_sense(sentence: str, surface: str, entry: dict) -> dict | None:
    """Classify how ``surface`` in ``sentence`` relates to the matched ``entry``
    ({lemma_ar, gloss_en, pos, candidates?}). Returns {verdict, gloss_en?,
    lemma_ar?, pos?}, or None when the call fails or its answer does not pass
    validation.

    ``entry["candidates"]`` ([{lemma_id, lemma_ar, gloss_en, pos}]) are other
    vocabulary entries with the same spelling that the word alone could not
    rule out (a homograph tie). The check may then answer
    ``{"verdict": "sibling", "lemma_id": <offered id>}``."""
    from app.services.llm import generate_completion

    candidates = entry.get("candidates") or []
    others = (
        "OTHER ENTRIES WITH THIS SPELLING:\n"
        + "\n".join(f"- {candidate_line(c)}" for c in candidates)
        + "\n"
        if candidates
        else ""
    )
    sibling_rule = (
        '- "sibling": the tapped word is one of the other entries with this '
        "spelling. Give candidate_id: that entry's id. Prefer it over "
        '"different" whenever a listed entry is the word used.\n'
        if candidates
        else ""
    )
    prompt = (
        "An adult learner of Arabic tapped a word while reading. Their app matched "
        "it to an entry in their vocabulary. Decide whether a reader relying on that "
        "entry would understand the word in this sentence.\n\n"
        f"SENTENCE: {sentence[:1500]}\n"
        f"TAPPED WORD: {surface}\n"
        f"MATCHED ENTRY: {entry.get('lemma_ar')} ({entry.get('pos') or '?'}) — "
        f"{entry.get('gloss_en') or ''}\n"
        f"{others}\n"
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
        f"{sibling_rule}"
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
    v = validate_verdict(res, entry)
    if v and v["verdict"] == "broader":
        from app.services.preread_service import gate_widenings

        item = {"lemma_id": entry.get("lemma_id"), "lemma_ar": entry.get("lemma_ar"),
                "pos": entry.get("pos"), "gloss_en": entry.get("gloss_en"),
                "forms": [surface], "context": sentence[:300]}
        gate_widenings(sentence, [(item, v)])
    return v


def validate_verdict(res: object, entry: dict) -> dict | None:
    """Keep only well-formed verdicts: a widening must keep part of the stored
    meaning, and a different word must have a different citation form."""
    if not isinstance(res, dict) or res.get("verdict") not in VERDICTS:
        return None
    sibling = sibling_choice(res, entry.get("candidates"))
    if sibling is not None:
        return {"verdict": "sibling", "lemma_id": sibling}
    verdict = res["verdict"]
    if verdict == "sibling":
        return None
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
