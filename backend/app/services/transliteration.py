"""Deterministic Arabic → ALA-LC romanization from diacritized text.

Handles consonant mapping, short/long vowels, shadda, tāʾ marbūṭa,
al- prefix, hamza forms, alif madda, alif wasla, nisba ending, and
dagger alef. Works best on fully diacritized input.

Reference: ALA-LC Romanization Tables for Arabic
Cross-checked against MTG/ArabicTransliterator and CAMeL-Lab/Arabic_ALA-LC_Romanization.
"""

# Unicode constants
FATHA = "\u064E"       # َ
DAMMA = "\u064F"       # ُ
KASRA = "\u0650"       # ِ
SHADDA = "\u0651"      # ّ
SUKUN = "\u0652"       # ْ
FATHATAN = "\u064B"    # ً
DAMMATAN = "\u064C"    # ٌ
KASRATAN = "\u064D"    # ٍ
SUPERSCRIPT_ALEF = "\u0670"  # ٰ (dagger alef)
TATWEEL = "\u0640"     # ـ

ALIF = "\u0627"            # ا
ALIF_MADDA = "\u0622"      # آ
ALIF_HAMZA_ABOVE = "\u0623" # أ
ALIF_HAMZA_BELOW = "\u0625" # إ
ALIF_WASLA = "\u0671"      # ٱ
WAW = "\u0648"             # و
YA = "\u064A"              # ي
ALIF_MAQSURA = "\u0649"   # ى
TA_MARBUTA = "\u0629"     # ة
HAMZA = "\u0621"           # ء
HAMZA_WAW = "\u0624"       # ؤ
HAMZA_YA = "\u0626"        # ئ
LAM = "\u0644"             # ل

# Consonant map (base letters → ALA-LC)
_CONSONANTS = {
    HAMZA: "ʾ",            # ء
    ALIF_HAMZA_ABOVE: "ʾ", # أ
    ALIF_HAMZA_BELOW: "ʾ", # إ
    HAMZA_WAW: "ʾ",        # ؤ
    HAMZA_YA: "ʾ",         # ئ
    ALIF: "",               # ا (long vowel marker)
    ALIF_WASLA: "",         # ٱ (always silent)
    ALIF_MADDA: "",         # آ (handled separately)
    "\u0628": "b",          # ب
    TA_MARBUTA: "",         # ة (handled separately)
    "\u062A": "t",          # ت
    "\u062B": "th",         # ث
    "\u062C": "j",          # ج
    "\u062D": "ḥ",          # ح
    "\u062E": "kh",         # خ
    "\u062F": "d",          # د
    "\u0630": "dh",         # ذ
    "\u0631": "r",          # ر
    "\u0632": "z",          # ز
    "\u0633": "s",          # س
    "\u0634": "sh",         # ش
    "\u0635": "ṣ",          # ص
    "\u0636": "ḍ",          # ض
    "\u0637": "ṭ",          # ط
    "\u0638": "ẓ",          # ظ
    "\u0639": "ʿ",          # ع
    "\u063A": "gh",         # غ
    "\u0641": "f",          # ف
    "\u0642": "q",          # ق
    "\u0643": "k",          # ك
    LAM: "l",               # ل
    "\u0645": "m",          # م
    "\u0646": "n",          # ن
    "\u0647": "h",          # ه
    WAW: "w",               # و
    YA: "y",                # ي
    ALIF_MAQSURA: "ā",     # ى
}

_SHORT_VOWELS = {FATHA: "a", DAMMA: "u", KASRA: "i"}
_TANWIN = {FATHATAN: "an", DAMMATAN: "un", KASRATAN: "in"}
_DIACRITICS = set(_SHORT_VOWELS) | set(_TANWIN) | {SHADDA, SUKUN, SUPERSCRIPT_ALEF}

# Hamza carriers that are silent at word start
_HAMZA_CARRIERS = {ALIF, ALIF_HAMZA_ABOVE, ALIF_HAMZA_BELOW, ALIF_WASLA}


def transliterate_arabic(text: str, strip_tanwin: bool = True) -> str:
    """Convert diacritized Arabic to ALA-LC romanization.

    Args:
        text: Arabic text (ideally fully diacritized).
        strip_tanwin: If True, omit tanwīn endings (pausal/dictionary form).
    """
    if not text:
        return ""

    text = text.replace(TATWEEL, "")
    result: list[str] = []
    words = text.split()

    for wi, word in enumerate(words):
        if wi > 0:
            result.append(" ")

        # Handle al- prefix: ال or اَل or اْل etc.
        al_prefix = ""
        core = word
        if _detect_al_prefix(word):
            al_prefix = "al-"
            core = _strip_al_prefix(word)
            # Strip sun-letter assimilation shadda from first consonant
            core = _strip_leading_shadda(core)

        result.append(al_prefix)
        result.append(_transliterate_word(core, strip_tanwin))

    return "".join(result)


