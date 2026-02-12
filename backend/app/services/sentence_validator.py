"""Deterministic sentence validator.

Tokenizes Arabic text and classifies each word as known, unknown,
function_word, or target_word by matching bare (undiacritized) forms
against the user's known vocabulary.

MVP approach: simple whitespace tokenization + diacritic stripping +
string matching. Will be replaced by CAMeL Tools lemmatization later.
"""

import re
import unicodedata
from dataclasses import dataclass, field

ARABIC_DIACRITICS = re.compile(
    "[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC"
    "\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]"
)

ARABIC_PUNCTUATION = re.compile(
    r"[،؟؛«»\u060C\u061B\u061F.,:;!?\"'\-\(\)\[\]{}…]"
)

# Common Arabic function words (particles, pronouns, demonstratives,
# prepositions, conjunctions, negation, question words, auxiliary verbs).
# Stored as bare (undiacritized) forms.
FUNCTION_WORDS: set[str] = {
    # Prepositions
    "في", "من", "على", "الى", "إلى", "عن", "مع", "بين", "حتى",
    "منذ", "خلال", "عند", "نحو", "فوق", "تحت", "امام", "أمام",
    "وراء", "بعد", "قبل", "حول", "دون",
    # Single-letter prepositions/conjunctions (often attached but can appear alone)
    "ب", "ل", "ك", "و", "ف",
    # Conjunctions
    "او", "أو", "ان", "أن", "إن", "لكن", "ثم", "بل",
    # Definite article (standalone, rare but possible after tokenization)
    "ال",
    # Pronouns
    "انا", "أنا", "انت", "أنت", "انتم", "أنتم", "هو", "هي",
    "هم", "هن", "نحن", "انتما", "هما",
    # Demonstratives
    "هذا", "هذه", "ذلك", "تلك", "هؤلاء", "اولئك", "أولئك",
    # Relative pronouns
    "الذي", "التي", "الذين", "اللذان", "اللتان", "اللواتي",
    # Question words
    "ما", "ماذا", "من", "لماذا", "كيف", "اين", "أين", "متى",
    "هل", "كم", "اي", "أي",
    # Negation
    "لا", "لم", "لن", "ما", "ليس", "ليست",
    # Auxiliary / modal
    "كان", "كانت", "يكون", "تكون", "قد", "سوف", "سـ",
    # Very common adverbs/particles
    "ايضا", "أيضا", "جدا", "فقط", "كل", "بعض", "كلما",
    "هنا", "هناك", "الان", "الآن", "لذلك", "هكذا", "معا",
    # Conditional/temporal conjunctions
    "اذا", "إذا", "لو", "عندما", "بينما", "حيث", "كما",
    "لان", "لأن", "كي", "لكي", "حين", "حينما",
    # Emphasis / structure particles
    "لقد", "اما", "أما", "الا", "إلا", "اذن", "إذن",
    "انه", "إنه", "انها", "إنها", "مثل", "غير",
    # Common verbs that are essentially grammatical
    "يوجد", "توجد",
}

