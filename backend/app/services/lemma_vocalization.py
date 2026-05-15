"""Add tashkeel (full diacritization) to Arabic lemmas.

Used by:
- scripts/vocalize_unvocalized_lemmas.py — one-shot backfill
- app/services/lemma_enrichment.py — runtime gate that catches any lemma
  arriving via run_quality_gates() without diacritics, so the bad-translit
  pattern (al-ghlām for الغلام) can't reappear.

A lemma is "unvocalized" when its lemma_ar contains zero diacritic marks
(U+064B–U+065F, U+0670). Such lemmas produce broken ALA-LC transliterations
because the romanizer has no short-vowel information to encode.
"""

import re
from typing import Iterable

from app.models import Lemma
from app.services.claude_code import generate_structured
from app.services.sentence_validator import strip_diacritics, normalize_alef


_ARABIC_RANGE = range(0x0600, 0x0700)
_DIACRITIC_RE = re.compile(r"[ً-ٰٟ]")


def is_arabic_script(text: str) -> bool:
    return any(ord(c) in _ARABIC_RANGE for c in text or "")


def has_diacritic(text: str) -> bool:
    return bool(_DIACRITIC_RE.search(text or ""))


def needs_vocalization(lemma: Lemma) -> bool:
    """True if the lemma's lemma_ar is Arabic-script and lacks any diacritic."""
    ar = lemma.lemma_ar or ""
    if not ar.strip():
        return False
    if not is_arabic_script(ar):
        return False
    return not has_diacritic(ar)


_SCHEMA = {
    "type": "object",
    "properties": {
        "vocalized": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_id": {"type": "integer"},
                    "vocalized_ar": {"type": "string"},
                },
                "required": ["lemma_id", "vocalized_ar"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["vocalized"],
    "additionalProperties": False,
}


_SYSTEM = """You are an expert in Arabic morphology and orthography.

Your task: add full tashkeel (Arabic diacritical marks) to a list of Arabic
lemmas given as unvocalized text plus an English gloss and part of speech.

Rules:
- Output the same word with proper diacritics (fatha, kasra, damma, sukun,
  shadda where appropriate). No final case-ending vowel (no iʿrāb).
- The unvocalized letters MUST remain identical — only diacritics are added.
  If the input form has an attached al-prefix (e.g. الغلام), preserve it
  and vocalize as written (الغُلَام). Do NOT strip it.
- For verbs, use the canonical past-tense 3rd-singular masculine form (the
  citation form), e.g. "كَتَبَ" not "كتب".
- For nouns and adjectives, use the singular indefinite vocalization with
  no tanwīn.
- For ت marbuṭa words ending in ة, do not add a final tanwin or vowel.
- For function words / particles (e.g. قد, لو, كي, لقد), use their
  conventional vocalization.
- If a word is foreign or you genuinely cannot vocalize it, output the bare
  word unchanged.
"""


def _build_prompt(batch: Iterable[Lemma]) -> str:
    lines = ["Add tashkeel to each lemma. Reply with the JSON schema only.\n"]
    for l in batch:
        lines.append(
            f'  - lemma_id={l.lemma_id}, '
            f'form="{l.lemma_ar}", '
            f'pos={l.pos or "?"}, '
            f'gloss="{l.gloss_en or "?"}"'
        )
    return "\n".join(lines)


def vocalize_batch(batch: list[Lemma], timeout: int = 180) -> dict[int, str]:
    """Call the LLM to vocalize a batch of lemmas.

    Returns {lemma_id: vocalized_ar} for entries the LLM produced.
    Caller is responsible for validating the result against the original
    letter sequence — use `validate_proposal()` below.
    """
    prompt = _build_prompt(batch)
    result = generate_structured(
        prompt=prompt,
        system_prompt=_SYSTEM,
        json_schema=_SCHEMA,
        model="haiku",
        timeout=timeout,
    )
    return {entry["lemma_id"]: entry["vocalized_ar"] for entry in result.get("vocalized", [])}


def validate_proposal(proposal: str, lemma_ar: str) -> bool:
    """True if `proposal` is a valid vocalization of `lemma_ar`.

    The proposal must contain at least one diacritic and, after stripping
    diacritics + normalizing alef variants, must letter-match the original.
    """
    if not proposal or not lemma_ar:
        return False
    if not has_diacritic(proposal):
        return False
    return normalize_alef(strip_diacritics(proposal)) == normalize_alef(lemma_ar)


def apply_vocalization(lemma: Lemma, proposal: str) -> bool:
    """Validate the proposal and, if valid, update the lemma in place.

    Clears `transliteration_ala_lc` so the next backfill step regenerates
    it from the vocalized form (stale unvocalized translit like al-ghlām
    must not survive a vocalization update).

    Returns True if applied.
    """
    if not validate_proposal(proposal, lemma.lemma_ar or ""):
        return False
    lemma.lemma_ar = proposal
    lemma.transliteration_ala_lc = None
    return True
