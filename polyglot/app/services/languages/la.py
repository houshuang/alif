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
  - **u/v orthography (display) vs u-folded (lookup)**: LatinCy emits lemmas
    in classical u/i form (``uenio``, ``uir``). ``normalize_bare`` folds v→u
    and j→i for the lookup key, so seed vocab, LatinCy output, and modern
    v-spelled text all collapse to one key. The DISPLAY form, however, is
    modern reading orthography (distinguish u/v but NOT i/j — the LLPSI / OUP
    intermediate convention) since 2026-05-26: ``_to_modern_reading_orthography``
    flips LatinCy's u output to v on the way out (does NOT touch i — see the
    rule docstring). Seed lemmas bypass the heuristic at the importer (LLPSI
    and Roma Aeterna TSVs carry their v-spelling natively); reading-intake
    novel lemmas rely on the heuristic.

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

    This is the LOOKUP KEY — deliberately convention-agnostic. It matches
    LatinCy's lemma orthography (u/i), so a v-spelled seed lemma, a macron-
    bearing Roma Aeterna entry, and a LatinCy-emitted lemma all collapse to
    the same key. This MUST NOT change when the display convention changes
    (it's been u-folded since 2026-05-25 and stays that way regardless of
    which way the display flips — currently u/v distinguished, no i/j).
    """
    decomposed = unicodedata.normalize("NFD", form)
    no_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    folded = no_marks.lower().replace("j", "i").replace("v", "u")
    return unicodedata.normalize("NFC", folded)


# Vowels for the consonantal-u/i detector. Macron-stripped before checking so we
# don't have to enumerate `ā ē ī ō ū`.
_VOWELS = frozenset("aeiouy")

# Known exceptions where the conservative heuristic would either over-apply or
# under-apply. Add a row when reading-intake produces a misspelled novel lemma
# and the correct form is verified in a Latin dictionary. Keep small.
_DISPLAY_OVERRIDES: dict[str, str] = {}


# ── Homograph lemma overrides (2026-06-01) ──────────────────────────────────
# LatinCy confidently assigns a CITATION lemma that contradicts the morphology
# it tagged on the same token: e.g. `pilum` comes back lemma=`pilus` (a
# *masculine* 2nd-decl noun = "hair") while the token is tagged Gender=Neut —
# but the neuter form is `pilum` = "javelin". Likewise `tantum` lemma=`tantus`
# (an adjective) while tagged pos=ADV (the adverb is `tantum`). These are flat
# model errors, NOT genuine ambiguity, so they recur in every context and the
# sentence-context verifier doesn't reliably catch them (a wrong-sense mapping
# to a valid lemma looks structurally fine). We correct them deterministically
# at the lemmatizer boundary — the single chokepoint every path flows through
# (reading intake, generation validator, on-demand tap lookup).
#
# The override keys on LatinCy's OWN tag (Gender or pos), which is reliable here
# even when its lemma is not, so the fix is correct in all contexts: a genuine
# masc-acc `pilum` (= a hair) is tagged Gender=Masc and left as `pilus`. This is
# why `pila` (fem ball vs neut-pl javelins) is deliberately ABSENT — LatinCy
# disambiguates it correctly by context and it carries no lemma/tag contradiction.
#
# Each entry: surface bare form -> ordered list of (feature, expected, lemma).
# `feature` is "Gender" (matched against morph feats) or "pos" (UPOS tag).
# First matching rule wins; no match leaves LatinCy's lemma untouched. The
# corrected lemma string flows through the normal display/bare pipeline, so it
# must be a real lemma_bare present in the vocabulary.
_LEMMA_OVERRIDES: dict[str, list[tuple[str, str, str]]] = {
    "pilum":  [("Gender", "Neut", "pilum")],   # neuter javelin, not masc pilus/hair
    "tantum": [("pos", "ADV", "tantum")],       # adverb "only", not adjective tantus
    "malum":  [("pos", "NOUN", "malum")],       # noun evil/apple, not adjective malus
    "solum":  [("pos", "NOUN", "solum")],       # noun ground/soil, not adjective solus
}


def override_surface_keys() -> frozenset[str]:
    """Normalized-bare surface forms that carry a homograph override. The
    generation pipeline uses this to refuse PR #165 observed-surface ingestion
    of these surfaces (their stored mappings are exactly the ones the override
    exists to distrust), so a corrected mapping can't be re-polluted."""
    return frozenset(_LEMMA_OVERRIDES)