def _detect_al_prefix(word: str) -> bool:
    """Detect if word starts with definite article ال."""
    chars = list(word)
    n = len(chars)
    if n < 3:
        return False
    # Pattern: alif (possibly with diacritics) + lam (possibly with diacritics) + more
    i = 0
    if chars[i] not in (ALIF, ALIF_HAMZA_ABOVE, ALIF_WASLA):
        return False
    i += 1
    # Skip diacritics on alif
    while i < n and chars[i] in _DIACRITICS:
        i += 1
    if i >= n or chars[i] != LAM:
        return False
    # Make sure there's something after the lam + its diacritics
    j = i + 1
    while j < n and chars[j] in _DIACRITICS:
        j += 1
    return j < n


def _strip_al_prefix(word: str) -> str:
    """Remove the al- prefix, returning the remainder."""
    chars = list(word)
    n = len(chars)
    i = 1  # skip alif
    # Skip diacritics on alif
    while i < n and chars[i] in _DIACRITICS:
        i += 1
    # Skip lam
    i += 1
    # Skip diacritics on lam
    while i < n and chars[i] in _DIACRITICS:
        i += 1
    return "".join(chars[i:])


def _strip_leading_shadda(word: str) -> str:
    """Remove shadda from the first consonant (sun letter assimilation after al-)."""
    chars = list(word)
    n = len(chars)
    i = 0
    while i < n and chars[i] in _DIACRITICS:
        i += 1
    if i >= n:
        return word
    # i is on the consonant; remove shadda from trailing diacritics
    j = i + 1
    new_chars = chars[:j]
    while j < n and chars[j] in _DIACRITICS:
        if chars[j] != SHADDA:
            new_chars.append(chars[j])
        j += 1
    new_chars.extend(chars[j:])
    return "".join(new_chars)


def _collect_diacritics(chars: list[str], start: int) -> tuple[list[str], int]:
    """Collect diacritics after position start, return (diacritics, next_pos)."""
    diacritics = []
    j = start
    n = len(chars)
    while j < n and chars[j] in _DIACRITICS:
        diacritics.append(chars[j])
        j += 1
    return diacritics, j


def _next_has_shadda(chars: list[str], j: int) -> bool:
    """Check if the character at position j has shadda in its diacritics."""
    n = len(chars)
    k = j + 1
    while k < n and chars[k] in _DIACRITICS:
        if chars[k] == SHADDA:
            return True
        k += 1
    return False


