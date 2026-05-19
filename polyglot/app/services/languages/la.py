"""Latin NLP provider — stub. Will use LatinCy (Patrick Burns 2023; POS 97.4%,
lemma 94.7%, morph 92.8%). Install: `pip install spacy` plus a LatinCy model
from HuggingFace (e.g. `la_core_web_lg`).

Macrons: stripped for `normalize_bare` lookup but preserved on `lemma_form`
display, mirroring the accent-handling pattern. `j → i` and `v → u` Classical
normalization also folded for lookup.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from app.services.languages.base import (
    ProviderUnavailable, Token, LemmaCandidate, Morphology,
)

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-zĀāĒēĪīŌōŪūȲȳÀ-ɏ]+|[^\w\s]", re.UNICODE)


def _normalize_latin(form: str) -> str:
    """Strip macrons, fold j→i and v→u (Classical orthographic convention)."""
    decomposed = unicodedata.normalize("NFD", form)
    no_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    folded = no_marks.lower().replace("j", "i").replace("v", "u")
    return unicodedata.normalize("NFC", folded)


class LatinProvider:
    """Latin lemmatization via simplemma (works well for Classical Latin).
    LatinCy/spaCy can be wired in later for POS/morphology and macron-aware
    lemmatization — until then, simplemma is enough for read-and-mark.
    """
    code = "la"
    display_name = "Latin"

    def __init__(self):
        self._simplemma_ok = False
        self._tried_simplemma = False

    def _ensure_simplemma(self):
        if self._tried_simplemma:
            if not self._simplemma_ok:
                raise ProviderUnavailable("simplemma not installed")
            return
        self._tried_simplemma = True
        try:
            import simplemma  # noqa: F401
            self._simplemma_ok = True
        except ImportError as e:
            raise ProviderUnavailable("simplemma not installed") from e

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
        lemma = simplemma.lemmatize(surface, lang="la")
        confidence = 1.0 if lemma != surface else 0.5
        return LemmaCandidate(
            lemma=lemma,
            lemma_bare=self.normalize_bare(lemma),
            pos=None,
            confidence=confidence,
        )

    def analyze(self, surface: str, context: str | None = None) -> Morphology:
        cand = self.lemmatize(surface, context)
        return Morphology(surface=surface, lemma=cand.lemma)

    def normalize_bare(self, form: str) -> str:
        return _normalize_latin(form)
