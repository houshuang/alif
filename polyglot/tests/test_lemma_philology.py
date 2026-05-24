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


def _verify_ok_for(*lemma_ids: int) -> _FakeProc:
    """Canned 'all OK' verifier response covering the given lemma_ids."""
    return _FakeProc(stdout=_envelope({
        "verdicts": [
            {"lemma_id": lid, "verdict": "ok"} for lid in lemma_ids
        ],
    }))


def _verify_flagged_for(lemma_id: int, kind: str = "etymology_issue",
                       note: str = "PIE root incorrect") -> _FakeProc:
    return _FakeProc(stdout=_envelope({
        "verdicts": [{"lemma_id": lemma_id, "verdict": kind, "note": note}],
    }))


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
    # Scripts an exact single-provider (Claude) call sequence, so pin failover
    # off — provider failover is covered in test_llm_cli.py.
    monkeypatch.setenv("POLYGLOT_LLM_FALLBACK", "0")
    monkeypatch.delenv("POLYGLOT_LLM_PROVIDER", raising=False)
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
    """One lemma, one valid enrichment + verifier passes → row written with
    status=done. Two Claude calls total: Sonnet generation + Haiku verify."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="καρδιά", gloss="heart")
        db.commit()
        lemma_id = lemma.lemma_id

    fake_claude["script"].append(_FakeProc(stdout=_envelope({
        "lemmas": [{"lemma_form": "καρδιά", "enrichment": _good_payload("καρδιά")}],
    })))
    fake_claude["script"].append(_verify_ok_for(lemma_id))

    result = lp.batch_enrich(language_code="el", lemma_ids=[lemma_id])

    assert result["enriched"] == 1
    assert result["failed_lemma_ids"] == []
    assert result["skipped_lemma_ids"] == []
    assert len(fake_claude["calls"]) == 2

    with tmp_db() as db:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        assert lemma.enrichment_status == "done"
        assert lemma.enrichment_json is not None
        assert lemma.enrichment_json["etymology"]["origin_note"].startswith("Origin note")
        assert "_verifier_note" not in lemma.enrichment_json
        assert lemma.enriched_at is not None


def test_self_correct_recovers_flagged_lemma(tmp_db, fake_claude):
    """Verifier flags → self-correct → re-verify OK → final status `done`.
    The corrected payload replaces the flagged one and no `_verifier_note`
    is attached (since we got to clean)."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="στο", gloss="in the")
        db.commit()
        lemma_id = lemma.lemma_id

    # 1. Original Sonnet generation
    fake_claude["script"].append(_FakeProc(stdout=_envelope({
        "lemmas": [{"lemma_form": "στο", "enrichment": _good_payload("στο")}],
    })))
    # 2. Haiku verify flags it
    fake_claude["script"].append(_verify_flagged_for(
        lemma_id, "etymology_issue", "PIE *steh₂- is wrong",
    ))
    # 3. Sonnet self-correct (different payload — pretend pie_root is now null)
    corrected = _good_payload("στο")
    corrected["etymology"]["pie_root"] = None
    corrected["etymology"]["origin_note"] = "Corrected note."
    fake_claude["script"].append(_FakeProc(stdout=_envelope({
        "lemmas": [{"lemma_form": "στο", "enrichment": corrected}],
    })))
    # 4. Haiku re-verify passes
    fake_claude["script"].append(_verify_ok_for(lemma_id))

    result = lp.batch_enrich(language_code="el", lemma_ids=[lemma_id])
    assert result["enriched"] == 1

    with tmp_db() as db:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        assert lemma.enrichment_status == "done"
        assert lemma.enrichment_json["etymology"]["pie_root"] is None
        assert lemma.enrichment_json["etymology"]["origin_note"] == "Corrected note."
        assert "_verifier_note" not in lemma.enrichment_json