# Glosses for function words so they're tappable in review even without a lemma entry
FUNCTION_WORD_GLOSSES: dict[str, str] = {
    # Prepositions
    "في": "in", "من": "from", "على": "on/upon", "الى": "to", "إلى": "to",
    "عن": "about/from", "مع": "with", "بين": "between", "حتى": "until/even",
    "منذ": "since", "خلال": "during", "عند": "at/with", "نحو": "toward",
    "فوق": "above", "تحت": "under", "امام": "in front of", "أمام": "in front of",
    "وراء": "behind", "بعد": "after", "قبل": "before", "حول": "around", "دون": "without",
    # Single-letter
    "ب": "with/by", "ل": "for/to", "ك": "like/as", "و": "and", "ف": "so/then",
    # Conjunctions
    "او": "or", "أو": "or", "ان": "that", "أن": "that", "إن": "indeed",
    "لكن": "but", "ثم": "then", "بل": "rather",
    # Pronouns
    "انا": "I", "أنا": "I", "انت": "you (m)", "أنت": "you (m)",
    "انتم": "you (pl)", "أنتم": "you (pl)", "هو": "he", "هي": "she",
    "هم": "they (m)", "هن": "they (f)", "نحن": "we", "انتما": "you (dual)", "هما": "they (dual)",
    # Demonstratives
    "هذا": "this (m)", "هذه": "this (f)", "ذلك": "that (m)", "تلك": "that (f)",
    "هؤلاء": "these", "اولئك": "those", "أولئك": "those",
    # Relative pronouns
    "الذي": "who/which (m)", "التي": "who/which (f)", "الذين": "who/which (pl)",
    "اللذان": "who/which (dual m)", "اللتان": "who/which (dual f)", "اللواتي": "who/which (f pl)",
    # Question words
    "ما": "what", "ماذا": "what", "لماذا": "why", "كيف": "how",
    "اين": "where", "أين": "where", "متى": "when", "هل": "? (yes/no)",
    "كم": "how many", "اي": "which", "أي": "which",
    # Negation
    "لا": "no/not", "لم": "did not", "لن": "will not", "ليس": "is not", "ليست": "is not (f)",
    # Auxiliary / modal
    "كان": "was/were", "كانت": "was (f)", "يكون": "to be", "تكون": "to be (f)",
    "قد": "may/already", "سوف": "will", "سـ": "will",
    # Adverbs/particles
    "ايضا": "also", "أيضا": "also", "جدا": "very", "فقط": "only",
    "كل": "every/all", "بعض": "some", "كلما": "whenever",
    "هنا": "here", "هناك": "there", "الان": "now", "الآن": "now",
    "لذلك": "therefore", "هكذا": "thus", "معا": "together",
    # Conditional/temporal
    "اذا": "if", "إذا": "if", "لو": "if (hypothetical)", "عندما": "when",
    "بينما": "while", "حيث": "where", "كما": "as/like",
    "لان": "because", "لأن": "because", "كي": "in order to", "لكي": "in order to",
    "حين": "when", "حينما": "when",
    # Emphasis / structure
    "لقد": "indeed (past)", "اما": "as for", "أما": "as for",
    "الا": "except", "إلا": "except", "اذن": "then/so", "إذن": "then/so",
    "انه": "indeed he", "إنه": "indeed he", "انها": "indeed she", "إنها": "indeed she",
    "مثل": "like", "غير": "other than",
    # Grammatical verbs
    "يوجد": "there is", "توجد": "there is (f)",
}


def strip_diacritics(text: str) -> str:
    """Remove Arabic diacritical marks (tashkeel) from text."""
    return ARABIC_DIACRITICS.sub("", text)


def strip_tatweel(text: str) -> str:
    """Remove tatweel (kashida) character."""
    return text.replace("\u0640", "")


def normalize_alef(text: str) -> str:
    """Normalize alef variants to bare alef."""
    text = text.replace("أ", "ا")
    text = text.replace("إ", "ا")
    text = text.replace("آ", "ا")
    text = text.replace("ٱ", "ا")
    return text


def normalize_arabic(text: str) -> str:
    """Full normalization: strip diacritics, tatweel, normalize alef."""
    text = strip_diacritics(text)
    text = strip_tatweel(text)
    text = normalize_alef(text)
    return text


# Punctuation pattern for stripping from word boundaries (leading/trailing).
_WORD_BOUNDARY_PUNCT = re.compile(
    r"^[،؟؛«»\u060C\u061B\u061F.,:;!?\"'\-\(\)\[\]{}…/\s]+"
    r"|[،؟؛«»\u060C\u061B\u061F.,:;!?\"'\-\(\)\[\]{}…/\s]+$"
)


