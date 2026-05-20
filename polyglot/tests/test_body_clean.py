"""Tests for the LLM body-text cleaner.

CLI subprocess is mocked everywhere except the explicit `slow` test which
hits a real Claude CLI. The shape of the mock matches what
`lemma_quality._call_claude` expects from `claude -p --output-format json
--json-schema`: a `structured_output` dict at the top level (or a JSON-string
`result` field as the legacy fallback).
"""
from __future__ import annotations

import json

import pytest

from app.services import body_clean


def _fake_proc(structured: dict, returncode: int = 0, stderr: str = ""):
    class FakeProc:
        pass

    FakeProc.returncode = returncode
    FakeProc.stderr = stderr
    FakeProc.stdout = json.dumps({"structured_output": structured, "result": ""})
    return FakeProc


def test_short_text_short_circuits_no_llm_call(monkeypatch):
    """Tiny pasted snippets shouldn't pay the LLM tax — they almost never
    carry the pollution we built this for."""
    called = {"n": 0}

    def fake_run(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("subprocess should not be called for short input")

    monkeypatch.setattr(body_clean.subprocess, "run", fake_run)
    out = body_clean.clean_body("Καλημέρα κόσμε", "el")
    assert out is not None
    assert out.cleaned == "Καλημέρα κόσμε"
    assert out.removed == []
    assert called["n"] == 0


def test_empty_input_returns_empty_cleaned(monkeypatch):
    monkeypatch.setattr(body_clean.subprocess, "run",
                        lambda *a, **k: pytest.fail("no LLM for empty input"))
    out = body_clean.clean_body("   \n  ", "el")
    assert out is not None
    assert out.cleaned == ""


def test_happy_path_removes_and_joins(monkeypatch):
    src = (
        "10\n"
        "1.1 Η χώρα\n"
        "Μεσοποταμία ονομάστηκε για πρώτη φορά από \n"
        "τους αρχαίους Έλληνες. Ο Τίγρης διαρ-\n"
        "ρέουν στα ανατολι-\n"
        "κά μέρη. σιτηρών1.\n"
        "1. γεράνι ή γερανός: ανυψωτικό μηχάνημα.\n"
    )
    structured = {
        "cleaned": (
            "Μεσοποταμία ονομάστηκε για πρώτη φορά από "
            "τους αρχαίους Έλληνες. Ο Τίγρης διαρρέουν στα ανατολικά μέρη. σιτηρών."
        ),
        "removed": [
            "10",
            "1.1 Η χώρα",
            "1. γεράνι ή γερανός: ανυψωτικό μηχάνημα.",
        ],
        "hyphen_joins": ["διαρρέουν", "ανατολικά"],
    }
    monkeypatch.setattr(body_clean.subprocess, "run",
                        lambda *a, **k: _fake_proc(structured))
    out = body_clean.clean_body(src, "el")
    assert out is not None
    assert "διαρρέουν" in out.cleaned
    assert "ανατολικά" in out.cleaned
    assert "10" not in out.cleaned
    assert "γεράνι ή γερανός" not in out.cleaned
    assert len(out.removed) == 3
    assert out.hyphen_joins == ["διαρρέουν", "ανατολικά"]


def test_hallucinated_removal_discards_result(monkeypatch):
    """If the LLM claims to have removed text that wasn't in the source,
    discard the whole result — it likely paraphrased."""
    src = "Μεσοποταμία ονομάστηκε για πρώτη φορά."
    structured = {
        "cleaned": "Mesopotamia was first named.",
        "removed": ["This string never existed in the source"],
        "hyphen_joins": [],
    }
    monkeypatch.setattr(body_clean.subprocess, "run",
                        lambda *a, **k: _fake_proc(structured))
    out = body_clean.clean_body(src + "\n\nfiller to clear the short-input bypass " * 10, "el")
    assert out is None


def test_whitespace_collapsed_removals_still_match(monkeypatch):
    """A bibliographic citation that spans a line break in the source should
    still match when the LLM returns it as a single normalised line."""
    src = (
        "Real prose here that's long enough to skip the bypass. "
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit.\n"
        "Ηρόδοτος, Α, 193 μετ. Αγγ. Βλάχου,\n"
        "εκδ. Δ. Παπαδήμα.\n"
    )
    structured = {
        "cleaned": "Real prose here that's long enough to skip the bypass. Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
        "removed": ["Ηρόδοτος, Α, 193 μετ. Αγγ. Βλάχου, εκδ. Δ. Παπαδήμα."],
        "hyphen_joins": [],
    }
    monkeypatch.setattr(body_clean.subprocess, "run",
                        lambda *a, **k: _fake_proc(structured))
    out = body_clean.clean_body(src, "el")
    assert out is not None
    assert "Ηρόδοτος" not in out.cleaned


def test_softhyphen_inside_removed_segment_passes_audit(monkeypatch):
    """Haiku silently joins line-break hyphens inside removed segments too:
    a sidebar quote containing ``δυναμώ-\\nνει`` arrives back as
    ``δυναμώνει``. The audit must tolerate that, otherwise every page
    with a removed sidebar containing a hyphen-broken word is discarded."""
    src = (
        "Real chapter prose here that's long enough to clear the bypass. "
        "Lorem ipsum dolor sit amet consectetur adipiscing elit.\n"
        "Στην Ασσυρία βρέχει λίγο κι\n"
        "αυτό το νερό τρέφει τη ρίζα του\n"
        "σιταριού. Ποτίζεται όμως με το\n"
        "νερό του ποταμού που δυναμώ-\n"
        "νει τα σπαρτά κι έτσι ωριμάζει\n"
        "το σιτάρι.\n"
    )
    structured = {
        "cleaned": "Real chapter prose here that's long enough to clear the bypass. Lorem ipsum dolor sit amet consectetur adipiscing elit.",
        # The returned segment has the soft-hyphen already joined (δυναμώνει
        # instead of δυναμώ-\nνει) and whitespace collapsed.
        "removed": [
            "Στην Ασσυρία βρέχει λίγο κι αυτό το νερό τρέφει τη ρίζα του σιταριού. Ποτίζεται όμως με το νερό του ποταμού που δυναμώνει τα σπαρτά κι έτσι ωριμάζει το σιτάρι.",
        ],
        "hyphen_joins": [],
    }
    monkeypatch.setattr(body_clean.subprocess, "run",
                        lambda *a, **k: _fake_proc(structured))
    out = body_clean.clean_body(src, "el")
    assert out is not None, "audit should accept hyphen-joined removed segments"
    assert "δυναμώνει" not in out.cleaned
    assert "Ασσυρία" not in out.cleaned


def test_footnote_digit_detach_inside_removed_segment_passes_audit(monkeypatch):
    """Same idea as soft-hyphen: Haiku detaches footnote-marker digits
    (``γεράνια1`` → ``γεράνια``) inside removed segments. The audit must
    tolerate that or every sidebar with a marker gets discarded."""
    src = (
        "Real chapter prose here that's long enough to clear the bypass. "
        "Lorem ipsum dolor sit amet consectetur adipiscing elit.\n"
        "βγάζουν με το χέρι ή με γεράνια1. Ολόκληρη η Βαβυλωνία.\n"
    )
    structured = {
        "cleaned": "Real chapter prose here that's long enough to clear the bypass. Lorem ipsum dolor sit amet consectetur adipiscing elit.",
        "removed": ["βγάζουν με το χέρι ή με γεράνια. Ολόκληρη η Βαβυλωνία."],
        "hyphen_joins": [],
    }
    monkeypatch.setattr(body_clean.subprocess, "run",
                        lambda *a, **k: _fake_proc(structured))
    out = body_clean.clean_body(src, "el")
    assert out is not None, "audit should accept footnote-digit-stripped removed segments"
    assert "γεράνια" not in out.cleaned
    assert "Βαβυλωνία" not in out.cleaned


def test_control_char_in_source_passes_audit(monkeypatch):
    """PyMuPDF leaks C0 control chars (BEL=0x07 in front of footnote markers,
    among others). Haiku correctly drops them; audit shouldn't punish that."""
    src = (
        "Real chapter prose here that's long enough to clear the bypass. "
        "Lorem ipsum dolor sit amet consectetur adipiscing elit.\n"
        "Παπαδήμα. 1. \x07γεράνι ή γερανός: ανυψωτικό μηχάνημα.\n"
    )
    structured = {
        "cleaned": "Real chapter prose here that's long enough to clear the bypass. Lorem ipsum dolor sit amet consectetur adipiscing elit.",
        "removed": [
            "Παπαδήμα.",
            "1. γεράνι ή γερανός: ανυψωτικό μηχάνημα.",
        ],
        "hyphen_joins": [],
    }
    monkeypatch.setattr(body_clean.subprocess, "run",
                        lambda *a, **k: _fake_proc(structured))
    out = body_clean.clean_body(src, "el")
    assert out is not None, "audit should accept BEL-stripped removed segments"
    assert "γεράνι" not in out.cleaned


def test_cli_nonzero_returncode_returns_none(monkeypatch):
    class FakeProc:
        returncode = 1
        stderr = "boom"
        stdout = ""

    monkeypatch.setattr(body_clean.subprocess, "run",
                        lambda *a, **k: FakeProc())
    out = body_clean.clean_body("a" * 500 + "\n" * 5, "el")
    assert out is None


def test_cli_timeout_returns_none(monkeypatch):
    import subprocess as _sp

    def fake_run(*a, **k):
        raise _sp.TimeoutExpired(cmd="claude", timeout=180)

    monkeypatch.setattr(body_clean.subprocess, "run", fake_run)
    out = body_clean.clean_body("a" * 500 + "\n" * 5, "el")
    assert out is None


def test_unparseable_envelope_returns_none(monkeypatch):
    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = "not json at all"

    monkeypatch.setattr(body_clean.subprocess, "run",
                        lambda *a, **k: FakeProc())
    out = body_clean.clean_body("a" * 500 + "\n" * 5, "el")
    assert out is None


def test_legacy_result_field_fallback(monkeypatch):
    """Older CLI versions put the JSON in `result` as a string instead of
    `structured_output`. The cleaner accepts both."""
    structured = {"cleaned": "ok", "removed": [], "hyphen_joins": []}
    payload = json.dumps({"structured_output": None, "result": json.dumps(structured)})

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = payload

    monkeypatch.setattr(body_clean.subprocess, "run",
                        lambda *a, **k: FakeProc())
    out = body_clean.clean_body("a" * 500 + "\n" * 5, "el")
    assert out is not None
    assert out.cleaned == "ok"


# ─── Integration: real Claude CLI (slow, marked) ─────────────────────────

@pytest.mark.slow
def test_real_haiku_cleans_greek_textbook_page():
    """Actually call the Claude CLI on the screenshot's page-11 prose. Skipped
    in the fast suite; runs in the slow lane to catch real-world regressions
    in prompt design."""
    src = (
        "10\n"
        "Ι. ΟΙ ΠΟΛΙΤΙΣΜΟΙ ΤΗΣ ΕΓΓΥΣ ΑΝΑΤΟΛΗΣ\n"
        "1.1 Η χώρα\n"
        "Μεσοποταμία ονομάστηκε για πρώτη φορά από τους αρχαίους Έλληνες "
        "η χώρα την οποία διαρρέουν δύο μεγάλοι ποταμοί, ο Τίγρης και ο Ευφράτης. "
        "Ωστόσο, η άρδευση της γης από τα νερά των ποταμών μετέβαλε τις άγονες "
        "εκτάσεις σε εύφορες για την παραγωγή σιτηρών1.\n"
        "Ηρόδοτος, Α, 193 μετ. Αγγ. Βλάχου, εκδ. Δ. Παπαδήμα.\n"
        "1. γεράνι ή γερανός: ανυψωτικό μηχάνημα.\n"
    )
    out = body_clean.clean_body(src, "el")
    assert out is not None
    assert "10" not in out.cleaned.split("\n")[0]
    assert "ΠΟΛΙΤΙΣΜΟΙ" not in out.cleaned
    assert "Ηρόδοτος" not in out.cleaned
    assert "γεράνι ή γερανός" not in out.cleaned
    assert "Μεσοποταμία" in out.cleaned
    assert "σιτηρών" in out.cleaned
    # Footnote digit detached:
    assert "σιτηρών1" not in out.cleaned
