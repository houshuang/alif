"""Deterministic sentence validator.

Tokenizes Arabic text and classifies each word as known, unknown,
function_word, or target_word by matching bare (undiacritized) forms
against the user's known vocabulary.

MVP approach: simple whitespace tokenization + diacritic stripping +
string matching. Will be replaced by CAMeL Tools lemmatization later.
"""

import logging as _logging
import re
import unicodedata
from dataclasses import dataclass, field

_validator_logger = _logging.getLogger(__name__)

ARABIC_DIACRITICS = re.compile(
    "[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC"
    "\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]"
)

ARABIC_PUNCTUATION = re.compile(
    r"[،؟؛«»\u060C\u061B\u061F.,:;!?\"'\-\(\)\[\]{}…]"
)

# Function words are excluded from story/book "to learn" counts and from
# book page word introduction. They CAN still be learned through normal
# sentence review (they get FSRS scheduling when encountered in sentences),
# but they don't count as "new vocabulary" in book progress tracking.
# Populated from FUNCTION_WORD_GLOSSES below at module load time.
FUNCTION_WORDS: set[str] = set()

# Fallback glosses for common words that may lack lemma entries.
# Used during sentence validation to provide gloss_en even without a DB lemma.
# Also the source of truth for which words are considered function words.
FUNCTION_WORD_GLOSSES: dict[str, str] = {
    # Prepositions
    "في": "in", "من": "from", "على": "on/upon", "الى": "to", "إلى": "to",
    "عن": "about/from", "مع": "with", "بين": "between", "حتى": "until/even",
    "منذ": "since", "خلال": "during", "عند": "at/with", "نحو": "toward",
    "فوق": "above", "تحت": "under", "امام": "in front of", "أمام": "in front of",
    "وراء": "behind", "بعد": "after", "قبل": "before", "حول": "around", "دون": "without",
    # Single-letter clitics
    "ب": "in/by/with", "ل": "for/to", "ك": "like/as", "و": "and", "ف": "so/then",
    # Conjunctions
    "او": "or", "أو": "or", "ان": "that", "أن": "that", "إن": "indeed",
    "لكن": "but", "ثم": "then (after delay)", "بل": "rather/nay",
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
    "قد": "indeed/may/already", "سوف": "will", "سـ": "will",
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
    "لقد": "indeed/certainly (past)", "اما": "as for", "أما": "as for",
    "الا": "except", "إلا": "except", "اذن": "then/so", "إذن": "then/so",
    "لولا": "if not for", "لوما": "if not for",
    "انه": "indeed he", "إنه": "indeed he", "انها": "indeed she", "إنها": "indeed she",
    "مثل": "like", "غير": "other than",
    # Grammatical verbs
    "يوجد": "there is", "توجد": "there is (f)",
    # Article
    "ال": "the",
    # ── Preposition + pronoun fused forms ──
    # These are extremely common and must be recognized as function words.
    # Without them, sentences fail comprehensibility gate and corpus import.
    # بِـ (in/by/with)
    "به": "in/by him", "بها": "in/by her", "بهم": "in/by them", "بهما": "in/by them (dual)",
    "بك": "in/by you", "بكم": "in/by you (pl)", "بي": "in/by me", "بنا": "in/by us",
    # لِـ (for/to)
    "له": "for him", "لها": "for her", "لهم": "for them", "لهما": "for them (dual)",
    "لك": "for you", "لكم": "for you (pl)", "لي": "for me", "لنا": "for us",
    # عَنْ (about/from)
    "عنه": "about him", "عنها": "about her", "عنهم": "about them", "عنهما": "about them (dual)",
    "عنك": "about you", "عني": "about me", "عنا": "about us",
    # مِنْ (from)
    "منه": "from him", "منها": "from her", "منهم": "from them", "منهما": "from them (dual)",
    "منك": "from you", "مني": "from me", "منا": "from us",
    # فِي (in)
    "فيه": "in him/it", "فيها": "in her/it", "فيهم": "in them", "فيهما": "in them (dual)",
    "فيك": "in you", "فينا": "in us",
    # عَلَى (on/upon)
    "عليه": "on him", "عليها": "on her", "عليهم": "on them", "عليهما": "on them (dual)",
    "عليك": "on you", "عليكم": "on you (pl)", "علينا": "on us",
    # إِلَى (to)
    "اليه": "to him", "إليه": "to him", "اليها": "to her", "إليها": "to her",
    "اليهم": "to them", "إليهم": "to them", "اليهما": "to them (dual)", "إليهما": "to them (dual)",
    "اليك": "to you", "إليك": "to you",
    "الينا": "to us", "إلينا": "to us",
    # مَعَ (with)
    "معه": "with him", "معها": "with her", "معهم": "with them", "معهما": "with them (dual)",
    "معك": "with you", "معي": "with me", "معنا": "with us",
    # لَدَى / عِنْدَ (at/with)
    "لديه": "he has", "لديها": "she has", "لديهم": "they have", "لديهما": "they (dual) have",
    "لديك": "you have", "لدي": "I have", "لدينا": "we have",
    "عنده": "he has", "عندها": "she has", "عندهم": "they have", "عندهما": "they (dual) have",
    "عندك": "you have", "عندي": "I have", "عندنا": "we have",
}

# Populate FUNCTION_WORDS from the glosses dict
FUNCTION_WORDS.update(FUNCTION_WORD_GLOSSES.keys())


def strip_punctuation(text: str) -> str:
    """Remove Arabic and Latin punctuation from text."""
    return ARABIC_PUNCTUATION.sub("", text)


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


def normalize_quranic_to_msa(text: str) -> str:
    """Convert Quranic Mushaf presentation letters to standard MSA letters.

    Handles three Quranic-only letters that must be converted BEFORE
    strip_diacritics (U+0670 is in the diacritic range and would otherwise
    be stripped, losing vowel information):

    - Dagger alef (U+0670 ـٰ) → ا. After conversion, collapse the resulting
      duplicate when it sits next to an alef (ا) or alif maksura (ى), which
      already encode the long ā.
    - Small waw (U+06E5 ۥ) → strip. In MSA orthography the long ū after a
      damma-bearing pronoun suffix (هُۥ) is implicit.
    - Small ya (U+06E6 ۦ) → strip. Same reasoning for long ī.

    Alif waṣlah ٱ is already normalized in normalize_alef().
    Quranic annotation marks (ۡ ٓ ۖ ۗ etc.) are stripped by strip_diacritics().
    """
    # Small waw/ya — redundant with the vowel context in MSA, drop them.
    text = text.replace("\u06E5", "").replace("\u06E6", "")
    # Dagger alef → alef; then collapse adjacent duplicates.
    text = text.replace("\u0670", "\u0627")
    text = text.replace("\u0627\u0627", "\u0627")  # اا → ا
    text = text.replace("\u0649\u0627", "\u0649")  # ىا → ى
    text = text.replace("\u0627\u0649", "\u0649")  # اى → ى (rare but safe)
    return text


def strip_tanwin_alif(text: str) -> str:
    """Strip trailing alif that was the seat of fathatan (accusative tanwin).

    After diacritics are stripped, سَعِيدًا becomes سعيدا — the trailing alif
    is a grammatical marker, not part of the root. Stripping it allows matching
    the base form سعيد. Also handles alif maqsura seat (ًى → ى → strip).

    Only strips if the word has 3+ characters (to avoid destroying short words).
    """
    if len(text) >= 3 and text.endswith("ا"):
        return text[:-1]
    return text


