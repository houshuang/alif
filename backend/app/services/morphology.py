"""Morphology analysis service using CAMeL Tools.

Provides Arabic word analysis: lemmatization, root extraction, POS tagging,
and morphological feature extraction (gender, number, enclitics, etc.).
Falls back to stub behavior if CAMeL Tools is not installed.
"""

import logging
import re

logger = logging.getLogger(__name__)

_ARABIC_LETTER_RE = re.compile(r'^[\u0621-\u064a]$')


def is_valid_root(root_str: str) -> bool:
    """Check if a root string is valid Arabic (3-4 dot-separated Arabic radicals)."""
    if not root_str:
        return False
    parts = root_str.split(".")
    if len(parts) not in (3, 4):
        return False
    return all(_ARABIC_LETTER_RE.match(p) for p in parts)


def backfill_root_meanings(db) -> int:
    """Fill in missing core_meaning_en for roots using LLM. Returns count filled."""
    from app.models import Root, Lemma
    from app.services.llm import generate_completion
    import json

    missing = db.query(Root).filter(
        (Root.core_meaning_en == None) | (Root.core_meaning_en == "")
    ).all()
    if not missing:
        return 0

    filled = 0
    batch_size = 20
    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        items = []
        for r in batch:
            sample = db.query(Lemma).filter(Lemma.root_id == r.root_id).first()
            items.append({
                "root": r.root,
                "sample_word": sample.lemma_ar_bare if sample else "",
                "sample_gloss": sample.gloss_en if sample else "",
            })

        result = generate_completion(
            prompt=f"For each Arabic root, provide a brief English meaning (5-10 words) describing the core semantic field.\n\nRoots:\n{json.dumps(items, ensure_ascii=False)}\n\nReturn a JSON array: [{{\"root\": \"...\", \"meaning\": \"...\"}}]",
            system_prompt="You are an Arabic morphology expert. Return valid JSON only.",
            json_mode=True,
            temperature=0.0,
            task_type="morphology",
        )

        results = result if isinstance(result, list) else result.get("roots", result.get("results", []))
        rmap = {r["root"]: r["meaning"] for r in results if isinstance(r, dict) and "meaning" in r}

        for r in batch:
            meaning = rmap.get(r.root)
            if meaning:
                r.core_meaning_en = meaning
                filled += 1

    db.flush()
    return filled


try:
    from camel_tools.morphology.database import MorphologyDB
    from camel_tools.morphology.analyzer import Analyzer
    from camel_tools.disambig.mle import MLEDisambiguator

    _db = None
    _analyzer = None
    _disambiguator = None
    CAMEL_AVAILABLE = True
    MLE_AVAILABLE = True
except ImportError:
    CAMEL_AVAILABLE = False
    MLE_AVAILABLE = False
    logger.info("camel_tools not installed, morphology analysis will use stubs")


def _get_analyzer():
    """Lazy-load the CAMeL Tools analyzer singleton."""
    global _db, _analyzer
    if _analyzer is None:
        _db = MorphologyDB.builtin_db()
        _analyzer = Analyzer(_db, backoff="ADD_PROP")
    return _analyzer


def _get_disambiguator():
    """Lazy-load the CAMeL Tools MLE disambiguator singleton."""
    global _disambiguator
    if not MLE_AVAILABLE:
        return None
    if _disambiguator is None:
        try:
            _disambiguator = MLEDisambiguator.pretrained()
        except Exception:
            logger.warning("MLE disambiguator model not available, falling back to analyzer")
            return None
    return _disambiguator


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


def get_best_lemma_mle(word: str) -> dict | None:
    """Get the most likely base lemma using MLE disambiguation.

    Returns dict with lex, root, pos from the MLE-selected analysis,
    or falls back to top analyzer result. Returns None if CAMeL unavailable.
    """
    disambig = _get_disambiguator()
    if disambig:
        try:
            results = disambig.disambiguate([word])
            if results and results[0].analyses:
                top = results[0].analyses[0].analysis
                return {
                    "lex": top.get("lex"),
                    "root": top.get("root"),
                    "pos": top.get("pos"),
                    "enc0": top.get("enc0", ""),
                }
        except Exception:
            logger.debug("MLE disambiguation failed for %s, falling back", word)

    analyses = analyze_word_camel(word)
    if analyses:
        top = analyses[0]
        return {
            "lex": top.get("lex"),
            "root": top.get("root"),
            "pos": top.get("pos"),
            "enc0": top.get("enc0", ""),
        }
    return None


def is_variant_form(word: str, base_lemma_bare: str) -> bool:
    """Check if word is an inflected variant of base_lemma (possessive, etc.).

    Iterates through all analyses to find one where:
    - The lex (base lemma) matches the expected base form (after stripping diacritics)
    - The word has a pronominal enclitic (enc0) or other variant marker
    """
    from app.services.sentence_validator import normalize_alef, strip_diacritics

    analyses = analyze_word_camel(word)
    base_norm = normalize_alef(base_lemma_bare)
    for a in analyses:
        lex = a.get("lex", "")
        enc0 = a.get("enc0", "")
        lex_bare = normalize_alef(strip_diacritics(lex))
        if lex_bare == base_norm and enc0 and enc0 != "0":
            return True
    return False


def find_matching_analysis(
    word: str, known_lemma_bare: str
) -> dict | None:
    """Find the analysis whose lex matches a known lemma bare form.

    Useful when disambiguation is needed: we check all analyses and pick
    the one that matches what we know from our DB.
    """
    from app.services.sentence_validator import normalize_alef, strip_diacritics

    analyses = analyze_word_camel(word)
    target = normalize_alef(known_lemma_bare)
    for a in analyses:
        lex = a.get("lex", "")
        if normalize_alef(strip_diacritics(lex)) == target:
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


def find_best_db_match(
    word: str, known_bare_forms: set[str], self_bare: str | None = None
) -> dict | None:
    """Find the CAMeL analysis whose lex matches a known lemma in our DB.

    Iterates all analyses (not just [0]) and returns the first one where
    strip_diacritics(lex) is in known_bare_forms and is not self_bare.
    Hamza is normalized at comparison time so أحب matches احب.

    Returns a dict with: lex_bare, enc0, analysis, or None if no match.
    """
    from app.services.sentence_validator import normalize_alef, strip_diacritics

    analyses = analyze_word_camel(word)
    if not analyses:
        analyses = analyze_word_camel(strip_diacritics(word))
    if not analyses:
        return None

    self_norm = normalize_alef(self_bare) if self_bare else None
    for a in analyses:
        lex = a.get("lex", "")
        lex_bare = strip_diacritics(lex)
        lex_norm = normalize_alef(lex_bare)
        if self_norm and lex_norm == self_norm:
            continue
        if lex_norm in known_bare_forms:
            return {
                "lex_bare": lex_bare,
                "enc0": a.get("enc0", ""),
                "analysis": a,
            }
    return None


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
