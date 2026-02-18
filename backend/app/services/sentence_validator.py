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

# Formerly held ~100 function words excluded from FSRS. Now empty:
# ALL words are learnable and get FSRS scheduling. The frontend shows
# richer grammar info for particles (في، من، etc.) via grammar-particles.ts.
# The set and _is_function_word() are kept for backward compatibility.
FUNCTION_WORDS: set[str] = set()

# Fallback glosses for common words that may lack lemma entries.
# Used during sentence validation to provide gloss_en even without a DB lemma.
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
    """Check if a bare form is a grammar particle (excluded from FSRS).

    With FUNCTION_WORDS now empty, always returns False — all words are learnable.
    Kept for backward compatibility with callers.
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


def map_tokens_to_lemmas(
    tokens: list[str],
    lemma_lookup: dict[str, int],
    target_lemma_id: int,
    target_bare: str,
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

        is_function = _is_function_word(bare_clean)
        if is_function:
            # Direct-only lookup for function words — no clitic stripping.
            # This prevents false analysis like كانت → ك+انت → أنت.
            lemma_id = lookup_lemma_direct(bare_norm, lemma_lookup)
        else:
            lemma_id = lookup_lemma(
                bare_norm, lemma_lookup, original_bare=bare_clean,
            )
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
    elif len(bare_norm) >= 3:
        with_al = "ال" + bare_norm
        if with_al in lemma_lookup:
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
) -> int | None:
    """Find a lemma_id for a normalized bare form, trying variants and clitic stripping.

    Args:
        bare_norm: Alef-normalized bare form.
        lemma_lookup: Dict from build_lemma_lookup().
        original_bare: Pre-normalization bare form (preserves hamza/madda).
            Used for collision disambiguation.
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
                return resolved
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
        return candidates[0]
    if len(candidates) > 1:
        # Multiple clitic interpretations — try CAMeL to disambiguate
        camel_id = _camel_disambiguate(
            original_bare or bare_norm, lemma_lookup
        )
        if camel_id is not None:
            return camel_id
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
    for lem in lemmas:
        forms = getattr(lem, "forms_json", None)
        if forms and isinstance(forms, dict):
            for key, form_val in forms.items():
                if key in ("plural", "present", "masdar", "active_participle",
                           "feminine", "elative", "past_3fs", "past_3p",
                           "imperative", "passive_participle") or key.startswith("variant_"):
                    if form_val and isinstance(form_val, str):
                        form_bare = normalize_alef(strip_diacritics(form_val))
                        lookup.set_if_new(form_bare, lem.lemma_id, form_val)
                        if not form_bare.startswith("ال"):
                            lookup.set_if_new("ال" + form_bare, lem.lemma_id, form_val)

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
    Uses Gemini Flash for lowest cost (~$0.001 per call).
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
        word_lines.append(f"  {m.position}: {m.surface_form} → {lar} ({gloss})")

    if not word_lines:
        return []

    prompt = f"""Arabic sentence: {arabic_text}
English translation: {english_text}

Word-to-lemma mappings:
{chr(10).join(word_lines)}

ONLY flag a mapping as wrong if the lemma is a COMPLETELY DIFFERENT WORD than what appears in the sentence. Specifically:

Flag as WRONG:
- The word in context has a totally different meaning from the lemma's English gloss (e.g. حَوْلَ "around" mapped to حَالَ "to change" — different words despite shared root)
- A verb form mapped to an unrelated noun, or vice versa, when the bare forms happen to look the same (e.g. كَتَبَ "he wrote" mapped to كُتُب "books")
- A clitic combination misidentified as a single word (e.g. بِأَنَّ "with that" mapped to بَانَ "to separate")

Do NOT flag (these are CORRECT):
- A conjugated verb mapped to its dictionary/past-tense form (e.g. يَكْتُبُ mapped to كَتَبَ)
- A plural mapped to its singular lemma or vice versa (e.g. رِجَال mapped to رَجُل or رِجَال)
- A feminine form mapped to its masculine lemma (e.g. مُعَلِّمَة mapped to مُعَلِّم)
- A noun with possessive suffix mapped to the base noun (e.g. أُمِّي mapped to أُمّ)
- A word with a preposition prefix mapped to the base word (e.g. بِالعَرَبِيَّة mapped to عَرَبِيّ)
- A masdar mapped to its verb or vice versa, when semantically related (e.g. قِرَاءة mapped to قَرَأَ)
- A word mapped to a lemma whose gloss is a close synonym or semantic relative

Return JSON: {{"wrong": [list of position numbers where the mapping is WRONG]}}
If all mappings are acceptable, return {{"wrong": []}}
When in doubt, do NOT flag — only flag clear semantic mismatches."""

    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt="You are an Arabic morphology expert reviewing word-lemma mappings. Be conservative — only flag clear errors.",
            json_mode=True,
            temperature=0.0,
            model_override="gemini",
        )
        wrong = result.get("wrong", [])
        if isinstance(wrong, list):
            return [int(p) for p in wrong if isinstance(p, (int, float))]
    except (AllProvidersFailed, Exception) as e:
        _validator_logger.warning(f"LLM mapping verification failed: {e}")

    return []


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

        # Check if it's a target word
        matched_target = target_form_map.get(bare_normalized)
        if not matched_target:
            for stem in _strip_clitics(bare_normalized):
                matched_target = target_form_map.get(normalize_alef(stem))
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
                stem_norm = normalize_alef(stem)
                if stem_norm in known_normalized or _is_function_word(stem_norm):
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
