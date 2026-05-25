"""Latin provider: normalization, enclitic handling, LatinCy lemmatization.

Fast tests cover the deterministic bits (normalize_bare, enclitic strip,
tokenize fidelity, simplemma fallback). The `slow`-marked tests exercise the
real LatinCy model and only run under `pytest -m slow`.
"""
import pytest

from app.services.languages.base import ProviderUnavailable
from app.services.languages.la import LatinProvider


def test_normalize_bare_strips_macrons_and_folds_j_v():
    p = LatinProvider()
    assert p.normalize_bare("vīta") == "uita"
    assert p.normalize_bare("Jūlius") == "iulius"
    assert p.normalize_bare("amō") == "amo"
    assert p.normalize_bare("VĒNĪ") == "ueni"
    # v→u/j→i reconcile a v/j-spelled seed lemma with LatinCy's u/i output
    assert p.normalize_bare("venio") == p.normalize_bare("uenio")


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
    the primary — here we only assert the fallback path runs and carries no POS."""
    p = LatinProvider()

    def _raise():
        raise ProviderUnavailable("no model")

    monkeypatch.setattr(p, "_ensure_latincy", _raise)
    cand = p.lemmatize("puella")
    assert cand.lemma_bare and cand.lemma_bare == cand.lemma_bare.lower()
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
def test_latincy_cum_pos_disambiguation():
    """LatinCy resolves the cum preposition vs subordinator by context — the
    kind of disambiguation simplemma cannot do."""
    p = LatinProvider()
    assert p.analyze("Cum", "Cum amicis venit.").pos == "ADP"
    assert p.analyze("Cum", "Cum venisset, risit.").pos == "SCONJ"
