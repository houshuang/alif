"""Latin provider: normalization, enclitic handling, LatinCy lemmatization.

Fast tests cover the deterministic bits (normalize_bare, enclitic strip,
tokenize fidelity, simplemma fallback). The `slow`-marked tests exercise the
real LatinCy model and only run under `pytest -m slow`.
"""
import pytest

from app.services.languages.base import ProviderUnavailable
from app.services.languages.la import LatinProvider, _to_modern_reading_orthography


def test_normalize_bare_strips_macrons_and_folds_j_v():
    p = LatinProvider()
    assert p.normalize_bare("vīta") == "uita"
    assert p.normalize_bare("Jūlius") == "iulius"
    assert p.normalize_bare("amō") == "amo"
    assert p.normalize_bare("VĒNĪ") == "ueni"
    # v→u/j→i reconcile a v-spelled seed lemma with LatinCy's u/i output
    assert p.normalize_bare("venio") == p.normalize_bare("uenio")


# ─── modern-reading display transformer (2026-05-26 convention flip) ─────────


@pytest.mark.parametrize("src,want", [
    # Word-initial u before vowel → v
    ("uir", "vir"),
    ("uocabulum", "vocabulum"),
    ("uita", "vita"),
    ("uolo", "volo"),
    ("uenio", "venio"),
    # Intervocalic u → v
    ("nouus", "novus"),
    ("cauere", "cavere"),
    ("nouitas", "novitas"),
    ("iuuenis", "iuvenis"),  # first `i` stays vocalic; second `u` (intervocalic) → v
    ("iuuat", "iuvat"),
    # Fixpoint cases: word-initial u→v then the middle i loses its consonantal
    # context, so the second u flips on a second pass (`uiuo`→`viuo`→`vivo`).
    ("uiuo", "vivo"),
    ("uiuus", "vivus"),
    ("uiua", "viva"),
    # i is NEVER transformed to j — convention picked 2026-05-26 is u/v but
    # NOT i/j (matches LLPSI / Roma Aeterna / OUP intermediate convention).
    ("iam", "iam"),
    ("iulius", "iulius"),
    ("maior", "maior"),
    ("peior", "peior"),
    # qu / gu / su digraph exceptions: u after q/g/s + vowel stays u.
    ("aqua", "aqua"),
    ("aquila", "aquila"),
    ("quum", "quum"),
    ("lingua", "lingua"),
    ("pinguis", "pinguis"),
    ("suadeo", "suadeo"),
    ("suauis", "suavis"),  # first u stays (s-digraph); second is intervocalic → v
    # Conservative: post-consonant u (not q/g/s) NOT transformed even if
    # classically `v` (servus/silva). The heuristic can't distinguish the
    # consonantal case from a vocalic syllable-nucleus u in the same position.
    ("puer", "puer"),
    ("puella", "puella"),
    ("culpa", "culpa"),
    ("multus", "multus"),
    ("pensum", "pensum"),
    # Already-correct v inputs pass through unchanged (idempotent)
    ("vir", "vir"),
    ("vocabulum", "vocabulum"),
    ("iuvenis", "iuvenis"),
    # Case preserved
    ("Iulius", "Iulius"),
    ("Iuuenis", "Iuvenis"),
    ("Uir", "Vir"),
])
def test_modern_reading_orthography(src, want):
    assert _to_modern_reading_orthography(src) == want


def test_lemmatize_splits_display_and_lookup_key(monkeypatch):
    """Reading-intake path: simplemma (or LatinCy) returns a u-spelled lemma.
    The candidate must carry the v-spelled display form AND the u-folded lookup key,
    so DB writes get the modern-reading spelling while matching still works.

    Direct stub on simplemma — the real Latin table doesn't reduce many forms
    (it's a graceful fallback, not a primary), so probing it for an
    intervocalic-u inflection like `noua`→`nouus` isn't reliable. We monkeypatch
    a known u-spelled return to assert the split."""
    p = LatinProvider()

    def _raise():
        raise ProviderUnavailable("no model")

    monkeypatch.setattr(p, "_ensure_latincy", _raise)

    import simplemma as _sm
    monkeypatch.setattr(_sm, "lemmatize", lambda surface, lang, greedy: "uocabulum")

    cand = p.lemmatize("uocabulorum")
    # Lookup key stays u-folded regardless of display convention
    assert cand.lemma_bare == "uocabulum"
    # Display form follows modern reading orthography
    assert cand.lemma == "vocabulum"