def sanitize_arabic_word(text: str) -> tuple[str, list[str]]:
    """Strip punctuation from an Arabic word. Returns (cleaned, warnings).

    Handles: trailing/leading punctuation, slash-separated alternatives
    (takes first), multi-word phrases (takes first word, warns).
    Does NOT strip diacritics — that's strip_diacritics()'s job.
    """
    warnings: list[str] = []

    if not text or not text.strip():
        return "", ["empty"]

    cleaned = _WORD_BOUNDARY_PUNCT.sub("", text)

    if not cleaned:
        return "", ["empty_after_clean"]

    # Handle slash-separated alternatives: take the first
    if "/" in cleaned:
        parts = [p.strip() for p in cleaned.split("/") if p.strip()]
        if len(parts) >= 2:
            warnings.append("slash_split")
            cleaned = parts[0]
            # Re-strip punctuation from the chosen part
            cleaned = _WORD_BOUNDARY_PUNCT.sub("", cleaned)

    # After cleanup, check for multi-word (spaces)
    if " " in cleaned.strip():
        warnings.append("multi_word")
        words = cleaned.strip().split()
        cleaned = words[0]
        cleaned = _WORD_BOUNDARY_PUNCT.sub("", cleaned)

    cleaned = cleaned.strip()

    if not cleaned:
        return "", ["empty_after_clean"]

    # Reject single-character bare forms — typically abbreviations
    # (ج for plural, ص for page, م for year, etc.) not real vocabulary
    bare = normalize_arabic(cleaned)
    if len(bare) < 2:
        warnings.append("too_short")
        return cleaned, warnings

    return cleaned, warnings


def compute_bare_form(lemma_ar: str) -> str:
    """Compute the bare (undiacritized, normalized) form for a lemma."""
    return normalize_arabic(lemma_ar)


# Pre-computed normalized set for fast lookup (must be after normalize_alef def)
_FUNCTION_WORDS_NORMALIZED: set[str] = {normalize_alef(fw) for fw in FUNCTION_WORDS}

# Conjugated function word forms → base lemma bare form.
# Prevents false clitic analysis (e.g. كانت → ك+انت) by providing
# a direct match path before clitic stripping is attempted.
FUNCTION_WORD_FORMS: dict[str, str] = {
    # كان conjugations
    "كانت": "كان", "كانوا": "كان", "كنت": "كان", "كنا": "كان",
    "يكون": "كان", "تكون": "كان", "يكونون": "كان", "نكون": "كان",
    "اكون": "كان", "كانا": "كان", "كنتم": "كان",
    # ليس conjugations
    "ليست": "ليس", "ليسوا": "ليس", "لست": "ليس", "لسنا": "ليس",
    "ليسا": "ليس",
    # يوجد/توجد
    "توجد": "يوجد", "وجد": "يوجد",
    # كان passive
    "يكن": "كان",
}


def tokenize(text: str) -> list[str]:
    """Tokenize Arabic text into words.

    Simple whitespace split with punctuation stripping.
    Returns non-empty tokens only.
    """
    text = ARABIC_PUNCTUATION.sub(" ", text)
    tokens = text.split()
    return [t.strip() for t in tokens if t.strip()]


@dataclass
class WordClassification:
    original: str
    bare: str
    category: str  # "known", "unknown", "function_word", "target_word"


@dataclass
class ValidationResult:
    valid: bool
    target_found: bool
    unknown_words: list[str] = field(default_factory=list)
    known_words: list[str] = field(default_factory=list)
    function_words: list[str] = field(default_factory=list)
    classifications: list[WordClassification] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


PROCLITICS = ["وال", "بال", "فال", "لل", "كال", "و", "ف", "ب", "ل", "ك"]

ENCLITICS = ["هما", "هم", "هن", "ها", "كم", "كن", "نا", "ني", "ه", "ك"]