def normalize_arabic(text: str) -> str:
    """Full normalization: Quranic→MSA, strip diacritics, tatweel, normalize alef.

    Quranic→MSA must run first because U+0670 (dagger alef) is in the diacritic
    range and would otherwise be stripped, losing the long-vowel information.
    """
    text = normalize_quranic_to_msa(text)
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


def tokenize_display(text: str) -> list[str]:
    """Tokenize Arabic text preserving punctuation attached to words.

    Used for creating SentenceWord records where surface_form should
    preserve original punctuation (question marks, periods, commas).
    Filters out pure-punctuation tokens.
    """
    result = []
    for t in text.split():
        if not t.strip():
            continue
        if strip_punctuation(t).strip():
            result.append(t)
    return result


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
    """Check if a bare form is a grammar particle.

    Function words are excluded from story/book "to learn" counts and from
    book page word introduction priority. They can still be learned through
    normal sentence review when encountered as scaffold words.
    """
    if not FUNCTION_WORDS:
        return False
    stripped = strip_diacritics(bare_form)
    normalized = normalize_alef(stripped)
    return normalized in _FUNCTION_WORDS_NORMALIZED


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
    alternative_lemma_ids: list[int] | None = None
    via_clitic: bool = False
    is_proper_name: bool = False


def detect_proper_names(
    unmapped_words: dict[str, int],
    lemma_lookup: dict[str, int],
    min_frequency: int = 3,
) -> set[str]:
    """Identify likely proper names from a frequency map of unmapped words.

    Heuristics:
    - Word appears at least min_frequency times (not a one-off OCR error)
    - Not in lemma lookup (already checked, but safety)
    - Not a common Arabic morphological pattern that we just don't have
    - Prioritizes: short words (2-4 chars), words without Arabic article,
      words that don't decompose via clitic stripping to known lemmas

    Args:
        unmapped_words: {normalized_bare_form: count} of words that failed lookup
        lemma_lookup: the lemma lookup dict (for final verification)
        min_frequency: minimum occurrences to consider (filters OCR noise)

    Returns:
        Set of normalized bare forms identified as proper names.
    """
    # Common foreign name transliterations in Arabic children's books.
    # These appear across many translated works (Grimm, Dickens, etc.)
    KNOWN_FOREIGN_NAMES = {
        # English/European names
        "بيتر", "توم", "ماري", "جون", "جيم", "هنري", "ديفيد", "جورج",
        "تشارلز", "ويليام", "روبرت", "ريتشارد", "جيمس", "ادوارد",
        "اليس", "دوروثي", "مارجريت", "اليزابيث", "كاترين",
        "بوبي", "ريدي", "بيل", "جاك", "سام", "بن", "دان", "تيم",
        "سالي", "جين", "كيت", "روز", "لوسي", "ايمي", "بيتي",
        "هايدي", "كلارا", "فريتس", "سيباستيان",
        # German/French names common in fairy tales
        "هانز", "جريتل", "رابونزل", "هانسل",
        # Titles that act as names in context
        "السيد", "السيدة", "الآنسة", "البروفيسور", "الدكتور",
        # Character names from specific Hindawi children's books
        "فوكس", "ميكي", "دوليتل", "كوبرفيلد", "هاملت",
        "هولمز", "براون", "هوكاي", "كوجيا", "بلاكي", "مارثا",
        "مايلز", "بيجوتي", "ويندي", "فيلياس", "باسبارتو",
        "ثرثار", "سوسنة", "جولييت", "روميو", "شيرلوك",
        "فرانك", "ادم", "حنا", "ماركو", "فرانسيس",
        "مولي", "جيني", "بيكي", "تينكر", "ماكبث",
    }
    known_norm = {normalize_alef(n) for n in KNOWN_FOREIGN_NAMES}

    names: set[str] = set()
    for word, count in unmapped_words.items():
        if count < min_frequency:
            continue
        if word in lemma_lookup:
            continue

        word_norm = normalize_alef(word)

        # Match against known foreign names
        if word_norm in known_norm:
            names.add(word)

    return names


def map_tokens_to_lemmas(
    tokens: list[str],
    lemma_lookup: dict[str, int],
    target_lemma_id: int,
    target_bare: str,
    proper_names: set[str] | None = None,
) -> list[TokenMapping]:
    """Map tokenized sentence words to lemma IDs.

    Args:
        tokens: Tokenized Arabic words (from tokenize() or tokenize_display()).
                May include attached punctuation which is stripped for matching
                but preserved in surface_form.
        lemma_lookup: Dict of {normalized_bare_form: lemma_id} including
                      al-prefix variants.
        target_lemma_id: The lemma_id of the target word.
        target_bare: Bare form of the target word.
        proper_names: Optional set of normalized bare forms to treat as proper
                      names (no lemma required). Used by corpus import to skip
                      character names like بيتر, توم, etc.

    Returns:
        List of TokenMapping with position, surface_form, lemma_id, flags.
    """
    proper_names_norm = set()
    if proper_names:
        proper_names_norm = {normalize_alef(strip_diacritics(n)) for n in proper_names}

    target_normalized = normalize_alef(target_bare)
    target_forms = {target_normalized}
    if not target_normalized.startswith("ال"):
        target_forms.add("ال" + target_normalized)
    if target_normalized.startswith("ال") and len(target_normalized) > 2:
        target_forms.add(target_normalized[2:])

    result: list[TokenMapping] = []
    for i, token in enumerate(tokens):
        bare = strip_diacritics(token)
        bare_clean = strip_punctuation(strip_tatweel(bare))
        if not bare_clean:
            continue
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

        # Check proper names before function word / lemma lookup
        if proper_names_norm and bare_norm in proper_names_norm:
            result.append(TokenMapping(i, token, None, False, False, is_proper_name=True))
            continue

        is_function = _is_function_word(bare_clean)
        if is_function:
            # Direct-only lookup for function words — no clitic stripping.
            # This prevents false analysis like كانت → ك+انت → أنت.
            lemma_id = lookup_lemma_direct(bare_norm, lemma_lookup, original_bare=bare_clean)
            result.append(TokenMapping(i, token, lemma_id, False, is_function))
        else:
            alternatives: list[int] = []
            clitic_flag: list[bool] = [False]
            lemma_id = lookup_lemma(
                bare_norm, lemma_lookup, original_bare=bare_clean,
                out_alternatives=alternatives,
                out_via_clitic=clitic_flag,
            )
            # Deduplicate and exclude winner
            alts = list(dict.fromkeys(a for a in alternatives if a != lemma_id))
            result.append(TokenMapping(
                i, token, lemma_id, False, False,
                alternative_lemma_ids=alts or None,
                via_clitic=clitic_flag[0],
            ))

    return result


