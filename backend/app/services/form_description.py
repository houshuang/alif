"""A short English line saying which form of a word a sentence shows.

يَسْتَخْدِمُونَ (of اِسْتَخْدَمَ) -> "present · they (m.)"; وَبِكُتُبِهِ (of كِتَاب) ->
"plural". Shown in the reading word panel after a miss. Attached words (وَ، بِ،
الـ، ـهُ) are not described.

CAMeL Tools returns every reading of the bare letters, so the readings are
filtered twice: the dictionary form must be this lemma, and every vowel mark the
sentence shows (sukun included) must agree with the reading wherever the reading
marks that letter. Only
features all remaining readings agree on are described; a subject that differs
between two readings is shown as "you (m.) or she". No reading, or nothing
worth saying (a bare singular), gives None.
"""
from __future__ import annotations

from app.services.sentence_validator import normalize_alef, strip_diacritics

_MARKS = set("ًٌٍَُِّ")  # tanween, short vowels, shadda (not sukun)
_SUKUN, _SHADDA = "\u0652", "\u0651"
_ALL_MARKS = _MARKS | {_SUKUN, "\u0670"}

_ASPECT = {"p": "past", "i": "present", "c": "command"}
_SUBJECT = {
    ("1", "s"): "I", ("1", "d"): "we", ("1", "p"): "we",
    ("2", "m", "s"): "you (m.)", ("2", "f", "s"): "you (f.)", ("2", "d"): "you (two)",
    ("2", "m", "p"): "you (pl.)", ("2", "f", "p"): "you (f. pl.)",
    ("3", "m", "s"): "he", ("3", "f", "s"): "she", ("3", "m", "d"): "they (two)", ("3", "f", "d"): "they (two f.)",
    ("3", "m", "p"): "they (m.)", ("3", "f", "p"): "they (f.)",
}


def _letters_and_marks(text: str) -> list[tuple[str, set[str]]]:
    out: list[tuple[str, set[str]]] = []
    for ch in text or "":
        if ch in _ALL_MARKS:
            if out and (ch in _MARKS or ch == _SUKUN):
                out[-1][1].add(ch)
        elif ch == "ـ":  # tatweel
            continue
        else:
            out.append((normalize_alef(ch), set()))
    return out


def _fits(surface: str, diac: str) -> bool:
    """No letter where the surface and the reading show different marks.

    CAMeL leaves some marks out (a fatha before a long alif, often the final case
    vowel), so a letter the reading leaves unmarked is not a conflict."""
    s, d = _letters_and_marks(surface), _letters_and_marks(diac)
    if [c for c, _ in s] != [c for c, _ in d]:
        return False
    # Sukun counts too: اقْتَرَبَتْ is "she", not اقْتَرَبْتَ "you"; بَيْت is not بَيَّت.
    return all(not md or ms <= md for (_, ms), (_, md) in zip(s, d))


def _skeleton(text: str | None) -> str:
    """Letters plus shadda, so the lemma بَيْت and the verb بَيَّت stay different."""
    kept = "".join(ch for ch in (text or "").replace("\u0671", "ا") if ch == _SHADDA or ch not in _ALL_MARKS)
    return normalize_alef(kept).replace("\u0640", "")


def _same_vowels(a: str, b: str) -> bool:
    """Same letters and, ignoring sukun and the last letter's case vowel, the same marks."""
    x, y = _letters_and_marks(a), _letters_and_marks(b)
    if len(x) != len(y):
        return False
    return all((mx - {_SUKUN}) == (my - {_SUKUN}) or not mx or not my for (_, mx), (_, my) in zip(x[:-1], y[:-1]))


def _bare(text: str | None) -> str:
    return normalize_alef(strip_diacritics(text or "")).replace("ٱ", "ا")



def _subject(a: dict) -> str | None:
    per, gen, num = a.get("per"), a.get("gen"), a.get("num")
    return _SUBJECT.get((per, gen, num)) or _SUBJECT.get((per, num))


def _slots(a: dict, lemma_bare: str) -> dict[str, str]:
    # Attached words (وَ، فَ، بِ، الـ، ـها، ـهُ) are left out: the learner reads them already.
    slots: dict[str, str] = {}
    if a.get("pos") == "verb":
        if a.get("prc1") == "sa_fut" and a.get("asp") == "i":
            slots["aspect"] = "future"
        elif a.get("asp") in _ASPECT:
            slots["aspect"] = _ASPECT[a["asp"]]
        if a.get("vox") == "p":
            slots["voice"] = "passive"
        subject = _subject(a)
        if subject:
            slots["subject"] = subject
    else:
        if a.get("num") == "p":
            slots["number"] = "plural"
        elif a.get("num") == "d":
            slots["number"] = "dual"
        if a.get("gen") == "f" and not lemma_bare.endswith(("ة", "ى", "اء")):
            slots["gender"] = "feminine"
    return slots


_ORDER = ("aspect", "voice", "subject", "gender", "number")
# The dictionary form of a verb says nothing new.
_CITATION = ["past", "he"]


def describe_surface_form(surface: str, lemma_ar: str, lemma_bare: str | None = None,
                          analyses: list[dict] | None = None) -> str | None:
    """The form line for this surface of this lemma, or None."""
    if not surface or not lemma_ar:
        return None
    target = _bare(lemma_bare or lemma_ar)
    same_letters = _bare(surface) == target
    if same_letters and _same_vowels(surface, lemma_ar):
        return None  # the dictionary form itself
    if analyses is None:
        from app.services.morphology import analyze_word_camel
        analyses = analyze_word_camel(surface.strip())
    vocalized = any(ch in _ALL_MARKS for ch in lemma_ar)
    readings = [a for a in analyses
                if a.get("pos") not in ("noun_prop", None)
                and _bare(a.get("lex")) == target
                and (not vocalized or _skeleton(a.get("lex")) == _skeleton(lemma_ar))
                and _fits(surface, a.get("diac") or "")]
    if not readings:
        return None
    per_reading = [_slots(a, target) for a in readings]
    parts: list[str] = []
    for key in _ORDER:
        values = list(dict.fromkeys(s.get(key) for s in per_reading))
        if len(values) == 1 and values[0]:
            parts.append(values[0])
        elif key == "subject" and len(values) == 2 and all(values):
            parts.append(f"{values[0]} or {values[1]}")
    if not parts or parts == _CITATION:
        return None
    if same_letters and any(" or " in p for p in parts):
        return None  # headword letters, other vowels: describe only an unambiguous reading (كُتِبَ)
    # A verb whose readings disagree on the tense is too uncertain to describe.
    if all(a.get("pos") == "verb" for a in readings) and not any(p in parts for p in ("past", "present", "command", "future")):
        return None
    return " · ".join(parts)
