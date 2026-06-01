"""Run-on gloss heuristic. Documents what the LLM-judge sees as a candidate
so the filter doesn't silently widen or narrow on refactor."""
from scripts.repair_runon_glosses import _looks_runon


def test_runon_candidates_marked_true():
    # Real prod examples from the 2026-05-26 audit
    assert _looks_runon("demolition setting of the sun")        # excidium
    assert _looks_runon("beginning introduction foundation")    # exordium
    assert _looks_runon("assistant attendant")                  # administer
    assert _looks_runon("copper bronze")                        # aeneus (2-word)
    assert _looks_runon("anxious unshorn")                      # anxius  (2-word)
    assert _looks_runon("adultery adulteration")                # adulterium


def test_multi_verb_runons_marked_true():
    # The DCC Latin seed fused first-person verb senses with no separator.
    # These start with "I " so the phrase-prefix skip used to hide the whole
    # class (found live 2026-06-01: 265 Latin lemmas, 23 of them being studied).
    assert _looks_runon("I surround I circle")          # cingo
    assert _looks_runon("I feed I pasture I consume")   # pasco
    assert _looks_runon("I am useful I am good")        # prosum
    assert _looks_runon("I leap I spring forth I mount for copulation")  # salio


def test_legitimate_definitions_marked_false():
    # The filter is intentionally permissive; the LLM is the final arbiter.
    # Whatever can be CHEAPLY ruled out here saves an LLM call.

    # Comma separators present → already correct
    assert not _looks_runon("almost, about, nearly")
    # Semicolon separators present → already correct
    assert not _looks_runon("hut; cottage")
    # Period → likely a sentence-form definition
    assert not _looks_runon("A small bird that sings.")
    # Verb-phrase prefix → infinitive / 1sg form, single sense
    assert not _looks_runon("to bear, carry")
    assert not _looks_runon("I stand firm")
    # Article prefix → relative-clause definition, usually single sense
    assert not _looks_runon("the king")
    assert not _looks_runon("the body of a horse")
    # Numbered list → already structured
    assert not _looks_runon("1. strict 2. a poor man")
    # Parens or quotes — already formatted in some way
    assert not _looks_runon("(of) copper bronze")
    assert not _looks_runon("'almost' nearly very")


def test_disjunctive_falls_through_to_llm_judge():
    # "senate or parliament" reads as one disjunctive sense to a human, but
    # the heuristic can't tell that cheaply from "demolition setting of the
    # sun". Let it through — the LLM marks it "ok" and the apply step is a
    # no-op. This test pins the documented behavior so a future refactor
    # doesn't get clever and start filtering out real run-ons.
    assert _looks_runon("senate or parliament")


def test_short_glosses_skipped():
    assert not _looks_runon("king")
    assert not _looks_runon("any")


def test_empty_or_none_safe():
    assert not _looks_runon(None)
    assert not _looks_runon("")
    assert not _looks_runon("   ")