def lemma_override(surface: str, pos: str | None, feats: dict | None) -> str | None:
    """Return the corrected lemma for a known LatinCy homograph confusion, or
    None when no override applies. Keyed on the normalized-bare surface and
    discriminated by LatinCy's own pos/Gender tag. Pure — exported so the
    generation pipeline can guard PR #165 observed-surface augmentation against
    re-ingesting a mapping this override would flip."""
    rules = _LEMMA_OVERRIDES.get(_normalize_latin(surface))
    if not rules:
        return None
    feats = feats or {}
    for feature, expected, correct in rules:
        actual = pos if feature == "pos" else feats.get(feature)
        if actual == expected:
            return correct
    return None


def _to_modern_reading_orthography(form: str) -> str:
    """Convert a u-spelled Latin form to modern reading orthography (v).

    Used as the DISPLAY transformer in `lemmatize` so LatinCy's u-spelled
    output (`uir`, `uocabulum`, `iuuenis`) renders as `vir`, `vocabulum`,
    `iuvenis`. The lookup key stays u-folded via `_normalize_latin`, so
    matching is unaffected.

    Convention (2026-05-26, picked after a small literature/survey audit):
    distinguish `u`/`v` but NOT `i`/`j` — the dominant modern editorial
    practice (per Wikipedia "Latin phonology and orthography" and the 2010
    Textkit survey of 251 readers), and exactly what LLPSI / Roma Aeterna
    themselves use. So `iuvenis` stays `iuvenis`, NOT `juvenis`; `Iulius`
    stays `Iulius`, NOT `Julius`. The plan's text mentioned j-spelling as an
    option but the user picked v-only after seeing the survey data.

    Rule (one pass, u→v only, conservative):
      - Word-initial `u` before a vowel → `v` (uir → vir, uolo → volo)
      - Intervocalic `u` (vowel-vowel) → `v` (nouus → novus, cauere → cavere)
      - Otherwise `u` stays (puer, culpa, multus, seruus, silua all unchanged)

    Post-consonant cases like `seruus`/`silua` deliberately don't transform
    even though they ARE consonantal in modern editions. The same shape is
    vocalic in words like a syllable-nucleus `u` after a consonant cluster,
    and the heuristic can't distinguish them syllabically. Seed lemmas
    (LLPSI, Roma Aeterna, DCC) carry their v-spelling natively, so the
    heuristic only affects novel reading-intake lemmas — rare, and the
    override dict above catches any that slip through.

    Exceptions per Wikipedia's modern-edition convention:
      - `qu` digraph: u after q stays (aqua, qui, quum)
      - `gu` digraph: u after g + vowel stays (lingua, pinguis)
      - `su` digraph: u after s + vowel stays (suadeo, suavis)
    """
    if form in _DISPLAY_OVERRIDES:
        return _DISPLAY_OVERRIDES[form]

    def _is_vowel(ch: str) -> bool:
        if not ch:
            return False
        # Decompose to strip macrons so ā/ē/ī/ō/ū all count.
        bare = "".join(c for c in unicodedata.normalize("NFD", ch)
                       if unicodedata.category(c) != "Mn")
        return bare.lower() in _VOWELS

    def _is_consonantal_i(chars: list[str], pos: int) -> bool:
        """An `i` is in consonantal (j-sound) position when it's word-initial
        OR intervocalic AND followed by a vowel. We still WRITE it as `i`
        (modern convention is u/v but not i/j), but for the *u-classification*
        below it must count as a consonant — otherwise `iuuenis` is incorrectly
        scored as `i-u-u` (vowel-vowel) and the first u flips, giving
        `ivvenis` instead of `iuvenis`."""
        if pos < 0 or pos >= len(chars) or chars[pos].lower() != "i":
            return False
        if pos + 1 >= len(chars) or not _is_vowel(chars[pos + 1]):
            return False
        word_initial = (pos == 0)
        intervocalic = (pos > 0 and _is_vowel(chars[pos - 1]))
        return word_initial or intervocalic

    def _counts_as_vowel(chars: list[str], pos: int) -> bool:
        """Vowel for u-classification: any vowel char EXCEPT an `i` standing
        in consonantal position."""
        if not _is_vowel(chars[pos]):
            return False
        if chars[pos].lower() == "i" and _is_consonantal_i(chars, pos):
            return False
        return True

    def _one_pass(chars: list[str]) -> list[str]:
        out = list(chars)
        for i, ch in enumerate(chars):
            if ch.lower() != "u":
                continue
            if i + 1 >= len(chars):
                continue
            if not _is_vowel(chars[i + 1]):
                continue
            # Digraph exceptions: q/g/s + u + vowel keeps the u (these clusters
            # were historically [kw]/[gw]/[sw] and editors still write them
            # u-spelled even in v-distinguishing editions).
            if i > 0 and chars[i - 1].lower() in ("q", "g", "s"):
                continue
            word_initial = (i == 0)
            intervocalic = (i > 0 and _counts_as_vowel(chars, i - 1))
            if not (word_initial or intervocalic):
                continue
            out[i] = "v" if ch.islower() else "V"
        return out

    # Iterate to fixpoint. `uiuo` → `viuo` after one pass (the second `u` is
    # blocked because the middle `i` looks intervocalic between two real
    # vowels), then `viuo` → `vivo` on the second pass because the leading
    # `u` is now a `v` (consonant) so the middle `i` is no longer
    # intervocalic and the second `u` is correctly seen as intervocalic with
    # the vocalic `i`. Capped at 4 iterations as a paranoia bound; in
    # practice 1 or 2 passes always suffice.
    chars = list(form)
    for _ in range(4):
        nxt = _one_pass(chars)
        if nxt == chars:
            break
        chars = nxt
    return "".join(chars)


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
        lemma = lemma_override(surface, match.pos_, feats) or match.lemma_
        return lemma, match.pos_, feats

    def lemmatize(self, surface: str, context: str | None = None) -> LemmaCandidate:
        # Latin display policy (2026-05-26): display form is modern reading
        # orthography (u/v distinguished, NO i/j), the lookup key
        # (`lemma_bare`) stays u-folded. `_to_modern_reading_orthography`
        # flips LatinCy's u-spelled output (uir/uocabulum/iuuenis) to
        # vir/vocabulum/iuvenis for storage; matching never breaks because
        # `_normalize_latin` collapses both back to the same key. Seed lemmas
        # (LLPSI / Roma Aeterna) bypass this transformer at the importer
        # (they're natively v-spelled) — see import_latin_vocab.
        try:
            lemma, pos, _ = self._analyze_token(surface, context)
            display = _to_modern_reading_orthography(lemma)
            bare = _normalize_latin(lemma)
            confidence = 1.0 if bare != _normalize_latin(surface) else 0.7
            return LemmaCandidate(lemma=display, lemma_bare=bare, pos=pos, confidence=confidence)
        except ProviderUnavailable:
            pass
        # Fallback: simplemma (no POS, no context).
        try:
            self._ensure_simplemma()
            import simplemma
            lemma = simplemma.lemmatize(surface, lang=self._SIMPLEMMA_LANG, greedy=True)
            display = _to_modern_reading_orthography(lemma)
            bare = _normalize_latin(lemma)
            confidence = 1.0 if lemma.lower() != surface.lower() else 0.5
            return LemmaCandidate(lemma=display, lemma_bare=bare, pos=None, confidence=confidence)
        except ProviderUnavailable:
            display = _to_modern_reading_orthography(surface)
            bare = _normalize_latin(surface)
            return LemmaCandidate(lemma=display, lemma_bare=bare, pos=None, confidence=0.0)

    def analyze(self, surface: str, context: str | None = None) -> Morphology:
        try:
            lemma, pos, feats = self._analyze_token(surface, context)
            return Morphology(surface=surface, lemma=lemma, pos=pos, features=feats)
        except ProviderUnavailable:
            cand = self.lemmatize(surface, context)
            return Morphology(surface=surface, lemma=cand.lemma, pos=cand.pos)

    def normalize_bare(self, form: str) -> str:
        return _normalize_latin(form)
