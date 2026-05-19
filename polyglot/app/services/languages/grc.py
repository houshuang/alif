"""Ancient Greek NLP provider — stub. Will use OdyCy (spaCy-based, 2023; 94.4%
lemmatization on UD-PROIEL, 83.2% Perseus). Install: `pip install spacy` plus
the OdyCy model from HuggingFace.

For now this returns degraded results (raw surface as lemma) so the rest of
the app can be built without the model installed.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from app.services.languages.base import (
    ProviderUnavailable, Token, LemmaCandidate, Morphology,
)

log = logging.getLogger(__name__)

# Greek block (Modern) + Greek Extended block (polytonic)
_TOKEN_RE = re.compile(r"[Ͱ-Ͽἀ-῿']+|[^\w\s]", re.UNICODE)


def _strip_accents_polytonic(form: str) -> str:
    """Strip ALL combining marks (acute/grave/circumflex/breathings/iota
    subscript) so accent-agnostic lookup works."""
    decomposed = unicodedata.normalize("NFD", form)
    no_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    # Final sigma normalization: ς → σ for lookup
    no_marks = no_marks.replace("ς", "σ")
    return unicodedata.normalize("NFC", no_marks).lower()


class AncientGreekProvider:
    code = "grc"
    display_name = "Ancient Greek"

    def __init__(self):
        self._nlp = None
        self._tried_load = False

    def _ensure_pipeline(self):
        if self._nlp is not None:
            return self._nlp
        if self._tried_load:
            raise ProviderUnavailable("OdyCy failed to load (see earlier log)")
        self._tried_load = True
        try:
            import spacy
            # Try OdyCy first (best quality), fall back to greCy
            for model in ("grc_odycy_joint_trf", "grc_odycy_joint_sm", "grc_proiel_trf"):
                try:
                    self._nlp = spacy.load(model)
                    log.info("Ancient Greek: spaCy model %s loaded", model)
                    return self._nlp
                except OSError:
                    continue
            raise ProviderUnavailable(
                "No Ancient Greek spaCy model installed. Try: "
                "pip install https://huggingface.co/chcaa/grc_odycy_joint_trf/resolve/main/grc_odycy_joint_trf-any-py3-none-any.whl"
            )
        except ImportError as e:
            raise ProviderUnavailable("spacy not installed") from e

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
        nlp = self._ensure_pipeline()
        doc = nlp(context or surface)
        target = next((t for t in doc if t.text == surface), None)
        if target is None and len(doc) > 0:
            target = doc[0]
        if target is None:
            return LemmaCandidate(lemma=surface, lemma_bare=self.normalize_bare(surface), confidence=0.0)
        return LemmaCandidate(
            lemma=target.lemma_ or surface,
            lemma_bare=self.normalize_bare(target.lemma_ or surface),
            pos=target.pos_,
            confidence=1.0,
        )

    def analyze(self, surface: str, context: str | None = None) -> Morphology:
        nlp = self._ensure_pipeline()
        doc = nlp(context or surface)
        target = next((t for t in doc if t.text == surface), None)
        if target is None and len(doc) > 0:
            target = doc[0]
        if target is None:
            return Morphology(surface=surface, lemma=surface)
        feats = {}
        if target.morph:
            for part in str(target.morph).split("|"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    feats[k] = v
        return Morphology(surface=surface, lemma=target.lemma_ or surface, pos=target.pos_, features=feats)

    def normalize_bare(self, form: str) -> str:
        return _strip_accents_polytonic(form)