def lookup_lemma_direct(
    bare_norm: str,
    lemma_lookup: dict[str, int],
    original_bare: str | None = None,
) -> int | None:
    """Find a lemma_id using direct match and al-prefix only — no clitic stripping.

    When a collision exists for the normalized key and ``original_bare`` is
    provided, delegates to ``_resolve_collision`` (hamza match then CAMeL) to
    pick the right lemma — same logic used by ``lookup_lemma`` for regular words.
    """

    def _check_collision(key: str) -> int | None:
        """If *key* has a collision entry and we can resolve it, return the winner."""
        if (original_bare
                and hasattr(lemma_lookup, "collisions")
                and key in lemma_lookup.collisions):
            resolved = _resolve_collision(
                original_bare, lemma_lookup.collisions[key]
            )
            if resolved is not None:
                return resolved
        return None

    if bare_norm in lemma_lookup:
        resolved = _check_collision(bare_norm)
        if resolved is not None:
            return resolved
        return lemma_lookup[bare_norm]
    if bare_norm.startswith("ال") and len(bare_norm) > 2:
        without_al = bare_norm[2:]
        if without_al in lemma_lookup:
            resolved = _check_collision(without_al)
            if resolved is not None:
                return resolved
            return lemma_lookup[without_al]
    elif len(bare_norm) >= 3:
        with_al = "ال" + bare_norm
        if with_al in lemma_lookup:
            resolved = _check_collision(with_al)
            if resolved is not None:
                return resolved
            return lemma_lookup[with_al]
    return None


def _resolve_collision(
    original_bare: str, candidates: list[tuple[int, str]]
) -> int | None:
    """Resolve a lemma collision using hamza-sensitive match, then CAMeL."""
    # Exact hamza-sensitive match (e.g., آب matches آب but not أب)
    for lid, cand_bare in candidates:
        if cand_bare == original_bare:
            return lid

    # Try CAMeL analysis
    try:
        from app.services.morphology import find_best_db_match

        cand_bares = {strip_diacritics(bare) for _, bare in candidates}
        match = find_best_db_match(original_bare, cand_bares)
        if match:
            matched_bare = match["lex_bare"]
            for lid, cand_bare in candidates:
                if strip_diacritics(cand_bare) == matched_bare:
                    return lid
    except Exception:
        pass

    return None


def lookup_lemma(
    bare_norm: str,
    lemma_lookup: dict[str, int],
    original_bare: str | None = None,
    out_alternatives: list[int] | None = None,
    out_via_clitic: list[bool] | None = None,
) -> int | None:
    """Find a lemma_id for a normalized bare form, trying variants and clitic stripping.

    Args:
        bare_norm: Alef-normalized bare form.
        lemma_lookup: Dict from build_lemma_lookup().
        original_bare: Pre-normalization bare form (preserves hamza/madda).
            Used for collision disambiguation.
        out_alternatives: If provided, alternative candidate lemma_ids are
            appended here when the mapping is ambiguous (collisions or
            multiple clitic interpretations). Callers can use these for
            LLM-based contextual disambiguation.
        out_via_clitic: If provided (as single-element list), set to [True]
            when the match came from clitic stripping rather than direct match.
    """
    # Direct match
    if bare_norm in lemma_lookup:
        # If collision exists and we have original form, disambiguate
        if (original_bare
                and hasattr(lemma_lookup, "collisions")
                and bare_norm in lemma_lookup.collisions):
            resolved = _resolve_collision(
                original_bare, lemma_lookup.collisions[bare_norm]
            )
            if resolved is not None:
                # Still report alternatives — hamza/CAMeL isn't always right
                if out_alternatives is not None:
                    for lid, _ in lemma_lookup.collisions[bare_norm]:
                        if lid != resolved:
                            out_alternatives.append(lid)
                return resolved
        # Unresolved collision — report all alternatives
        if (out_alternatives is not None
                and hasattr(lemma_lookup, "collisions")
                and bare_norm in lemma_lookup.collisions):
            for lid, _ in lemma_lookup.collisions[bare_norm]:
                if lid != lemma_lookup[bare_norm]:
                    out_alternatives.append(lid)
        return lemma_lookup[bare_norm]

    # With/without al-prefix
    if bare_norm.startswith("ال") and len(bare_norm) > 2:
        without_al = bare_norm[2:]
        if without_al in lemma_lookup:
            return lemma_lookup[without_al]
    elif len(bare_norm) >= 3:
        # Don't add ال to 2-char words — causes false matches
        # e.g. أن (ان) + ال → الان → الآن (now)
        with_al = "ال" + bare_norm
        if with_al in lemma_lookup:
            return lemma_lookup[with_al]

    # Clitic stripping — collect all candidates, prefer CAMeL disambiguation
    candidates = []
    for stem in _strip_clitics(bare_norm):
        norm_stem = normalize_alef(stem)
        if norm_stem in lemma_lookup:
            candidates.append(lemma_lookup[norm_stem])

    if len(candidates) == 1:
        if out_via_clitic is not None:
            out_via_clitic[0] = True
        return candidates[0]
    if len(candidates) > 1:
        if out_via_clitic is not None:
            out_via_clitic[0] = True
        # Multiple clitic interpretations — try CAMeL to disambiguate
        camel_id = _camel_disambiguate(
            original_bare or bare_norm, lemma_lookup
        )
        if camel_id is not None:
            if out_alternatives is not None:
                for c in candidates:
                    if c != camel_id:
                        out_alternatives.append(c)
            return camel_id
        # Report all non-winner candidates as alternatives
        if out_alternatives is not None:
            for c in candidates[1:]:
                if c != candidates[0]:
                    out_alternatives.append(c)
        return candidates[0]  # fallback to first match

    # No clitic match — try CAMeL as last resort for unmapped words
    camel_id = _camel_disambiguate(original_bare or bare_norm, lemma_lookup)
    if camel_id is not None:
        return camel_id

    return None


def _camel_disambiguate(word: str, lemma_lookup: dict[str, int]) -> int | None:
    """Use CAMeL morphological analysis to find the best lemma match.

    Args:
        word: Arabic word (pre-normalization preferred for better accuracy).
        lemma_lookup: Normalized bare form → lemma_id dict.
    """
    try:
        from app.services.morphology import find_best_db_match
        known_bare_forms = set(lemma_lookup.keys())
        match = find_best_db_match(word, known_bare_forms)
        if match:
            lex_norm = normalize_alef(match["lex_bare"])
            return lemma_lookup.get(lex_norm)
    except Exception:
        pass
    return None


def lookup_lemma_id(surface_form: str, lemma_lookup: dict[str, int]) -> int | None:
    """Resolve a sentence token surface form to a lemma_id using lookup variants."""
    bare = strip_diacritics(surface_form)
    bare_clean = strip_tatweel(bare)
    bare_norm = normalize_alef(bare_clean)
    return lookup_lemma(bare_norm, lemma_lookup)


class LemmaLookupDict(dict):
    """Dict subclass that tracks collisions for lemma lookups.

    When two different lemmas normalize to the same key (e.g., أب and آب
    both normalize to اب), the first one wins and the collision is recorded
    for hamza-sensitive or CAMeL-based disambiguation at lookup time.
    """

    def __init__(self):
        super().__init__()
        # normalized_key → [(lemma_id, pre_normalized_bare), ...]
        self.collisions: dict[str, list[tuple[int, str]]] = {}
        self._first_bare: dict[str, str] = {}

    def set_if_new(self, key: str, lemma_id: int, original_bare: str = "") -> None:
        """Set key→lemma_id without overwriting. Track collisions."""
        bare = original_bare or key
        if key in self:
            if self[key] != lemma_id:
                if key not in self.collisions:
                    first_bare = self._first_bare.get(key, key)
                    self.collisions[key] = [(self[key], first_bare)]
                if lemma_id not in [lid for lid, _ in self.collisions[key]]:
                    self.collisions[key].append((lemma_id, bare))
        else:
            self[key] = lemma_id
            self._first_bare[key] = bare