def _strip_clitics(bare_form: str) -> list[str]:
    """Return all possible stems after removing Arabic proclitics/enclitics.

    Tries prefix-only, suffix-only, and prefix+suffix combinations.
    Handles taa marbuta: ة→ت before suffixes (e.g. مدرسته → مدرسة + ه).
    Also tries ال removal on the remaining stem.
    """
    candidates: set[str] = set()

    def _add_with_al_variants(stem: str) -> None:
        if len(stem) < 2:
            return
        candidates.add(stem)
        if stem.startswith("ال") and len(stem) > 2:
            candidates.add(stem[2:])
        else:
            candidates.add("ال" + stem)

    def _strip_suffix(stem: str) -> list[str]:
        results = [stem]
        for suf in ENCLITICS:
            if stem.endswith(suf) and len(stem) > len(suf):
                base = stem[: -len(suf)]
                results.append(base)
                # taa marbuta restoration: final ت → ة
                if base.endswith("ت"):
                    results.append(base[:-1] + "ة")
        return results

    # 1. Suffix-only stripping
    for stem in _strip_suffix(bare_form):
        _add_with_al_variants(stem)

    # 2. Prefix stripping (then optional suffix stripping)
    for pre in PROCLITICS:
        if bare_form.startswith(pre) and len(bare_form) > len(pre):
            after_pre = bare_form[len(pre):]
            for stem in _strip_suffix(after_pre):
                _add_with_al_variants(stem)

    candidates.discard(bare_form)
    return list(candidates)


def _is_function_word(bare_form: str) -> bool:
    """Check if a bare form is a known function word.

    Strips diacritics first so diacritized input (e.g. كَانَتْ) is handled.
    Also checks FUNCTION_WORD_FORMS for conjugated forms.
    """
    stripped = strip_diacritics(bare_form)
    normalized = normalize_alef(stripped)
    if normalized in _FUNCTION_WORDS_NORMALIZED:
        return True
    if normalized in FUNCTION_WORD_FORMS:
        return True
    return False


def _bare_forms_match(word_bare: str, candidate_bare: str) -> bool:
    """Check if two bare Arabic forms match, with alef normalization."""
    return normalize_alef(word_bare) == normalize_alef(candidate_bare)


@dataclass
class TokenMapping:
    position: int
    surface_form: str
    lemma_id: int | None
    is_target: bool
    is_function_word: bool


def map_tokens_to_lemmas(
    tokens: list[str],
    lemma_lookup: dict[str, int],
    target_lemma_id: int,
    target_bare: str,
) -> list[TokenMapping]:
    """Map tokenized sentence words to lemma IDs.

    Args:
        tokens: Tokenized Arabic words (from tokenize()).
        lemma_lookup: Dict of {normalized_bare_form: lemma_id} including
                      al-prefix variants.
        target_lemma_id: The lemma_id of the target word.
        target_bare: Bare form of the target word.

    Returns:
        List of TokenMapping with position, surface_form, lemma_id, flags.
    """
    target_normalized = normalize_alef(target_bare)
    target_forms = {target_normalized}
    if not target_normalized.startswith("ال"):
        target_forms.add("ال" + target_normalized)
    if target_normalized.startswith("ال") and len(target_normalized) > 2:
        target_forms.add(target_normalized[2:])

    result: list[TokenMapping] = []
    for i, token in enumerate(tokens):
        bare = strip_diacritics(token)
        bare_clean = strip_tatweel(bare)
        bare_norm = normalize_alef(bare_clean)

        # Check target
        is_target = bare_norm in target_forms
        if not is_target:
            for stem in _strip_clitics(bare_norm):
                if normalize_alef(stem) in target_forms:
                    is_target = True
                    break

        if is_target:
            result.append(TokenMapping(i, token, target_lemma_id, True, False))
            continue

        is_function = _is_function_word(bare_clean)
        if is_function:
            # Direct-only lookup for function words — no clitic stripping.
            # This prevents false analysis like كانت → ك+انت → أنت.
            lemma_id = lookup_lemma_direct(bare_norm, lemma_lookup)
        else:
            lemma_id = lookup_lemma(bare_norm, lemma_lookup)
        result.append(TokenMapping(i, token, lemma_id, False, is_function))

    return result


