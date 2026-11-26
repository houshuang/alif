"""Token classification in scripts/bookify_arabic.py."""
from scripts import bookify_arabic as bk


def test_guessed_match_is_no_match_and_vowels_reach_the_resolver(monkeypatch):
    seen = []

    def fake_lookup(surface, lookup, provenance=None, strict_fallback=False):
        seen.append(surface)
        if provenance is not None:
            provenance.layer = "camel" if surface.startswith("وِزَ") else "exact"
        return 7

    monkeypatch.setattr(bk, "lookup_lemma_id", fake_lookup)
    meta = {7: {"lemma_ar": "زَارَ", "gloss_en": "to visit"}}
    out = bk.classify_token("وِزَارَةُ،", {}, meta, {}, set(), set())
    assert out["kind"] == "unknown_to_alif"
    assert seen[-1] == "وِزَارَةُ"  # punctuation gone, vowels kept
    out = bk.classify_token("زَارَ", {}, meta, {}, set(), set())
    assert out.get("lemma_id") == 7