_PAST_3MS_SUFFIXES = ["ت", "ا", "تا", "وا", "ن"]  # 3fs, 3md, 3fd, 3mp, 3fp
_PAST_1S2_SUFFIXES = ["", "ي", "ما", "م", "ن", "نا"]  # 1s, 2fs, 2md, 2mp, 2fp, 1p
_PRESENT_PREFIXES = ["ي", "ت", "ا", "ن"]
_PRESENT_SUFFIXES = ["ون", "ان", "ين", "ن", "ي"]

# Noun inflection suffixes
_SOUND_F_PLURAL_SUFFIX = "ات"
_SOUND_M_PLURAL_SUFFIXES = ["ون", "ين"]
_DUAL_SUFFIXES = ["ان", "ين"]


def _generate_verb_conjugations(
    past_bare: str,
    present_bare: str | None,
    past_1s_bare: str | None = None,
) -> set[str]:
    """Generate common Arabic verb conjugation forms from known base forms.

    Given the 3ms past (e.g., كتب) and 3ms present (e.g., يكتب), generates
    all standard conjugations by applying regular suffix/prefix patterns.

    If past_1s is provided (e.g., قلت for قال), extracts the shortened stem
    for weak verb 1st/2nd person past forms. Without it, falls back to
    regular suffixation on the 3ms base (works for sound verbs only).

    Returns bare (undiacritized) forms, not including the input forms.
    """
    forms: set[str] = set()

    # Past tense: 3ms base + suffixes for 3rd person forms
    if len(past_bare) >= 2:
        for suffix in _PAST_3MS_SUFFIXES:
            forms.add(past_bare + suffix)

    # Past tense: 1st/2nd person forms — use past_1s stem if available (weak verbs)
    # For قال: past_1s=قلت → stem=قل, generates قلت/قلتي/قلتما/قلتم/قلتن/قلنا
    # For كتب (sound): past_1s=كتبت → stem=كتب (same as 3ms base)
    past_12_stem = None
    if past_1s_bare and len(past_1s_bare) >= 2:
        # Strip the ت suffix to get the stem
        if past_1s_bare.endswith("ت"):
            past_12_stem = past_1s_bare[:-1]
        else:
            past_12_stem = past_1s_bare
    if past_12_stem is None and len(past_bare) >= 2:
        past_12_stem = past_bare  # fallback: regular suffixation on 3ms base
    if past_12_stem and len(past_12_stem) >= 2:
        for suffix in _PAST_1S2_SUFFIXES:
            form = past_12_stem + "ت" + suffix if suffix else past_12_stem + "ت"
            forms.add(form)
        # 1p uses نا directly on stem
        forms.add(past_12_stem + "نا")

    # Present tense: extract stem, apply prefix/suffix combinations
    if present_bare and len(present_bare) >= 3 and present_bare[0] in "يتان":
        present_stem = present_bare[1:]  # strip 3ms prefix ي/ت
        if len(present_stem) >= 2:
            for prefix in _PRESENT_PREFIXES:
                forms.add(prefix + present_stem)
            for prefix in _PRESENT_PREFIXES:
                for suffix in _PRESENT_SUFFIXES:
                    forms.add(prefix + present_stem + suffix)

    # Filter: discard forms shorter than 2 chars (noise from short roots)
    return {f for f in forms if len(f) >= 2}


def _generate_noun_inflections(bare: str) -> set[str]:
    """Generate sound plural and dual forms for a noun/adjective base.

    Produces ـات (sound feminine plural), ـون/ـين (sound masculine plural),
    and ـان/ـين (dual) forms. These are speculative — many nouns use broken
    plurals instead. forms_json entries from LLM enrichment take priority
    in the lookup (Pass 2 > Pass 3).
    """
    forms: set[str] = set()
    if len(bare) < 2:
        return forms

    # Strip taa marbuta (ة→ stripped) for feminine nouns: معلمة → معلم + ات
    stem = bare
    if stem.endswith("ة") or stem.endswith("ه"):
        stem = stem[:-1]

    if len(stem) >= 2:
        forms.add(stem + _SOUND_F_PLURAL_SUFFIX)  # ـات
        for suffix in _SOUND_M_PLURAL_SUFFIXES:
            forms.add(stem + suffix)  # ـون / ـين
        for suffix in _DUAL_SUFFIXES:
            forms.add(stem + suffix)  # ـان / ـين (dual)

    return {f for f in forms if len(f) >= 2}


def build_lemma_lookup(lemmas: list) -> dict[str, int]:
    """Build a normalized bare form → lemma_id lookup dict.

    Includes both with and without al-prefix for each lemma,
    plus inflected forms from forms_json (plurals, feminines, verb
    conjugations, etc.), plus FUNCTION_WORD_FORMS conjugation mappings.

    Tracks collisions: when two lemmas normalize to the same key,
    first one wins and the collision is logged. Use the collisions
    attribute on the returned dict for disambiguation.

    Two-pass construction ensures direct lemma bare forms always take
    priority over derived forms from forms_json (e.g. حول "around"
    wins over حَوْل masdar of حال "to change").

    Args:
        lemmas: List of Lemma model objects with lemma_ar_bare and lemma_id.
    """
    lookup = LemmaLookupDict()
    bare_to_id: dict[str, int] = {}

    # Pass 1: Register all lemma bare forms (highest priority)
    for lem in lemmas:
        bare_norm = normalize_alef(lem.lemma_ar_bare)
        lookup.set_if_new(bare_norm, lem.lemma_id, lem.lemma_ar_bare)
        bare_to_id.setdefault(bare_norm, lem.lemma_id)
        if bare_norm.startswith("ال") and len(bare_norm) > 2:
            without_al = bare_norm[2:]
            lookup.set_if_new(without_al, lem.lemma_id, lem.lemma_ar_bare)
            bare_to_id.setdefault(without_al, lem.lemma_id)
        elif not bare_norm.startswith("ال"):
            lookup.set_if_new("ال" + bare_norm, lem.lemma_id, lem.lemma_ar_bare)

    # Pass 2: Register derived forms from forms_json (lower priority)
    # Indexes ALL string-valued keys — no hardcoded whitelist needed
    _FORMS_SKIP_KEYS = {"gender", "verb_form"}  # non-Arabic metadata
    for lem in lemmas:
        forms = getattr(lem, "forms_json", None)
        if forms and isinstance(forms, dict):
            for key, form_val in forms.items():
                if key in _FORMS_SKIP_KEYS:
                    continue
                if form_val and isinstance(form_val, str):
                    form_bare = normalize_alef(strip_diacritics(form_val))
                    lookup.set_if_new(form_bare, lem.lemma_id, form_val)
                    if not form_bare.startswith("ال"):
                        lookup.set_if_new("ال" + form_bare, lem.lemma_id, form_val)

    # Pass 3: Generate verb conjugation + noun inflection forms algorithmically
    pre_gen_size = len(lookup)
    for lem in lemmas:
        forms = getattr(lem, "forms_json", None)

        # Verb conjugations: use past_1s for weak verb stems when available
        if forms and isinstance(forms, dict) and forms.get("present"):
            present_val = forms["present"]
            if isinstance(present_val, str):
                past_bare = normalize_alef(lem.lemma_ar_bare)
                present_bare = normalize_alef(strip_diacritics(present_val))
                past_1s_val = forms.get("past_1s")
                past_1s_bare = normalize_alef(strip_diacritics(past_1s_val)) if past_1s_val and isinstance(past_1s_val, str) else None
                conjugations = _generate_verb_conjugations(past_bare, present_bare, past_1s_bare)
                for conj_form in conjugations:
                    lookup.set_if_new(conj_form, lem.lemma_id, f"conj:{conj_form}")

        # Noun/adjective inflections: sound plurals + dual
        pos = getattr(lem, "pos", None)
        if pos in ("noun", "adjective", None):
            bare = normalize_alef(lem.lemma_ar_bare)
            inflections = _generate_noun_inflections(bare)
            for infl_form in inflections:
                lookup.set_if_new(infl_form, lem.lemma_id, f"infl:{infl_form}")

    generated_forms = len(lookup) - pre_gen_size
    if generated_forms:
        _validator_logger.info(f"Lemma lookup: Pass 3 added {generated_forms} generated forms (verb conjugations + noun inflections)")

    # Add FUNCTION_WORD_FORMS: map conjugated forms to their base lemma_id
    for form, base in FUNCTION_WORD_FORMS.items():
        form_norm = normalize_alef(form)
        if form_norm not in lookup:
            base_norm = normalize_alef(base)
            base_id = bare_to_id.get(base_norm)
            if base_id is not None:
                lookup[form_norm] = base_id

    if lookup.collisions:
        _validator_logger.info(
            f"Lemma lookup: {len(lookup.collisions)} collision(s) on normalized forms"
        )
        for key, entries in lookup.collisions.items():
            ids_str = ", ".join(f"#{lid} ({bare})" for lid, bare in entries)
            _validator_logger.debug(f"  Collision on '{key}': {ids_str}")

    return lookup


