"""Latin NLP provider.

Primary lemmatizer: **LatinCy** (Patrick Burns 2023) `la_core_web_lg` — a spaCy
pipeline trained on the five Latin UD treebanks + LASLA. Empirically (2026-05-25
probe over homographs / enclitics / macrons / proper nouns / Eutropius prose) it
is solid as a primary but NOT to be trusted raw — it has the same class of
failure modes simplemma had for Greek, so it sits behind the same safety net
(`lemma_integrity` citation repair + `lemma_quality` sentence-context gate):

  - **Sentence-initial capitalization → false PROPN.** ``Malus``/``Uita``/
    ``Liber``/``Os`` mid-sentence tag correctly, but as the first (capitalized)
    word of a sentence they flip to PROPN. Every sentence's first word is
    capitalized, so the gate must catch this (it already does for Greek
    ``Τίγρης``→``τίγρη``). We pass full-sentence ``context`` so only the genuine
    sentence-initial position is at risk, not every mid-sentence token.
  - **Homographs** (``malum`` apple/evil vs ``malus`` bad) still flip; LatinCy's
    context POS resolves many (``cum`` prep/SCONJ, ``modo`` adv/noun) but not all.
  - **``-ne`` / ``-ve`` enclitics don't split** (only ``-que`` does — verified in
    the probe: ``populusque``→``populus``+``que`` but ``estne``→junk lemma
    ``estne``). We deliberately do NOT suffix-strip these: a ``-ne`` strip can't
    be told apart from 3rd-declension ablatives in ``-ne`` (``homine``,
    ``ordine``, ``ratione``), which it would mangle, and ``-ue`` collides with
    ``-que``. The rare fused ``estne``/``-ve`` form is left to the LLM gate,
    exactly as Greek leaves σε-crasis to it.
  - **u/i orthography**: LatinCy emits lemmas in classical u/i form (``uenio``,
    ``uir``). ``normalize_bare`` folds v→u and j→i, so the lookup key reconciles
    with v/j-spelled seed vocab (DCC / LLPSI). Display form for seeded lemmas
    comes from the authoritative list; novel reading-intake lemmas keep LatinCy's
    u/i form.

simplemma (lang='la') is the graceful fallback when the LatinCy model isn't
installed — it keeps read-and-mark working, just without POS/morph or context
disambiguation. Macrons stripped for ``normalize_bare`` lookup, preserved on the
display form.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata

from app.services.languages.base import (
    ProviderUnavailable, Token, LemmaCandidate, Morphology,
)

log = logging.getLogger(__name__)

# Latin letters incl. macron-marked vowels. Punctuation each becomes its own
# token (mirrors el.py).
_TOKEN_RE = re.compile(r"[A-Za-zĀāĒēĪīŌōŪūȲȳÀ-ɏ]+|[^\w\s]", re.UNICODE)

_DEFAULT_MODEL = os.environ.get("POLYGLOT_LATINCY_MODEL", "la_core_web_lg")


def _normalize_latin(form: str) -> str:
    """Strip macrons, fold j→i and v→u (classical orthographic convention).

    This is the lookup key. It deliberately matches LatinCy's lemma
    orthography (u/i), so a v/j-spelled seed lemma and a LatinCy-emitted lemma
    collapse to the same key.
    """
    decomposed = unicodedata.normalize("NFD", form)
    no_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    folded = no_marks.lower().replace("j", "i").replace("v", "u")
    return unicodedata.normalize("NFC", folded)


# UPOS tags LatinCy assigns to closed-class / non-content tokens. Exposed via
# the candidate's ``pos`` so downstream (reading intake, quality gate) can lean
# on morphology rather than only the bare-form function-word set.
FUNCTION_UPOS = frozenset({"ADP", "CCONJ", "SCONJ", "DET", "PRON", "PART", "AUX"})


class LatinProvider:
    code = "la"
    display_name = "Latin"
    _SIMPLEMMA_LANG = "la"

    def __init__(self):
        self._nlp = None
        self._tried_latincy = False
        self._tried_simplemma = False
        self._simplemma_ok = False

    # ─── backends ─────────────────────────────────────────────────────────

    def _ensure_latincy(self):
        """Load the LatinCy spaCy pipeline lazily. Heavy (spaCy + model), so
        only loaded when first requested. Failure is non-fatal — callers fall
        back to simplemma."""
        if self._nlp is not None:
            return self._nlp
        if self._tried_latincy:
            raise ProviderUnavailable("latincy model failed to load (see earlier log)")
        self._tried_latincy = True
        try:
            import spacy
            self._nlp = spacy.load(_DEFAULT_MODEL)
            log.info("Latin: LatinCy pipeline '%s' loaded", _DEFAULT_MODEL)
            return self._nlp
        except Exception as e:  # ImportError, OSError (model missing), etc.
            log.warning("Latin: LatinCy load failed (%s); falling back to simplemma", e)
            raise ProviderUnavailable(f"latincy load failed: {e}") from e

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

    # ─── NLPProvider interface ────────────────────────────────────────────

    def tokenize(self, text: str) -> list[Token]:
        """Whitespace/punctuation tokenizer. Tokens are kept WHOLE (enclitics
        not split) so the reading view matches the printed text; the enclitic
        is folded into the content lemma at lemmatization time instead."""
        out: list[Token] = []
        pos = 0
        for m in _TOKEN_RE.finditer(text):
            tok = m.group()
            is_punct = not any(c.isalpha() for c in tok)
            out.append(Token(surface=tok, position=pos, is_punctuation=is_punct))
            pos += 1
        return out

    def _analyze_token(self, surface: str, context: str | None):
        """Return (lemma, pos, feats) from LatinCy, using ``context`` for
        disambiguation. Matches the surface token within the parsed context."""
        nlp = self._ensure_latincy()
        doc = nlp(context or surface)
        target_bare = _normalize_latin(surface)
        match = None
        for t in doc:
            if t.is_space or t.is_punct:
                continue
            if _normalize_latin(t.text) == target_bare:
                match = t
                break
        if match is None:
            # surface not found in context (e.g. LatinCy split -que) —
            # re-parse the surface alone so we still return a real lemma.
            doc2 = nlp(surface)
            for t in doc2:
                if not (t.is_space or t.is_punct):
                    match = t
                    break
        if match is None:
            return surface, None, {}
        feats = {k: v for k, v in (match.morph.to_dict() or {}).items() if v}
        return match.lemma_, match.pos_, feats

    def lemmatize(self, surface: str, context: str | None = None) -> LemmaCandidate:
        try:
            lemma, pos, _ = self._analyze_token(surface, context)
            confidence = 1.0 if _normalize_latin(lemma) != _normalize_latin(surface) else 0.7
            return LemmaCandidate(
                lemma=lemma,
                lemma_bare=_normalize_latin(lemma),
                pos=pos,
                confidence=confidence,
            )
        except ProviderUnavailable:
            pass
        # Fallback: simplemma (no POS, no context).
        try:
            self._ensure_simplemma()
            import simplemma
            lemma = simplemma.lemmatize(surface, lang=self._SIMPLEMMA_LANG, greedy=True)
            confidence = 1.0 if lemma.lower() != surface.lower() else 0.5
            return LemmaCandidate(lemma=lemma, lemma_bare=_normalize_latin(lemma),
                                  pos=None, confidence=confidence)
        except ProviderUnavailable:
            return LemmaCandidate(lemma=surface, lemma_bare=_normalize_latin(surface),
                                  pos=None, confidence=0.0)

    def analyze(self, surface: str, context: str | None = None) -> Morphology:
        try:
            lemma, pos, feats = self._analyze_token(surface, context)
            return Morphology(surface=surface, lemma=lemma, pos=pos, features=feats)
        except ProviderUnavailable:
            cand = self.lemmatize(surface, context)
            return Morphology(surface=surface, lemma=cand.lemma, pos=cand.pos)

    def normalize_bare(self, form: str) -> str:
        return _normalize_latin(form)
