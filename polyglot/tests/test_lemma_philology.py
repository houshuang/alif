"""Tests for ``lemma_philology.batch_enrich``.

Strategy mirrors ``test_material_generator.py``: patch the module-level
``subprocess.run`` so tests run offline with canned Claude responses.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.models import Lemma, UserLemmaKnowledge
from app.services import lemma_philology as lp


@dataclass
class _FakeProc:
    stdout: str
    stderr: str = ""
    returncode: int = 0


def _envelope(structured: dict) -> str:
    return json.dumps({"structured_output": structured, "result": ""})


def _seed_lemma(db, *, form: str, gloss: str = "x", pos: str = "noun",
                cognate_id: int | None = None,
                canonical: int | None = None,
                word_category: str | None = None) -> Lemma:
    lemma = Lemma(
        language_code="el",
        lemma_form=form,
        lemma_bare=form,
        gloss_en=gloss,
        pos=pos,
        source="test",
        cognate_lemma_id=cognate_id,
        canonical_lemma_id=canonical,
        word_category=word_category,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _seed_ulk(db, lemma_id: int) -> UserLemmaKnowledge:
    ulk = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state="acquiring",
        acquisition_box=1,
        acquisition_next_due=datetime.now(timezone.utc),
    )
    db.add(ulk)
    db.flush()
    return ulk


@pytest.fixture
def fake_claude(monkeypatch):
    state = {"script": [], "calls": []}

    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        state["calls"].append(cmd)
        if not state["script"]:
            raise AssertionError("Unexpected extra Claude call")
        return state["script"].pop(0)

    monkeypatch.setattr(lp.subprocess, "run", fake_run)
    return state


# Minimal canonical enrichment payload that matches the JSON schema and the
# Pydantic model. Used as the fake Claude response in happy-path tests.
def _good_payload(form: str) -> dict:
    return {
        "version": 1,
        "etymology": {
            "pie_root": "*ḱerd- (heart)",
            "ancient_form": "καρδία",
            "origin_note": f"Origin note for {form}.",
            "morphology": None,
        },
        "diachrony": [
            {"era": "Classical", "form": form, "meaning": "heart", "note": None},
            {"era": "Modern", "form": form, "meaning": "heart", "note": None},
        ],
        "cognates": [
            {"language": "English", "form": "heart", "relation": "shared-pie-root",
             "gloss_en": None, "note": None},
        ],
        "quotes": [
            {"text": "καρδίας μου", "source": "Test source", "era": "Classical",
             "translation_en": "my heart"},
        ],
        "register": {
            "formality": "neutral",
            "collocations": ["κ μ"],
            "false_friends_en": [],
            "usage_note": None,
        },
    }


def test_batch_enrich_happy_path(tmp_db, fake_claude):
    """One lemma, one valid enrichment → row written with status=done."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="καρδιά", gloss="heart")
        db.commit()
        lemma_id = lemma.lemma_id

    fake_claude["script"].append(_FakeProc(stdout=_envelope({
        "lemmas": [{"lemma_form": "καρδιά", "enrichment": _good_payload("καρδιά")}],
    })))

    result = lp.batch_enrich(language_code="el", lemma_ids=[lemma_id])

    assert result["enriched"] == 1
    assert result["failed_lemma_ids"] == []
    assert result["skipped_lemma_ids"] == []
    assert len(fake_claude["calls"]) == 1

    with tmp_db() as db:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        assert lemma.enrichment_status == "done"
        assert lemma.enrichment_json is not None
        assert lemma.enrichment_json["etymology"]["origin_note"].startswith("Origin note")
        assert lemma.enriched_at is not None