def build_comprehensive_lemma_lookup(db) -> dict[str, int]:
    """Build lookup from ALL lemmas for sentence_word mapping.

    Unlike build_lemma_lookup() called with filtered lemmas, this includes
    every non-variant lemma in the database — function words, encountered
    words, etc. Used when creating SentenceWord records so every token
    can be mapped to a lemma_id.
    """
    from app.models import Lemma

    all_lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all()
    return build_lemma_lookup(all_lemmas)


def verify_word_mappings_llm(
    arabic_text: str,
    english_text: str,
    mappings: list[TokenMapping],
    lemma_map: dict[int, object],
) -> list[int]:
    """Ask LLM to verify word-lemma mappings make sense in context.

    Returns list of positions where the mapping looks wrong.
    Thin wrapper around verify_and_correct_mappings_llm for backward compat.
    """
    corrections = verify_and_correct_mappings_llm(
        arabic_text, english_text, mappings, lemma_map,
    )
    return [c["position"] for c in corrections]


_MAPPING_VERIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "position": {"type": "integer"},
                    "correct_lemma_ar": {"type": "string"},
                    "correct_gloss": {"type": "string"},
                    "correct_pos": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["position", "correct_lemma_ar", "correct_gloss", "correct_pos", "explanation"],
            },
        },
    },
    "required": ["issues"],
}


def verify_and_correct_mappings_llm(
    arabic_text: str,
    english_text: str,
    mappings: list[TokenMapping],
    lemma_map: dict[int, object],
) -> list[dict] | None:
    """Verify word-lemma mappings and suggest corrections for wrong ones.

    Returns:
        list[dict]: corrections needed (empty list = verified OK)
        None: verification failed (LLM unavailable) — caller must NOT
              treat this as "verified OK"; sentence should be rejected/skipped.

    Uses --json-schema for constrained decoding so the CLI model's output
    is guaranteed valid JSON. Without this, CLI models wrap JSON in
    explanation text which caused silent parse failures — Sonnet's correct
    answers were discarded and the weak API Haiku fallback missed errors.
    """
    from app.services.llm import generate_completion, AllProvidersFailed

    word_lines = []
    for m in mappings:
        lemma = lemma_map.get(m.lemma_id)
        if lemma and hasattr(lemma, "gloss_en"):
            gloss = lemma.gloss_en or "?"
            lar = lemma.lemma_ar or "?"
        else:
            continue
        tag = " [via clitic stripping]" if m.via_clitic else ""
        word_lines.append(f"  {m.position}: {m.surface_form} → {lar} ({gloss}){tag}")

    if not word_lines:
        return []

    prompt = f"""Arabic sentence: {arabic_text}
English translation: {english_text}

Word-to-lemma mappings:
{chr(10).join(word_lines)}

Your task: check that each word's lemma MAKES SENSE in the context of this sentence and its English translation. For each wrong mapping, provide the correct lemma.

Flag as WRONG (and provide correction):
- The lemma's English gloss doesn't match what the word means in this sentence (e.g. "to sleep" in a sentence about growing, "classroom" in a sentence about describing)
- **Homograph collisions**: same consonants but different meanings depending on voweling (e.g. جَدّ "grandfather" vs جِدّ "seriousness", حَرَم "to deprive" vs حَرَم "sanctuary", عِلم "knowledge" vs عَلَم "flag"). If the English translation uses a meaning that doesn't match the mapped gloss, FLAG IT even if they share the same root.
- A verb mapped to an unrelated noun or vice versa when they happen to share consonants (e.g. طَائِر "bird" mapped to طار "to fly" — these are different lemmas)
- A clitic prefix (و/ف/ب/ل/ك) wrongly stripped from a word where the letter is part of the root (e.g. وَصْف "description" stripped to صف "row/class")
- An active participle / verbal noun mapped to the root verb when it should be its own lemma (e.g. حُضُور "attendance" mapped to حاضر "present")
- A noun/verb homograph mapped to the wrong part of speech (e.g. ذَهَب "gold" mapped to ذَهَبَ "to go")

Do NOT flag (these are CORRECT):
- A conjugated verb mapped to its dictionary form, when the MEANING matches the sentence (e.g. يَكْتُبُ "he writes" mapped to كَتَبَ "to write")
- A plural/feminine/dual form mapped to its base lemma (e.g. مُعَلِّمَة mapped to مُعَلِّم)
- A noun with possessive suffix mapped to the base noun (e.g. أُمِّي mapped to أُمّ)
- A word with preposition prefix where the base word is correct (e.g. بِالعَرَبِيَّة mapped to عَرَبِيّ)

Words marked [via clitic stripping] had a prefix/suffix removed during lookup — these are higher risk for errors. Pay extra attention to them.

When in doubt, flag it — a false positive just causes a retry, but a false negative reaches the user.

Return issues array: empty if all correct, or one entry per wrong mapping."""

    system = "You are an Arabic morphology expert. Check each mapping against the English translation. Flag any mapping where the gloss doesn't fit the sentence meaning."

    # Try Claude CLI with structured output (free), then API fallback.
    # Structured output (--json-schema) guarantees valid JSON from CLI models.
    for model in ("claude_sonnet", "claude_haiku", "anthropic"):
        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=system,
                json_schema=_MAPPING_VERIFICATION_SCHEMA,
                temperature=0.0,
                model_override=model,
                task_type="mapping_verification",
                cli_only=(model != "anthropic"),
            )
            issues = result.get("issues", [])
            if isinstance(issues, list):
                return [
                    {
                        "position": int(iss["position"]),
                        "correct_lemma_ar": iss.get("correct_lemma_ar", ""),
                        "correct_gloss": iss.get("correct_gloss", ""),
                        "correct_pos": iss.get("correct_pos", ""),
                        "explanation": iss.get("explanation", ""),
                    }
                    for iss in issues
                    if isinstance(iss, dict) and "position" in iss
                ]
        except (AllProvidersFailed, Exception) as e:
            _validator_logger.warning(f"Mapping verification failed with {model}: {e}")
            continue

    _validator_logger.error("Mapping verification failed on ALL models — sentence cannot be verified")
    return None