def lookup_lemma_direct(bare_norm: str, lemma_lookup: dict[str, int]) -> int | None:
    """Find a lemma_id using direct match and al-prefix only — no clitic stripping."""
    if bare_norm in lemma_lookup:
        return lemma_lookup[bare_norm]
    if bare_norm.startswith("ال") and len(bare_norm) > 2:
        without_al = bare_norm[2:]
        if without_al in lemma_lookup:
            return lemma_lookup[without_al]
    else:
        with_al = "ال" + bare_norm
        if with_al in lemma_lookup:
            return lemma_lookup[with_al]
    return None


def lookup_lemma(bare_norm: str, lemma_lookup: dict[str, int]) -> int | None:
    """Find a lemma_id for a normalized bare form, trying variants and clitic stripping."""
    # Direct match
    if bare_norm in lemma_lookup:
        return lemma_lookup[bare_norm]

    # With/without al-prefix
    if bare_norm.startswith("ال") and len(bare_norm) > 2:
        without_al = bare_norm[2:]
        if without_al in lemma_lookup:
            return lemma_lookup[without_al]
    else:
        with_al = "ال" + bare_norm
        if with_al in lemma_lookup:
            return lemma_lookup[with_al]

    # Clitic stripping
    for stem in _strip_clitics(bare_norm):
        norm_stem = normalize_alef(stem)
        if norm_stem in lemma_lookup:
            return lemma_lookup[norm_stem]

    return None


def lookup_lemma_id(surface_form: str, lemma_lookup: dict[str, int]) -> int | None:
    """Resolve a sentence token surface form to a lemma_id using lookup variants."""
    bare = strip_diacritics(surface_form)
    bare_clean = strip_tatweel(bare)
    bare_norm = normalize_alef(bare_clean)
    return lookup_lemma(bare_norm, lemma_lookup)


def build_lemma_lookup(lemmas: list) -> dict[str, int]:
    """Build a normalized bare form → lemma_id lookup dict.

    Includes both with and without al-prefix for each lemma,
    plus inflected forms from forms_json (plurals, feminines, verb
    conjugations, etc.), plus FUNCTION_WORD_FORMS conjugation mappings.

    Args:
        lemmas: List of Lemma model objects with lemma_ar_bare and lemma_id.
    """
    lookup: dict[str, int] = {}
    # Index for mapping function word form bases to lemma_ids
    bare_to_id: dict[str, int] = {}

    for lem in lemmas:
        bare_norm = normalize_alef(lem.lemma_ar_bare)
        lookup[bare_norm] = lem.lemma_id
        bare_to_id[bare_norm] = lem.lemma_id
        if bare_norm.startswith("ال") and len(bare_norm) > 2:
            lookup[bare_norm[2:]] = lem.lemma_id
            bare_to_id[bare_norm[2:]] = lem.lemma_id
        elif not bare_norm.startswith("ال"):
            lookup["ال" + bare_norm] = lem.lemma_id

        forms = getattr(lem, "forms_json", None)
        if forms and isinstance(forms, dict):
            for key in ("plural", "present", "masdar", "active_participle",
                        "feminine", "elative"):
                form_val = forms.get(key)
                if form_val and isinstance(form_val, str):
                    form_bare = normalize_alef(strip_diacritics(form_val))
                    if form_bare not in lookup:
                        lookup[form_bare] = lem.lemma_id
                    al_form = "ال" + form_bare
                    if not form_bare.startswith("ال") and al_form not in lookup:
                        lookup[al_form] = lem.lemma_id

    # Add FUNCTION_WORD_FORMS: map conjugated forms to their base lemma_id
    for form, base in FUNCTION_WORD_FORMS.items():
        form_norm = normalize_alef(form)
        if form_norm not in lookup:
            base_norm = normalize_alef(base)
            base_id = bare_to_id.get(base_norm)
            if base_id is not None:
                lookup[form_norm] = base_id

    return lookup


