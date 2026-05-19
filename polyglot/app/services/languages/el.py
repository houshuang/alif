"""Modern Greek NLP provider — lemmatization via simplemma (dictionary-backed,
instant, deterministic). POS/morphology via GR-NLP-TOOLKIT loaded lazily when
needed for richer analysis.

simplemma covers Modern Greek + Latin + many others (so la.py uses the same
backend). For Ancient Greek, OdyCy/spaCy is still the right call — that
lives in grc.py.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from pathlib import Path

# HuggingFace cache — kept inside the project so the dev sandbox can write
# and the model is colocated with polyglot. Only relevant if you opt into the
# heavy GR-NLP-TOOLKIT path; simplemma needs none of this.
_HF_CACHE = Path(__file__).resolve().parents[3] / "data" / "hf_cache"
_HF_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(_HF_CACHE))
os.environ.setdefault("HF_HUB_CACHE", str(_HF_CACHE / "hub"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(_HF_CACHE))

from app.services.languages.base import (  # noqa: E402
    NLPProvider, ProviderUnavailable, Token, LemmaCandidate, Morphology,
)

log = logging.getLogger(__name__)

def _strip_accents_monotonic(form: str) -> str:
    """Strip acute / diaeresis from monotonic Greek so lookup is accent-agnostic.

    NOTE: stress is phonemic in Greek (πότε ≠ ποτέ), so we keep the *display*
    form accented on Lemma.lemma_form. Only the lookup key (`lemma_bare`)
    drops accents — same pattern as Alif's `lemma_ar_bare`.
    """
    decomposed = unicodedata.normalize("NFD", form)
    no_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", no_marks).lower()


# Greek + Latin word characters + apostrophe (for elisions like τ'άλλο)
_TOKEN_RE = re.compile(r"[Ͱ-Ͽἀ-῿\w']+|[^\w\s]", re.UNICODE)


class ModernGreekProvider:
    code = "el"
    display_name = "Modern Greek"
    _SIMPLEMMA_LANG = "el"

    def __init__(self):
        self._gr_pipeline = None
        self._tried_gr_load = False
        self._tried_simplemma = False
        self._simplemma_ok = False

    def _ensure_simplemma(self):
        """simplemma is the lemmatization backend. Cheap import (pure Python),
        but we still gate so unavailable installs degrade cleanly."""
        if self._tried_simplemma:
            if not self._simplemma_ok:
                raise ProviderUnavailable("simplemma not installed")
            return
        self._tried_simplemma = True
        try:
            import simplemma  # noqa: F401
            self._simplemma_ok = True
        except ImportError as e:
            raise ProviderUnavailable("simplemma not installed (pip install simplemma)") from e

    def _ensure_gr_pipeline(self):
        """GR-NLP-TOOLKIT loaded only when richer POS/morph/dep analysis is
        requested. Failing to load is non-fatal — lemmatization keeps working
        via simplemma."""
        if self._gr_pipeline is not None:
            return self._gr_pipeline
        if self._tried_gr_load:
            raise ProviderUnavailable("gr-nlp-toolkit failed to load (see earlier log)")
        self._tried_gr_load = True
        try:
            from gr_nlp_toolkit import Pipeline
            self._gr_pipeline = Pipeline("pos,ner,dp")
            log.info("Modern Greek: gr-nlp-toolkit pipeline loaded")
            return self._gr_pipeline
        except Exception as e:
            log.warning("Modern Greek pipeline failed to load: %s", e)
            raise ProviderUnavailable(f"gr-nlp-toolkit load failed: {e}") from e

    # ─── NLPProvider interface ────────────────────────────────────────────

    def tokenize(self, text: str) -> list[Token]:
        out: list[Token] = []
        pos = 0
        for m in _TOKEN_RE.finditer(text):
            tok = m.group()
            is_punct = not any(c.isalpha() for c in tok)
            out.append(Token(surface=tok, position=pos, is_punctuation=is_punct))
            pos += 1
        return out

    def lemmatize(self, surface: str, context: str | None = None) -> LemmaCandidate:
        try:
            self._ensure_simplemma()
        except ProviderUnavailable:
            return LemmaCandidate(lemma=surface, lemma_bare=self.normalize_bare(surface), confidence=0.0)
        import simplemma
        # simplemma's dictionary is lowercase — uppercase headings ("ΠΟΛΙΤΙΣΜΟΙ")
        # won't match unless we fold case first. We still feed the original
        # case to simplemma in case it has casing rules; if that returns the
        # surface unchanged, retry with .lower().
        lemma = simplemma.lemmatize(surface, lang=self._SIMPLEMMA_LANG)
        if lemma == surface and not surface.islower():
            lemma = simplemma.lemmatize(surface.lower(), lang=self._SIMPLEMMA_LANG)
        confidence = 1.0 if lemma.lower() != surface.lower() else 0.5
        return LemmaCandidate(
            lemma=lemma,
            lemma_bare=self.normalize_bare(lemma),
            pos=None,
            confidence=confidence,
        )

    def analyze(self, surface: str, context: str | None = None) -> Morphology:
        """Returns lemma from simplemma + POS/morph from GR-NLP-TOOLKIT when
        available. Caller can tolerate empty features."""
        cand = self.lemmatize(surface, context)
        try:
            pipeline = self._ensure_gr_pipeline()
            doc = pipeline(context or surface)
            target = next((t for t in doc.tokens
                           if t.text == surface.lower()), None)
            if target is None and doc.tokens:
                target = doc.tokens[0]
            feats = {}
            if target is not None:
                feats_obj = getattr(target, "feats", {}) or {}
                if isinstance(feats_obj, dict):
                    feats = {k: str(v) for k, v in feats_obj.items() if v not in (None, "_")}
            return Morphology(
                surface=surface,
                lemma=cand.lemma,
                pos=getattr(target, "upos", None) if target else None,
                features=feats,
            )
        except ProviderUnavailable:
            return Morphology(surface=surface, lemma=cand.lemma)

    def normalize_bare(self, form: str) -> str:
        return _strip_accents_monotonic(form)