def batch_verify_sentences(
    sentences: list[dict],
    lemma_map: dict[int, object],
) -> list[dict] | None:
    """Verify mappings for multiple sentences in a single CLI call.

    Each entry in ``sentences`` must have:
        arabic: str, english: str, mappings: list[TokenMapping],
        has_ambiguous: bool

    Returns a list parallel to ``sentences``, each element being:
        {"disambiguation": [...], "issues": [...]}
    Returns None if the LLM call fails entirely.
    """
    from app.services.llm import generate_completion, AllProvidersFailed

    if not sentences:
        return []

    # Build combined prompt
    blocks = []
    for idx, sent in enumerate(sentences):
        word_lines = []
        for m in sent["mappings"]:
            lemma = lemma_map.get(m.lemma_id)
            if not lemma or not hasattr(lemma, "gloss_en"):
                continue
            tag = " [via clitic stripping]" if m.via_clitic else ""
            word_lines.append(
                f"  {m.position}: {m.surface_form} → {lemma.lemma_ar or '?'} "
                f"({lemma.gloss_en or '?'}){tag}"
            )

        # Add disambiguation options for ambiguous words
        disambig_lines = []
        if sent.get("has_ambiguous"):
            for m in sent["mappings"]:
                if not m.alternative_lemma_ids:
                    continue
                options = []
                for opt_id in [m.lemma_id] + m.alternative_lemma_ids:
                    opt_lem = lemma_map.get(opt_id)
                    if opt_lem:
                        options.append(
                            f"#{opt_id} {getattr(opt_lem, 'lemma_ar_bare', '?')} "
                            f"({getattr(opt_lem, 'gloss_en', '?')}, "
                            f"{getattr(opt_lem, 'pos', '?')})"
                        )
                if len(options) > 1:
                    labels = "ABCDEFGH"
                    opt_str = "\n".join(
                        f"    {labels[i]}) {o}" for i, o in enumerate(options)
                    )
                    disambig_lines.append(
                        f"  Position {m.position}: \"{m.surface_form}\"\n{opt_str}"
                    )

        block = f"=== Sentence {idx} ===\n"
        block += f"Arabic: {sent['arabic']}\nEnglish: {sent['english']}\n"
        block += f"Mappings:\n{chr(10).join(word_lines)}\n"
        if disambig_lines:
            block += f"Ambiguous (pick correct option):\n{chr(10).join(disambig_lines)}\n"
        blocks.append(block)

    prompt = f"""Check these {len(sentences)} Arabic sentences for correct word-lemma mappings.

For each sentence:
1. If ambiguous words are listed, pick the correct lemma based on context.
2. Check that each mapping's gloss matches the word's meaning in the sentence.

Flag as WRONG:
- Gloss doesn't match the word's meaning in context
- Homograph collisions (same consonants, different meanings)
- Clitic prefix wrongly stripped from a root letter
- Wrong part of speech

Do NOT flag:
- Conjugated verbs mapped to dictionary form (when meaning matches)
- Plural/feminine/dual mapped to base lemma
- Possessive/preposition affixes on correct base word

{chr(10).join(blocks)}

Return JSON:
{{"sentences": [
  {{"index": <int>, "disambiguation": [{{"position": <int>, "lemma_id": <int>}}], "issues": [{{"position": <int>, "correct_lemma_ar": "<bare>", "correct_gloss": "<English>", "correct_pos": "<pos>", "explanation": "<brief>"}}]}}
]}}
Only include sentences that have disambiguation choices or issues. Omit sentences where everything is correct."""

    system = (
        "You are an Arabic morphology expert. Check word-lemma mappings "
        "against English translations. Only flag clear errors."
    )

    # Try CLI first (free), fall back to Anthropic API.
    # Use json_schema= (not json_mode=True) for constrained decoding —
    # json_mode lets CLI models wrap JSON in explanation text that fails to parse.
    batch_schema = {
        "type": "object",
        "properties": {
            "sentences": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "disambiguation": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "position": {"type": "integer"},
                                    "lemma_id": {"type": "integer"},
                                },
                                "required": ["position", "lemma_id"],
                            },
                        },
                        "issues": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "position": {"type": "integer"},
                                    "correct_lemma_ar": {"type": "string"},
                                    "correct_gloss": {"type": "string"},
                                    "correct_pos": {"type": "string"},
                                    "explanation": {"type": "string"},
                                },
                                "required": ["position", "correct_lemma_ar", "correct_gloss", "correct_pos", "explanation"],
                            },
                        },
                    },
                    "required": ["index", "disambiguation", "issues"],
                },
            },
        },
        "required": ["sentences"],
    }

    result = None
    for model in ("claude_sonnet", "claude_haiku", "anthropic"):
        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=system,
                json_schema=batch_schema,
                temperature=0.0,
                model_override=model,
                task_type="batch_verification",
                cli_only=(model != "anthropic"),
            )
            break
        except (AllProvidersFailed, Exception) as e:
            _validator_logger.warning(f"Batch verification failed with {model}: {e}")
            continue

    if result is None:
        _validator_logger.error("Batch verification failed on ALL models")
        return None

    # Parse results into per-sentence dicts
    raw_sentences = result.get("sentences", [])
    # Build index lookup
    result_by_idx = {}
    for r in raw_sentences:
        if isinstance(r, dict) and "index" in r:
            result_by_idx[r["index"]] = r

    output = []
    for idx in range(len(sentences)):
        r = result_by_idx.get(idx, {})
        output.append({
            "disambiguation": r.get("disambiguation", []),
            "issues": r.get("issues", []),
        })
    return output