def test_self_correct_max_attempts_strips_flagged_field(tmp_db, fake_claude, monkeypatch):
    """Persistent flag through both self-correct attempts → `done_partial`,
    the flagged top-level field is stripped, and `_verifier_note` records
    `stripped: True` so the UI knows the omission was deliberate."""
    monkeypatch.setattr(lp, "SELF_CORRECT_MAX_ATTEMPTS", 2)
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="στο", gloss="in the")
        db.commit()
        lemma_id = lemma.lemma_id

    # 1. Original Sonnet generation
    fake_claude["script"].append(_FakeProc(stdout=_envelope({
        "lemmas": [{"lemma_form": "στο", "enrichment": _good_payload("στο")}],
    })))
    # 2. Haiku verify flags (etymology issue)
    fake_claude["script"].append(_verify_flagged_for(
        lemma_id, "etymology_issue", "PIE *steh₂- is wrong",
    ))
    # 3. Sonnet self-correct attempt 1
    fake_claude["script"].append(_FakeProc(stdout=_envelope({
        "lemmas": [{"lemma_form": "στο", "enrichment": _good_payload("στο")}],
    })))
    # 4. Haiku re-verify still flags
    fake_claude["script"].append(_verify_flagged_for(
        lemma_id, "etymology_issue", "Still wrong",
    ))
    # 5. Sonnet self-correct attempt 2
    fake_claude["script"].append(_FakeProc(stdout=_envelope({
        "lemmas": [{"lemma_form": "στο", "enrichment": _good_payload("στο")}],
    })))
    # 6. Haiku re-verify still flags
    fake_claude["script"].append(_verify_flagged_for(
        lemma_id, "etymology_issue", "Still wrong on round 2",
    ))

    result = lp.batch_enrich(language_code="el", lemma_ids=[lemma_id])
    assert result["enriched"] == 1

    with tmp_db() as db:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        assert lemma.enrichment_status == "done_partial"
        # Etymology block stripped — bad PIE root can't ship to UI
        assert lemma.enrichment_json["etymology"] is None
        # Other slices preserved (diachrony, cognates, quotes, register)
        assert len(lemma.enrichment_json["diachrony"]) > 0
        assert len(lemma.enrichment_json["cognates"]) > 0
        # Verifier note records the strip
        note = lemma.enrichment_json["_verifier_note"]
        assert note["verdict"] == "etymology_issue"
        assert note["stripped"] is True


def test_self_correct_sonnet_failure_strips_field(tmp_db, fake_claude):
    """If Sonnet self-correct itself fails (LLM error), the original flag
    sticks and the write phase strips the field + stamps `done_partial`.
    Important: a failed self-correct must NOT leave a `done_flagged` (bad
    payload exposed) row — the strip happens regardless of why the verdict
    is still flagged."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="στο", gloss="in the")
        db.commit()
        lemma_id = lemma.lemma_id

    # 1. Sonnet generation
    fake_claude["script"].append(_FakeProc(stdout=_envelope({
        "lemmas": [{"lemma_form": "στο", "enrichment": _good_payload("στο")}],
    })))
    # 2. Haiku verify flags diachrony this time
    fake_claude["script"].append(_verify_flagged_for(
        lemma_id, "diachrony_issue", "Byzantine stage is wrong",
    ))
    # 3. Sonnet self-correct FAILS (non-zero exit)
    fake_claude["script"].append(_FakeProc(stdout="", stderr="boom", returncode=1))

    result = lp.batch_enrich(language_code="el", lemma_ids=[lemma_id])
    assert result["enriched"] == 1

    with tmp_db() as db:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        assert lemma.enrichment_status == "done_partial"
        # Diachrony stripped this time
        assert lemma.enrichment_json["diachrony"] == []
        # Etymology preserved
        assert lemma.enrichment_json["etymology"] is not None
        note = lemma.enrichment_json["_verifier_note"]
        assert note["verdict"] == "diachrony_issue"
        assert note["stripped"] is True


def test_verifier_failure_writes_done_unverified(tmp_db, fake_claude):
    """When the Haiku verifier call itself fails (timeout / non-zero exit),
    the Sonnet output is still written but stamped 'done_unverified' so a
    later cron can re-pick if desired."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="καρδιά", gloss="heart")
        db.commit()
        lemma_id = lemma.lemma_id

    fake_claude["script"].append(_FakeProc(stdout=_envelope({
        "lemmas": [{"lemma_form": "καρδιά", "enrichment": _good_payload("καρδιά")}],
    })))
    # Verifier call fails (non-zero exit)
    fake_claude["script"].append(_FakeProc(stdout="", stderr="boom", returncode=1))

    result = lp.batch_enrich(language_code="el", lemma_ids=[lemma_id])
    assert result["enriched"] == 1

    with tmp_db() as db:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        assert lemma.enrichment_status == "done_unverified"
        assert lemma.enrichment_json is not None  # Sonnet payload written


