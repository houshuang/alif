"""Morphology analysis service using CAMeL Tools.

Provides Arabic word analysis: lemmatization, root extraction, POS tagging,
and morphological feature extraction (gender, number, enclitics, etc.).
Falls back to stub behavior if CAMeL Tools is not installed.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from camel_tools.morphology.database import MorphologyDB
    from camel_tools.morphology.analyzer import Analyzer

    _db = None
    _analyzer = None
    CAMEL_AVAILABLE = True
except ImportError:
    CAMEL_AVAILABLE = False
    logger.info("camel_tools not installed, morphology analysis will use stubs")


def _get_analyzer():
    """Lazy-load the CAMeL Tools analyzer singleton."""
    global _db, _analyzer
    if _analyzer is None:
        _db = MorphologyDB.builtin_db()
        _analyzer = Analyzer(_db, backoff="ADD_PROP")
    return _analyzer


def analyze_word_camel(word: str) -> list[dict]:
    """Return all morphological analyses for an Arabic word via CAMeL Tools.

    Each analysis dict contains keys like: lex, root, pos, enc0, num, gen, stt, etc.
    Returns empty list if CAMeL Tools is not available.
    """
    if not CAMEL_AVAILABLE:
        return []
    try:
        return _get_analyzer().analyze(word)
    except Exception:
        logger.exception("CAMeL Tools analysis failed for word: %s", word)
        return []


def get_base_lemma(word: str) -> str | None:
    """Get the base lemma (lex) for a word, picking the top analysis."""
    analyses = analyze_word_camel(word)
    if analyses:
        return analyses[0].get("lex")
    return None


def is_variant_form(word: str, base_lemma_bare: str) -> bool:
    """Check if word is an inflected variant of base_lemma (possessive, etc.).

    Iterates through all analyses to find one where:
    - The lex (base lemma) matches the expected base form (after stripping diacritics)
    - The word has a pronominal enclitic (enc0) or other variant marker
    """
    from app.services.sentence_validator import strip_diacritics

    analyses = analyze_word_camel(word)
    for a in analyses:
        lex = a.get("lex", "")
        enc0 = a.get("enc0", "")
        lex_bare = strip_diacritics(lex)
        if lex_bare == base_lemma_bare and enc0 and enc0 != "0":
            return True
    return False


def find_matching_analysis(
    word: str, known_lemma_bare: str
) -> dict | None:
    """Find the analysis whose lex matches a known lemma bare form.

    Useful when disambiguation is needed: we check all analyses and pick
    the one that matches what we know from our DB.
    """
    from app.services.sentence_validator import strip_diacritics

    analyses = analyze_word_camel(word)
    for a in analyses:
        lex = a.get("lex", "")
        if strip_diacritics(lex) == known_lemma_bare:
            return a
    return None


def get_word_features(word: str) -> dict:
    """Extract morphological features from the top analysis.

    Returns a dict with: lex, root, pos, enc0, num, gen, stt, source.
    """
    analyses = analyze_word_camel(word)
    if not analyses:
        return {
            "word": word,
            "lex": word,
            "root": None,
            "pos": "UNK",
            "enc0": None,
            "num": None,
            "gen": None,
            "stt": None,
            "source": "stub" if not CAMEL_AVAILABLE else "no_analysis",
        }

    top = analyses[0]
    return {
        "word": word,
        "lex": top.get("lex", word),
        "root": top.get("root"),
        "pos": top.get("pos", "UNK"),
        "enc0": top.get("enc0"),
        "num": top.get("num"),
        "gen": top.get("gen"),
        "stt": top.get("stt"),
        "source": "camel",
    }


def analyze_word(word: str) -> dict:
    """API-compatible analyze_word returning AnalyzeWordOut fields."""
    features = get_word_features(word)
    return {
        "word": word,
        "lemma": features["lex"],
        "root": features["root"],
        "pos": features["pos"],
        "gloss_en": None,
        "source": features["source"],
    }


def analyze_sentence(sentence: str) -> dict:
    """API-compatible analyze_sentence returning AnalyzeSentenceOut fields."""
    words = sentence.split()
    return {
        "sentence": sentence,
        "words": [analyze_word(w) for w in words],
        "source": "camel" if CAMEL_AVAILABLE else "stub",
    }