def test_tokenize_keeps_tokens_whole():
    p = LatinProvider()
    toks = [t.surface for t in p.tokenize("Senatus populusque Romanus est.")]
    # Enclitics are NOT suffix-split (display fidelity; -que handled by LatinCy,
    # rare -ne/-ve fused forms left to the LLM gate).
    assert "populusque" in toks
    assert toks[-1] == "."


def test_lemmatize_falls_back_to_simplemma_without_model(monkeypatch):
    """When the LatinCy model isn't installed, lemmatize must degrade to
    simplemma (read-and-mark keeps working), not crash. simplemma is unreliable
    for Latin (it mangles e.g. puella→puellus), which is exactly why LatinCy is
    the primary — here we only assert the fallback path runs and carries no POS.
    For `puella` specifically, display and bare happen to be equal (no consonantal
    u positions), but in general they can differ now (2026-05-26 u/v flip)."""
    p = LatinProvider()

    def _raise():
        raise ProviderUnavailable("no model")

    monkeypatch.setattr(p, "_ensure_latincy", _raise)
    cand = p.lemmatize("puella")
    assert cand.lemma_bare and cand.lemma_bare == cand.lemma_bare.lower()
    # simplemma mangles puella → puellus (textbook example of why LatinCy is
    # primary). Whatever simplemma returns, neither form contains a consonantal
    # u/i position, so display and bare must still agree here.
    assert cand.lemma == cand.lemma_bare
    assert cand.pos is None  # simplemma path carries no POS


@pytest.mark.slow
def test_latincy_core_lemmas():
    p = LatinProvider()
    assert p.lemmatize("est", "Marcus est puer.").lemma_bare == "sum"
    assert p.lemmatize("librum", "Puer librum legit.").lemma_bare == "liber"
    assert p.lemmatize("Romam", "Romulus Romam condidit.").lemma_bare == "roma"
    # deponent verb (hard — passive form, active sense)
    assert p.lemmatize("secuti", "Hostes secuti sunt.").lemma_bare == "sequor"
    # -que enclitic: LatinCy splits it natively; we return the content stem
    assert p.lemmatize("populusque", "Senatus populusque Romanus.").lemma_bare == "populus"


@pytest.mark.slow
def test_latin_sentence_validates_via_latincy():
    """The deterministic generation validator works for Latin with no
    surface-expansion crutch: inflected scaffold words (librum, legit) reduce to
    their lemmas (liber, lego) through LatinCy, matching the known-bare set.
    Proves task #5 needs only the function-word/scaffold lists, not a Latin
    analogue of _el_surface_bares_for_lemma."""
    from app.services.sentence_validator import validate_sentence
    from app.services.lemma_quality import FUNCTION_WORD_SETS

    res = validate_sentence(
        "Puer librum legit.",
        target_bare="liber",
        known_bare_forms={"puer", "lego"},
        function_word_bares=FUNCTION_WORD_SETS["la"],
        language_code="la",
    )
    assert res.valid, res.issues
    assert res.target_present


@pytest.mark.slow
def test_latincy_cum_pos_disambiguation():
    """LatinCy resolves the cum preposition vs subordinator by context — the
    kind of disambiguation simplemma cannot do."""
    p = LatinProvider()
    assert p.analyze("Cum", "Cum amicis venit.").pos == "ADP"
    assert p.analyze("Cum", "Cum venisset, risit.").pos == "SCONJ"
