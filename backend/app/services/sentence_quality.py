"""Pure-string regex helpers that flag low-quality sentences.

Phase 1 awkward-sentence prevention: a regex pre-filter for corpus-import
fragments (anaphoric continuations, dialogue-only chunks, demonstrative/
pronoun openers without antecedent). Lifted from the simulator at
/tmp/claude/awkward/simulate_b1.py — combined precision 79% / recall 81%
on the hand-graded Hindawi corpus eval set.

No DB access. No LLM. Just strings in, (bool, rule_name) out.
"""
from __future__ import annotations

import re

# Anaphoric multi-letter openers (single-letter و/ف are handled separately
# below — only when the connective fatha is present, to spare lexical words
# like فِي / وُجِدَ).
_ANAPHORIC_OPENERS_BARE = {
    "ثم", "لكن", "لكنّ", "فإذا", "فلما", "وعندما", "وفي",
    "فقال", "فقالت", "فأخذ",
}

# Diacritic codepoint we use for the و/ف disambiguation.
_FATHA = "\u064E"  # ـَ — marks the connective particle (فَ / وَ)

_TERMINAL = (".", "؟", "!", "»", "…", "؛")

_DEMONSTRATIVES_BARE = {
    "هذا", "هذه", "ذلك", "تلك", "هؤلاء", "أولئك",
}

# Standalone anaphoric pronouns. ها/ه/هم suffixes get caught attached to
# words too, so we restrict to length>=2 to avoid matching attached ـه.
_PRONOUN_ANAPHOR_BARE = {
    "هو", "هي", "هم", "هن", "هما", "ها",
}

_DIACRITICS_RE = re.compile(r"[\u064b-\u065f\u0670]")


def _strip_diacritics(s: str) -> str:
    return _DIACRITICS_RE.sub("", s)


def _first_word(s: str) -> str:
    bare = _strip_diacritics(s.strip())
    parts = bare.split()
    return parts[0] if parts else ""


def _r1_anaphoric_opener(text: str) -> bool:
    """Opens with the connective particle و/ف (only when explicitly
    diacritized as fatha — `وَ` / `فَ` — to spare lexical words like
    فِي / وُجِدَ) or with a multi-letter anaphoric connector
    (ثم/لكن/فإذا/...).

    Heuristic: look at the *raw* first word (no diacritic stripping).
    If it starts with و or ف and the next codepoint is fatha (ـَ),
    it's connective. Anything else (kasra, damma, sukun, no mark,
    shadda) is treated as lexical and does not trip R1.
    """
    raw = text.strip()
    if not raw:
        return False
    raw_first = raw.split()[0] if raw.split() else ""
    if raw_first and raw_first[0] in ("و", "ف"):
        # Need at least one diacritic mark on the first letter to call it
        # connective; bare-letter prefixes are ambiguous and we err on
        # the side of keeping them.
        if len(raw_first) >= 2 and raw_first[1] == _FATHA:
            return True
    bare = _strip_diacritics(raw)
    parts = bare.split()
    return bool(parts) and parts[0] in _ANAPHORIC_OPENERS_BARE


def _r3_no_terminal(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and not any(stripped.endswith(c) for c in _TERMINAL)


def _r5_dialogue_only(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("«") and stripped.endswith("»")


def _r7_demonstrative_subject(text: str) -> bool:
    """Demonstrative (hadhā/hadhihi/dhālika/tilka/...) in first 3 words —
    almost always an anaphoric subject pointing at an unstated referent."""
    bare = _strip_diacritics(text.strip())
    words = bare.split()[:3]
    return any(w in _DEMONSTRATIVES_BARE for w in words)


def _r8_pronoun_subject(text: str) -> bool:
    """First word is a standalone anaphoric pronoun (huwa/hiya/...)."""
    return _first_word(text) in _PRONOUN_ANAPHOR_BARE


# Order matters only for the rule_name returned on first match.
_RULES: tuple[tuple[str, callable], ...] = (
    ("R1_ANAPHORIC_OPENER", _r1_anaphoric_opener),
    ("R3_NO_TERMINAL", _r3_no_terminal),
    ("R5_DIALOGUE_ONLY", _r5_dialogue_only),
    ("R7_DEMONSTRATIVE_SUBJECT", _r7_demonstrative_subject),
    ("R8_PRONOUN_SUBJECT", _r8_pronoun_subject),
)


def fails_corpus_regex_filter(arabic_text: str) -> tuple[bool, str | None]:
    """Return (fails, rule_name) — True if the sentence should be rejected.

    Rules:
    - R1_ANAPHORIC_OPENER: opens with the connective particle وَ / فَ
      (single-letter prefix with explicit fatha — bare letters and
      kasra/damma vocalizations are spared, so فِي and وُجِدَ pass) or
      ثم / لكن / لكنّ / فإذا / فلما / وعندما / وفي / فقال / ...
    - R3_NO_TERMINAL: no terminal punctuation (. ؟ ! » … ؛)
    - R5_DIALOGUE_ONLY: entirely inside «...» quotes
    - R7_DEMONSTRATIVE_SUBJECT: hadhā/hadhihi/dhālika/tilka/hāʾulāʾi/ʾūlāʾika
      in first 3 words (anaphoric subject)
    - R8_PRONOUN_SUBJECT: first word is a standalone anaphoric pronoun
      (huwa/hiya/hum/hunna/humā/hā)

    Returns the first matching rule name. Diacritic-stripping applied
    before matching so vowelled and bare text behave the same.
    """
    if not arabic_text or not arabic_text.strip():
        return False, None
    for name, fn in _RULES:
        if fn(arabic_text):
            return True, name
    return False, None