def _log_mapping_correction(
    corrections: list[dict],
    success: bool,
    sentence_arabic: str,
) -> None:
    """Log mapping correction attempt for cost/success tracking."""
    from app.config import settings
    import json as _json
    from datetime import datetime as _dt

    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"mapping_corrections_{_dt.now():%Y-%m-%d}.jsonl"

    entry = {
        "ts": _dt.now().isoformat(),
        "event": "mapping_correction",
        "success": success,
        "corrections_count": len(corrections),
        "sentence_preview": sentence_arabic[:80],
        "corrections": corrections,
    }
    try:
        with open(log_file, "a") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def correct_mapping(
    db,
    correct_ar: str,
    correct_gloss: str,
    correct_pos: str,
    current_lemma_id: int | None = None,
    lemma_lookup: "LemmaLookupDict | None" = None,
) -> int | None:
    """Find the correct lemma in DB and return its lemma_id.

    Searches by bare form (with/without al-prefix). Falls back to
    normalized lookup when exact match fails (handles alef/hamza
    mismatches between LLM output and stored bare forms).

    When ``current_lemma_id`` is provided, prefers a *different* lemma
    (handles homographs like سلم peace vs سلم ladder). Returns None if
    the correct lemma doesn't exist — callers should reject the sentence
    rather than auto-creating lemmas nobody asked to learn.
    """
    from app.models import Lemma

    # Defensive: LLM sometimes returns non-string values
    correct_ar = str(correct_ar) if correct_ar else ""
    if not correct_ar:
        return None

    correct_bare = normalize_arabic(correct_ar)

    # Fast path: exact match on lemma_ar_bare
    candidates = (
        db.query(Lemma)
        .filter(Lemma.lemma_ar_bare == correct_bare)
        .all()
    )
    if not candidates:
        if correct_bare.startswith("ال"):
            candidates = db.query(Lemma).filter(
                Lemma.lemma_ar_bare == correct_bare[2:]
            ).all()
        else:
            candidates = db.query(Lemma).filter(
                Lemma.lemma_ar_bare == "ال" + correct_bare
            ).all()

    # Fallback: normalized lookup handles alef/hamza mismatches between
    # LLM output and stored bare forms (e.g. DB has أمر, query has امر)
    if not candidates:
        if lemma_lookup is None:
            lemma_lookup = build_comprehensive_lemma_lookup(db)

        search_forms = [correct_bare]
        stripped = strip_tanwin_alif(correct_bare)
        if stripped != correct_bare:
            search_forms.append(stripped)

        candidate_ids: set[int] = set()
        for form in search_forms:
            lid = lemma_lookup.get(form)
            if lid is not None:
                candidate_ids.add(lid)
                if form in lemma_lookup.collisions:
                    for alt_lid, _ in lemma_lookup.collisions[form]:
                        candidate_ids.add(alt_lid)

        if candidate_ids:
            candidates = db.query(Lemma).filter(
                Lemma.lemma_id.in_(candidate_ids)
            ).all()

    if not candidates:
        return None

    # If we know the current (wrong) lemma_id, prefer a different one
    if current_lemma_id is not None:
        for c in candidates:
            if c.lemma_id != current_lemma_id:
                return c.lemma_id
        # Only the same lemma exists — return it so caller can decide
        return current_lemma_id

    return candidates[0].lemma_id


def apply_corrections(
    corrections: list[dict],
    mappings: list,
    db,
    lemma_lookup=None,
    arabic_text: str = "",
) -> list[int]:
    """Apply LLM-suggested corrections to word-lemma mappings.

    Single place for the correct_mapping → 3-way check pattern.
    Mutates ``mapping.lemma_id`` in-place for successful corrections.

    Args:
        corrections: list of dicts from verify_and_correct_mappings_llm,
            each with position, correct_lemma_ar, correct_gloss, correct_pos.
        mappings: objects with ``.position`` and ``.lemma_id`` attributes
            (TokenMapping, SentenceWord, StoryWord, etc.)
        db: SQLAlchemy session for correct_mapping lookups.
        lemma_lookup: optional pre-built lemma lookup dict.
        arabic_text: sentence text for logging.

    Returns:
        List of positions where correction failed (empty = all OK).
        Callers decide what to do with failures (reject sentence,
        null out lemma_id, etc.)
    """
    if not corrections:
        return []

    pos_to_mapping = {m.position: m for m in mappings}
    failed_positions: list[int] = []

    for corr in corrections:
        pos = corr.get("position") if isinstance(corr.get("position"), int) else corr["position"]
        m = pos_to_mapping.get(pos)
        if not m:
            continue

        new_lid = correct_mapping(
            db,
            str(corr.get("correct_lemma_ar", "") or ""),
            str(corr.get("correct_gloss", "") or ""),
            str(corr.get("correct_pos", "") or ""),
            current_lemma_id=m.lemma_id,
            lemma_lookup=lemma_lookup,
        )

        if new_lid and new_lid != m.lemma_id:
            _validator_logger.info(
                f"Corrected mapping pos {pos} '{m.surface_form}': "
                f"#{m.lemma_id} → #{new_lid}"
            )
            m.lemma_id = new_lid
        elif not new_lid:
            _validator_logger.warning(
                f"Correction for pos {pos} '{m.surface_form}': "
                f"correct lemma not found in DB"
            )
            failed_positions.append(pos)
        else:
            _validator_logger.warning(
                f"Correction for pos {pos} '{m.surface_form}': "
                f"returned same lemma #{m.lemma_id} — correct lemma not in vocabulary"
            )
            failed_positions.append(pos)

    _log_mapping_correction(corrections, not failed_positions, arabic_text)
    return failed_positions


def disambiguate_mappings_llm(
    arabic_text: str,
    english_text: str,
    mappings: list[TokenMapping],
    lemma_map: dict[int, object],
) -> list[TokenMapping]:
    """Use LLM with sentence context to resolve ambiguous token→lemma mappings.

    For tokens where lookup produced multiple candidates (alternative_lemma_ids),
    asks the LLM to pick the correct lemma. Returns the same list with lemma_id
    updated for any disambiguated tokens.
    """
    from app.services.llm import generate_completion, AllProvidersFailed

    ambiguous = [
        m for m in mappings
        if m.alternative_lemma_ids and m.lemma_id is not None
    ]
    if not ambiguous:
        return mappings

    # Build prompt listing only the ambiguous positions
    word_blocks = []
    for m in ambiguous:
        all_ids = [m.lemma_id] + m.alternative_lemma_ids
        options = []
        for idx, lid in enumerate(all_ids):
            lemma = lemma_map.get(lid)
            if lemma and hasattr(lemma, "gloss_en"):
                label = chr(65 + idx)  # A, B, C...
                options.append(f"  {label}) #{lid} {getattr(lemma, 'lemma_ar_bare', '?')} ({lemma.gloss_en}, {getattr(lemma, 'pos', '?')})")
        if options:
            word_blocks.append(
                f"Position {m.position}: \"{m.surface_form}\"\n" + "\n".join(options)
            )

    if not word_blocks:
        return mappings

    prompt = f"""Arabic: {arabic_text}
English: {english_text}

For each word below, pick the correct lemma based on the sentence context.

{chr(10).join(word_blocks)}

Return JSON: {{"choices": [{{"position": <int>, "lemma_id": <int>}}]}}
Only include positions where your choice differs from option A (the current mapping)."""

    # Try CLI first (free), fall back to Anthropic API. Exclude GPT-5.2.
    for model in ("claude_sonnet", "claude_haiku", "anthropic"):
        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt="You are an Arabic morphology expert. Pick the lemma that matches the word's meaning in this specific sentence.",
                json_mode=True,
                temperature=0.0,
                model_override=model,
                task_type="mapping_disambiguation",
                cli_only=(model != "anthropic"),
            )
            choices = result.get("choices", [])
            if not isinstance(choices, list):
                return mappings

            # Build position → mapping index for fast lookup
            pos_to_mapping = {m.position: m for m in mappings}
            valid_ids = set()
            for m in ambiguous:
                valid_ids.add(m.lemma_id)
                valid_ids.update(m.alternative_lemma_ids)

            for choice in choices:
                pos = choice.get("position")
                chosen_id = choice.get("lemma_id")
                if pos is None or chosen_id is None:
                    continue
                m = pos_to_mapping.get(pos)
                if m and chosen_id in (m.alternative_lemma_ids or []):
                    _validator_logger.info(
                        f"LLM disambiguated pos {pos} '{m.surface_form}': "
                        f"#{m.lemma_id} → #{chosen_id}"
                    )
                    m.lemma_id = chosen_id
            return mappings
        except (AllProvidersFailed, Exception) as e:
            _validator_logger.warning(f"LLM mapping disambiguation failed with {model}: {e}")
            continue

    _validator_logger.error("Mapping disambiguation failed on ALL models")
    return None  # caller should skip sentence with unresolved ambiguities