def _transliterate_word(word: str, strip_tanwin: bool) -> str:
    chars = list(word)
    n = len(chars)
    out: list[str] = []
    i = 0

    while i < n:
        ch = chars[i]

        # Skip tatweel
        if ch == TATWEEL:
            i += 1
            continue

        # Orphan diacritics
        if ch in _DIACRITICS:
            i += 1
            continue

        # Superscript alef → ā
        if ch == SUPERSCRIPT_ALEF:
            out.append("ā")
            i += 1
            continue

        # Alif madda (آ): initial = ā, medial = ʾā
        if ch == ALIF_MADDA:
            diacritics, j = _collect_diacritics(chars, i + 1)
            if i == 0:
                out.append("ā")
            else:
                out.append("ʾā")
            i = j
            continue

        # Alif wasla (ٱ): always silent
        if ch == ALIF_WASLA:
            diacritics, j = _collect_diacritics(chars, i + 1)
            # Emit the vowel if present
            for d in diacritics:
                if d in _SHORT_VOWELS:
                    out.append(_SHORT_VOWELS[d])
                    break
            i = j
            continue

        # Tāʾ marbūṭa
        if ch == TA_MARBUTA:
            diacritics, j = _collect_diacritics(chars, i + 1)
            has_tanwin = any(d in _TANWIN for d in diacritics)
            if has_tanwin and not strip_tanwin:
                tanwin_val = next(_TANWIN[d] for d in diacritics if d in _TANWIN)
                out.append("t" + tanwin_val)
            else:
                # Emit "a" unless previous output already ends with "a"
                if not (out and out[-1].endswith("a")):
                    out.append("a")
            i = j
            continue

        # Alif maqsura at end — treat as ā (already mapped in _CONSONANTS)
        # but handle the case where fatḥa precedes it (avoid double "a")
        if ch == ALIF_MAQSURA:
            diacritics, j = _collect_diacritics(chars, i + 1)
            # Just emit ā; if preceded by fatḥa, the long-a detection
            # in the consonant handler already consumed it. If standalone, emit ā.
            if not (out and out[-1].endswith("ā")):
                out.append("ā")
            i = j
            continue

        consonant = _CONSONANTS.get(ch)
        if consonant is not None:
            diacritics, j = _collect_diacritics(chars, i + 1)

            has_shadda = SHADDA in diacritics
            has_sukun = SUKUN in diacritics
            has_dagger_alef = SUPERSCRIPT_ALEF in diacritics
            short_vowel = next((d for d in diacritics if d in _SHORT_VOWELS), None)
            tanwin = next((d for d in diacritics if d in _TANWIN), None)

            next_char = chars[j] if j < n else None
            next_shadda = _next_has_shadda(chars, j) if next_char else False

            # Long vowel detection
            # Alif or alif maqsura after fatḥa → ā
            is_long_a = (
                short_vowel == FATHA
                and next_char in (ALIF, ALIF_MAQSURA)
                and ch != ALIF
            )
            # Waw after ḍamma → ū (but not if waw has shadda = geminate)
            is_long_u = (
                short_vowel == DAMMA
                and next_char == WAW
                and not next_shadda
            )
            # Ya after kasra → ī (but not if ya has shadda = geminate/nisba)
            is_long_i = (
                short_vowel == KASRA
                and next_char == YA
                and not next_shadda
            )

            # Tanwin fatha + alif: alif is silent (orthographic only)
            tanwin_alif = (
                tanwin == FATHATAN
                and next_char == ALIF
            )

            # --- Word-initial hamza carriers are silent ---
            if ch in _HAMZA_CARRIERS and i == 0:
                if has_dagger_alef:
                    out.append("ā")
                elif is_long_a:
                    out.append("ā")
                    i = j + 1
                    continue
                elif short_vowel:
                    out.append(_SHORT_VOWELS[short_vowel])
                elif tanwin and not strip_tanwin:
                    out.append(_TANWIN[tanwin])
                if tanwin_alif:
                    i = j + 1  # skip the silent alif after tanwin
                else:
                    i = j
                continue

            # --- Medial alif (long vowel marker, not consonant) ---
            if ch == ALIF and i > 0:
                # Alif after tanwin fatha is silent (handled by tanwin_alif in prev consonant)
                # Standalone medial alif with no diacritics = long ā (already handled by is_long_a)
                if short_vowel:
                    out.append(_SHORT_VOWELS[short_vowel])
                i = j
                continue

            # --- Nisba ending: consonant + kasra + ya + shadda at word end → ī ---
            is_nisba = False
            if short_vowel == KASRA and next_char == YA and next_shadda:
                ya_diacs, after_ya = _collect_diacritics(chars, j + 1)
                if SHADDA in ya_diacs and after_ya >= n:
                    is_nisba = True

            # --- Emit the consonant ---
            if has_shadda:
                out.append(consonant + consonant)
            else:
                out.append(consonant)

            # --- Emit vowel ---
            if is_nisba:
                # kasra + ya + shadda (word-final) → just ī
                out.append("ī")
                # Skip past ya + its diacritics
                _, after_ya = _collect_diacritics(chars, j + 1)
                i = after_ya
            elif has_dagger_alef:
                out.append("ā")
                i = j
            elif is_long_a:
                out.append("ā")
                i = j + 1  # skip the alif/maqsura
            elif is_long_u:
                out.append("ū")
                i = j + 1  # skip the waw
            elif is_long_i:
                out.append("ī")
                i = j + 1  # skip the ya
            elif tanwin:
                if not strip_tanwin:
                    out.append(_TANWIN[tanwin])
                if tanwin_alif:
                    i = j + 1  # skip the silent alif
                else:
                    i = j
            elif short_vowel:
                out.append(_SHORT_VOWELS[short_vowel])
                i = j
            elif has_sukun:
                i = j
            else:
                i = j
        else:
            # Non-Arabic character (punctuation, digits, etc.)
            out.append(ch)
            i += 1

    return "".join(out)


def transliterate_lemma(lemma_ar: str) -> str:
    """Transliterate a single lemma to ALA-LC, pausal/dictionary form.

    Strips tanwīn and final case vowels (iʿrāb).
    """
    result = transliterate_arabic(lemma_ar, strip_tanwin=True)
    # Strip final short case vowel (u/i) — but not 'a' which may be ta marbuta
    # Only strip if preceded by a consonant (not a vowel)
    if len(result) > 2 and result[-1] in ("u", "i") and result[-2] not in "aāuūiī":
        result = result[:-1]
    return result