def test_find_unenriched_with_include_failed_picks_partial_and_legacy_flagged(tmp_db, fake_claude):
    """`include_failed=True` must re-pick three statuses so they don't linger:
      - `failed`        — Sonnet itself never produced a payload.
      - `done_flagged`  — legacy (pre-self-correct) rows in production.
      - `done_partial`  — verifier persistently flagged after self-correct.
    The cron passes `--include-failed` by default for exactly this reason."""
    with tmp_db() as db:
        a = _seed_lemma(db, form="στο", gloss="in the")
        b = _seed_lemma(db, form="απο", gloss="from")
        c = _seed_lemma(db, form="με", gloss="with")
        _seed_ulk(db, a.lemma_id)
        _seed_ulk(db, b.lemma_id)
        _seed_ulk(db, c.lemma_id)
        a.enrichment_status = "done_flagged"
        a.enrichment_json = {"version": 1, "etymology": {"origin_note": "bad"}}
        b.enrichment_status = "done_partial"
        b.enrichment_json = {"version": 1, "etymology": None}
        c.enrichment_status = "failed"
        db.commit()
        ids = {a.lemma_id, b.lemma_id, c.lemma_id}

    # Without include_failed, all three are skipped (only NULL picked).
    assert lp.find_unenriched_lemmas(language_code="el", limit=10) == []
    # With include_failed, all three show up.
    picked = lp.find_unenriched_lemmas(
        language_code="el", limit=10, include_failed=True,
    )
    assert set(picked) == ids


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
    # Verifier only sees καρδιά (λόγος was never parsed); OK for it.
    fake_claude["script"].append(_verify_ok_for(ids[0]))

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


def test_find_unenriched_excludes_known_state(tmp_db):
    """`known` lemmas are already learnt — skip them (2026-05-21 policy)."""
    with tmp_db() as db:
        learnt = _seed_lemma(db, form="γράφω", gloss="write")
        active = _seed_lemma(db, form="τρώω", gloss="eat")
        db.commit()
        # `known` ULK — should be excluded
        ulk1 = UserLemmaKnowledge(
            lemma_id=learnt.lemma_id,
            knowledge_state="known",
            acquisition_box=3,
        )
        db.add(ulk1)
        # `acquiring` ULK — should be included
        _seed_ulk(db, active.lemma_id)
        db.commit()
        active_id = active.lemma_id

    ids = lp.find_unenriched_lemmas(language_code="el", limit=10)
    assert ids == [active_id]


def test_find_unenriched_buckets_acquiring_before_encountered(tmp_db):
    """Bucket order: acquiring → learning/lapsed → encountered. Within
    acquiring, sort by `acquisition_next_due` ASC so the next-to-be-reviewed
    lemma's lookup card is ready when it shows up."""
    soon = datetime(2026, 5, 21, 0, 0, tzinfo=timezone.utc)
    later = datetime(2026, 5, 25, 0, 0, tzinfo=timezone.utc)
    with tmp_db() as db:
        acq_later = _seed_lemma(db, form="γράφω", gloss="write")
        acq_soon = _seed_lemma(db, form="τρώω", gloss="eat")
        learning = _seed_lemma(db, form="πίνω", gloss="drink")
        encountered = _seed_lemma(db, form="βλέπω", gloss="see")
        db.commit()
        db.add(UserLemmaKnowledge(
            lemma_id=acq_later.lemma_id,
            knowledge_state="acquiring", acquisition_box=1,
            acquisition_next_due=later,
        ))
        db.add(UserLemmaKnowledge(
            lemma_id=acq_soon.lemma_id,
            knowledge_state="acquiring", acquisition_box=1,
            acquisition_next_due=soon,
        ))
        db.add(UserLemmaKnowledge(
            lemma_id=learning.lemma_id,
            knowledge_state="learning",
        ))
        db.add(UserLemmaKnowledge(
            lemma_id=encountered.lemma_id,
            knowledge_state="encountered",
        ))
        db.commit()
        expected = [acq_soon.lemma_id, acq_later.lemma_id, learning.lemma_id, encountered.lemma_id]

    ids = lp.find_unenriched_lemmas(language_code="el", limit=10)
    assert ids == expected


def test_batch_enrich_skips_when_lock_held(tmp_db, fake_claude):
    """Concurrency lock: a second concurrent call returns immediately without
    spending any Claude budget. Mirrors warm_sentence_cache's lock pattern —
    prevents two overlapping cron runs from double-enriching the same lemmas
    when per-run caps are raised."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="καρδιά", gloss="heart")
        db.commit()
        lemma_id = lemma.lemma_id

    assert lp._enrich_lock.acquire(blocking=False)
    try:
        result = lp.batch_enrich(language_code="el", lemma_ids=[lemma_id])
    finally:
        lp._enrich_lock.release()

    assert result["skipped"] is True
    assert result["reason"] == "enrich_busy"
    assert result["enriched"] == 0
    assert fake_claude["calls"] == []


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