def resolve_existing_lemma(
    bare: str, lemma_lookup: dict[str, int]
) -> int | None:
    """Check if a bare form matches an existing lemma via clitic-aware lookup.

    Used by import scripts to avoid creating duplicate lemmas for clitic forms
    (وكتاب, كتابي, بالكتاب) or al-prefixed forms (الكتاب).

    Returns the matched lemma_id, or None if no match found.
    """
    bare_norm = normalize_alef(bare)
    return lookup_lemma(bare_norm, lemma_lookup, original_bare=bare)


@dataclass
class MultiTargetValidationResult:
    valid: bool
    targets_found: dict[str, bool]
    target_count: int
    unknown_words: list[str] = field(default_factory=list)
    known_words: list[str] = field(default_factory=list)
    function_words: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def validate_sentence_multi_target(
    arabic_text: str,
    target_bares: dict[str, int],
    known_bare_forms: set[str],
) -> MultiTargetValidationResult:
    """Validate that a sentence uses known words and contains target words.

    Args:
        arabic_text: The Arabic sentence (may include diacritics).
        target_bares: Dict mapping bare form -> lemma_id for each target word.
        known_bare_forms: Set of bare forms the user knows.

    Returns:
        MultiTargetValidationResult. Valid = at least 1 target found AND no unknown words.
    """
    tokens = tokenize(arabic_text)
    if not tokens:
        return MultiTargetValidationResult(
            valid=False, targets_found={}, target_count=0,
            issues=["Empty sentence"],
        )

    known_normalized = {normalize_alef(w) for w in known_bare_forms}

    # Build expanded target forms for each target (with/without al-prefix)
    target_form_map: dict[str, str] = {}  # normalized_form -> original_bare
    for bare in target_bares:
        norm = normalize_alef(bare)
        target_form_map[norm] = bare
        if not norm.startswith("ال"):
            target_form_map["ال" + norm] = bare
        if norm.startswith("ال") and len(norm) > 2:
            target_form_map[norm[2:]] = bare

    targets_found: dict[str, bool] = {bare: False for bare in target_bares}
    unknown_words: list[str] = []
    known_words: list[str] = []
    function_words: list[str] = []

    for token in tokens:
        bare = strip_diacritics(token)
        bare_clean = strip_tatweel(bare)
        bare_normalized = normalize_alef(bare_clean)

        # Check if it's a target word (try tanwin-alif stripping too)
        matched_target = target_form_map.get(bare_normalized)
        if not matched_target:
            sans_alif = strip_tanwin_alif(bare_normalized)
            if sans_alif != bare_normalized:
                matched_target = target_form_map.get(sans_alif)
        if not matched_target:
            for stem in _strip_clitics(bare_normalized):
                matched_target = target_form_map.get(normalize_alef(stem))
                if matched_target:
                    break
                stem_sans = strip_tanwin_alif(normalize_alef(stem))
                if stem_sans != normalize_alef(stem):
                    matched_target = target_form_map.get(stem_sans)
                    if matched_target:
                        break

        if matched_target:
            targets_found[matched_target] = True
            continue

        if _is_function_word(bare_clean):
            function_words.append(token)
            continue

        # Known word check (same logic as validate_sentence)
        is_known = False
        forms_to_check = [bare_normalized]
        if bare_normalized.startswith("ال") and len(bare_normalized) > 2:
            forms_to_check.append(bare_normalized[2:])
        if not bare_normalized.startswith("ال"):
            forms_to_check.append("ال" + bare_normalized)
        # Try stripping trailing alif (tanwin seat: سعيدًا → سعيدا → سعيد)
        sans_alif = strip_tanwin_alif(bare_normalized)
        if sans_alif != bare_normalized:
            forms_to_check.append(sans_alif)
            if not sans_alif.startswith("ال"):
                forms_to_check.append("ال" + sans_alif)
        for form in forms_to_check:
            if form in known_normalized:
                is_known = True
                break
        if not is_known:
            for stem in _strip_clitics(bare_normalized):
                stem_norm = normalize_alef(stem)
                if stem_norm in known_normalized or _is_function_word(stem_norm):
                    is_known = True
                    break
                stem_sans_alif = strip_tanwin_alif(stem_norm)
                if stem_sans_alif != stem_norm and (stem_sans_alif in known_normalized or _is_function_word(stem_sans_alif)):
                    is_known = True
                    break

        if is_known:
            known_words.append(token)
        else:
            unknown_words.append(token)

    target_count = sum(1 for found in targets_found.values() if found)
    issues: list[str] = []
    if target_count == 0:
        issues.append("No target words found in sentence")
    if unknown_words:
        issues.append(f"Unknown words: {', '.join(unknown_words)}")

    valid = target_count >= 1 and len(unknown_words) == 0

    return MultiTargetValidationResult(
        valid=valid,
        targets_found=targets_found,
        target_count=target_count,
        unknown_words=unknown_words,
        known_words=known_words,
        function_words=function_words,
        issues=issues,
    )


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

        # Check: is it the target word? (with ال prefix + tanwin-alif handling)
        target_forms = [target_normalized]
        if not target_normalized.startswith("ال"):
            target_forms.append("ال" + target_normalized)
        if target_normalized.startswith("ال") and len(target_normalized) > 2:
            target_forms.append(target_normalized[2:])

        # Try both the token as-is and with tanwin-alif stripped
        token_forms = [bare_normalized]
        token_sans_alif = strip_tanwin_alif(bare_normalized)
        if token_sans_alif != bare_normalized:
            token_forms.append(token_sans_alif)

        is_target = any(tf in target_forms for tf in token_forms)
        if not is_target:
            for stem in _strip_clitics(bare_normalized):
                stem_norm = normalize_alef(stem)
                if stem_norm in target_forms:
                    is_target = True
                    break
                stem_sans = strip_tanwin_alif(stem_norm)
                if stem_sans != stem_norm and stem_sans in target_forms:
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

        # Check: known word? Try the bare form and with/without ال prefix,
        # and with trailing tanwin-alif stripped (سعيدًا → سعيدا → سعيد).
        is_known = False
        forms_to_check = [bare_normalized]
        # If word starts with ال, also check without it
        if bare_normalized.startswith("ال") and len(bare_normalized) > 2:
            forms_to_check.append(bare_normalized[2:])
        # If word doesn't start with ال, also check with it
        if not bare_normalized.startswith("ال"):
            forms_to_check.append("ال" + bare_normalized)
        # Try stripping trailing alif (tanwin seat: سعيدًا → سعيدا → سعيد)
        sans_alif = strip_tanwin_alif(bare_normalized)
        if sans_alif != bare_normalized:
            forms_to_check.append(sans_alif)
            if not sans_alif.startswith("ال"):
                forms_to_check.append("ال" + sans_alif)

        for form in forms_to_check:
            if form in known_normalized:
                is_known = True
                break

        # Try clitic stripping if direct match failed
        if not is_known:
            for stem in _strip_clitics(bare_normalized):
                stem_norm = normalize_alef(stem)
                if stem_norm in known_normalized or _is_function_word(stem_norm):
                    is_known = True
                    break
                # Also try tanwin-alif stripping on clitic-stripped stems
                stem_sans_alif = strip_tanwin_alif(stem_norm)
                if stem_sans_alif != stem_norm and (stem_sans_alif in known_normalized or _is_function_word(stem_sans_alif)):
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