def test_glossless_lemma_is_skipped(tmp_db, fake_claude):
    """Lemmas without a gloss never reach the LLM call."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="ξξ", gloss="")
        db.commit()
        lemma_id = lemma.lemma_id

    result = lp.batch_enrich(language_code="el", lemma_ids=[lemma_id])

    assert result["enriched"] == 0
    assert lemma_id in result["skipped_lemma_ids"]
    assert len(fake_claude["calls"]) == 0  # never called the LLM


def test_variant_lemma_is_skipped(tmp_db, fake_claude):
    """Variant lemmas (canonical_lemma_id set) inherit their canonical's
    enrichment rather than getting their own."""
    with tmp_db() as db:
        canonical = _seed_lemma(db, form="γέροντας", gloss="old man")
        db.commit()
        variant = _seed_lemma(db, form="γερων", gloss="old man",
                              canonical=canonical.lemma_id)
        db.commit()
        variant_id = variant.lemma_id

    result = lp.batch_enrich(language_code="el", lemma_ids=[variant_id])

    assert result["enriched"] == 0
    assert variant_id in result["skipped_lemma_ids"]
    assert len(fake_claude["calls"]) == 0


def test_function_word_is_skipped(tmp_db, fake_claude):
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="το", gloss="the",
                            word_category="function_word")
        db.commit()
        lemma_id = lemma.lemma_id

    result = lp.batch_enrich(language_code="el", lemma_ids=[lemma_id])

    assert result["enriched"] == 0
    assert lemma_id in result["skipped_lemma_ids"]
    assert len(fake_claude["calls"]) == 0


def test_llm_failure_stamps_status_failed(tmp_db, fake_claude):
    """Total LLM failure (non-zero exit) → enrichment_status='failed', no JSON."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="καρδιά", gloss="heart")
        db.commit()
        lemma_id = lemma.lemma_id

    fake_claude["script"].append(_FakeProc(stdout="", stderr="boom", returncode=1))

    result = lp.batch_enrich(language_code="el", lemma_ids=[lemma_id])

    assert result["enriched"] == 0
    assert lemma_id in result["failed_lemma_ids"]

    with tmp_db() as db:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        assert lemma.enrichment_status == "failed"
        assert lemma.enrichment_json is None


def test_partial_response_marks_missing_as_failed(tmp_db, fake_claude):
    """Two lemmas requested, only one returned → other lands in failed list
    and gets status='failed'."""
    with tmp_db() as db:
        a = _seed_lemma(db, form="καρδιά", gloss="heart")
        b = _seed_lemma(db, form="λόγος", gloss="word")
        db.commit()
        ids = [a.lemma_id, b.lemma_id]

    fake_claude["script"].append(_FakeProc(stdout=_envelope({
        "lemmas": [{"lemma_form": "καρδιά", "enrichment": _good_payload("καρδιά")}],
    })))

    result = lp.batch_enrich(language_code="el", lemma_ids=ids)

    assert result["enriched"] == 1
    assert ids[1] in result["failed_lemma_ids"]


def test_find_unenriched_picks_engaged_vocabulary(tmp_db, monkeypatch):
    """find_unenriched_lemmas should only return lemmas with a ULK row."""
    with tmp_db() as db:
        engaged = _seed_lemma(db, form="καρδιά", gloss="heart")
        # No ULK for this one — should be skipped
        _seed_lemma(db, form="λόγος", gloss="word")
        db.commit()
        _seed_ulk(db, engaged.lemma_id)
        db.commit()
        engaged_id = engaged.lemma_id

    ids = lp.find_unenriched_lemmas(language_code="el", limit=10)
    assert ids == [engaged_id]


def test_fixture_round_trip_matches_pydantic_shape():
    """The real-world enrichment fixture from the POC must parse cleanly into
    LemmaEnrichment. Guard against silent drift between the prompt's JSON
    schema and the Pydantic model."""
    from pathlib import Path
    from app.schemas import LemmaEnrichment

    fixture = Path(__file__).resolve().parents[2] / "artifacts" / "polyglot-enrichment-poc" / "enrichment.json"
    if not fixture.exists():
        pytest.skip("Fixture not present; run artifacts/polyglot-enrichment-poc/build_prompt.py")
    data = json.loads(fixture.read_text())
    for item in data["lemmas"]:
        e = LemmaEnrichment.model_validate(item["enrichment"])
        assert e.etymology is not None
        assert e.etymology.origin_note
        assert len(e.diachrony) > 0
        assert len(e.cognates) > 0
        # version must be 1
        assert e.version == 1
