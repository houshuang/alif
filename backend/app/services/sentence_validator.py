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
    "هنا", "هناك", "الان", "الآن", "لذلك",
    # Common verbs that are essentially grammatical
    "يوجد", "توجد",
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
    """Check if a bare form is a known function word."""
    normalized = normalize_alef(bare_form)
    if normalized in FUNCTION_WORDS:
        return True
    # Also check with alef normalization applied to the function words set
    for fw in FUNCTION_WORDS:
        if normalize_alef(fw) == normalized:
            return True
    return False


def _bare_forms_match(word_bare: str, candidate_bare: str) -> bool:
    """Check if two bare Arabic forms match, with alef normalization."""
    return normalize_alef(word_bare) == normalize_alef(candidate_bare)


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