def resolve_existing_lemma(
    bare: str, lemma_lookup: dict[str, int]
) -> int | None:
    """Check if a bare form matches an existing lemma via clitic-aware lookup.

    Used by import scripts to avoid creating duplicate lemmas for clitic forms
    (وكتاب, كتابي, بالكتاب) or al-prefixed forms (الكتاب).

    Returns the matched lemma_id, or None if no match found.
    """
    bare_norm = normalize_alef(bare)
    return lookup_lemma(bare_norm, lemma_lookup)


def validate_sentence(
    arabic_text: str,
    target_bare: str,
    known_bare_forms: set[str],
) -> ValidationResult:
    """Validate that a sentence uses known words + exactly 1 target word.

    Args:
        arabic_text: The Arabic sentence (may include diacritics).
        target_bare: The bare (undiacritized) form of the target word.
        known_bare_forms: Set of bare forms the user knows.

    Returns:
        ValidationResult with word classifications and validity.
    """
    tokens = tokenize(arabic_text)
    if not tokens:
        return ValidationResult(
            valid=False,
            target_found=False,
            issues=["Empty sentence"],
        )

    # Normalize the known set for comparison
    known_normalized = {normalize_alef(w) for w in known_bare_forms}
    target_normalized = normalize_alef(target_bare)

    classifications: list[WordClassification] = []
    unknown_words: list[str] = []
    known_words: list[str] = []
    function_words: list[str] = []
    target_found = False

    for token in tokens:
        bare = strip_diacritics(token)
        bare_clean = strip_tatweel(bare)
        bare_normalized = normalize_alef(bare_clean)

        # Check: is it the target word? (with ال prefix handling)
        target_forms = [target_normalized]
        if not target_normalized.startswith("ال"):
            target_forms.append("ال" + target_normalized)
        if target_normalized.startswith("ال") and len(target_normalized) > 2:
            target_forms.append(target_normalized[2:])

        is_target = bare_normalized in target_forms
        if not is_target:
            for stem in _strip_clitics(bare_normalized):
                if normalize_alef(stem) in target_forms:
                    is_target = True
                    break

        if is_target:
            classifications.append(
                WordClassification(token, bare_clean, "target_word")
            )
            target_found = True
            continue

        # Check: function word?
        if _is_function_word(bare_clean):
            classifications.append(
                WordClassification(token, bare_clean, "function_word")
            )
            function_words.append(token)
            continue

        # Check: known word? Try the bare form and with/without ال prefix.
        is_known = False
        forms_to_check = [bare_normalized]
        # If word starts with ال, also check without it
        if bare_normalized.startswith("ال") and len(bare_normalized) > 2:
            forms_to_check.append(bare_normalized[2:])
        # If word doesn't start with ال, also check with it
        if not bare_normalized.startswith("ال"):
            forms_to_check.append("ال" + bare_normalized)

        for form in forms_to_check:
            if form in known_normalized:
                is_known = True
                break

        # Try clitic stripping if direct match failed
        if not is_known:
            for stem in _strip_clitics(bare_normalized):
                if normalize_alef(stem) in known_normalized:
                    is_known = True
                    break

        if is_known:
            classifications.append(
                WordClassification(token, bare_clean, "known")
            )
            known_words.append(token)
        else:
            classifications.append(
                WordClassification(token, bare_clean, "unknown")
            )
            unknown_words.append(token)

    # Build issues
    issues: list[str] = []
    if not target_found:
        issues.append(f"Target word '{target_bare}' not found in sentence")
    if unknown_words:
        issues.append(
            f"Unknown words (besides target): {', '.join(unknown_words)}"
        )

    valid = target_found and len(unknown_words) == 0

    return ValidationResult(
        valid=valid,
        target_found=target_found,
        unknown_words=unknown_words,
        known_words=known_words,
        function_words=function_words,
        classifications=classifications,
        issues=issues,
    )
