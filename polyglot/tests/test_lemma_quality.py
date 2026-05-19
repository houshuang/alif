"""Quality-gate tests with Claude CLI mocked. The real CLI path runs in a
separate manual smoke test (see scripts/qg_smoke.py)."""
from datetime import datetime, timezone

import pytest

from app.services import reading_intake, lemma_quality
from app.services.lemma_quality import Verdict
from app.models import Lemma, Page, PageWord


@pytest.fixture
def force_gate_enabled(monkeypatch):
    monkeypatch.setattr(lemma_quality, "QUALITY_GATE_ENABLED", True)


def test_apply_ok_verdict_stamps_verified(tmp_db, force_gate_enabled, monkeypatch):
    """Mock Claude returning verdict=ok for every token. Should stamp
    verified_at on every PageWord and on the Page."""
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο σπίτι")
        page, _ = reading_intake.get_page_view(db, story.id, 1)
        # Pre-test: mappings_verified_at is NULL
        assert page.mappings_verified_at is None

        # Mock Claude → every token is OK
        def fake_call(chunk, language_name):
            return [Verdict(pageword_id=c.pageword_id, verdict="ok") for c in chunk]
        monkeypatch.setattr(lemma_quality, "_call_claude", fake_call)

        corrected = lemma_quality.verify_page_mappings(db, page, force=True)
        assert corrected == 0
        db.refresh(page)
        assert page.mappings_verified_at is not None


def test_apply_wrong_verdict_creates_or_links_lemma(tmp_db, force_gate_enabled, monkeypatch):
    """Mock Claude flagging 'χώρα' as wrongly lemmatized to 'χωρώ' (which is
    exactly the homograph problem we hit on real content). Should redirect
    the PageWord.lemma_id to a Lemma with lemma_form='χώρα'."""
    with tmp_db() as db:
        # Set up a Lemma for the wrong proposed form (simulating simplemma)
        wrong = Lemma(language_code="el", lemma_form="χωρώ",
                      lemma_bare="χωρω", source="reading_intake")
        db.add(wrong); db.commit()

        story = reading_intake.import_paste(db, language_code="el", body="χώρα")
        page, _ = reading_intake.get_page_view(db, story.id, 1)
        word = db.query(PageWord).filter(PageWord.page_id == page.id).first()
        # Manually point the word at the wrong lemma to set up the test
        word.lemma_id = wrong.lemma_id
        db.commit()

        def fake_call(chunk, language_name):
            return [Verdict(pageword_id=c.pageword_id, verdict="wrong",
                            correct_lemma="χώρα", reason="noun, not verb")
                    for c in chunk]
        monkeypatch.setattr(lemma_quality, "_call_claude", fake_call)

        # Force the gate to consider this token (it'd normally skip surface==lemma)
        monkeypatch.setenv("POLYGLOT_QG_SKIP_IDENTITY", "0")
        corrected = lemma_quality.verify_page_mappings(db, page, force=True)
        assert corrected == 1

        db.refresh(word)
        assert word.original_lemma_id == wrong.lemma_id
        new_lemma = db.get(Lemma, word.lemma_id)
        assert new_lemma is not None
        assert new_lemma.lemma_form == "χώρα"
        assert new_lemma.source == "quality_gate"
        assert word.verified_at is not None
        assert word.quality_note == "noun, not verb"


def test_apply_unclear_verdict_marks_but_doesnt_change(tmp_db, force_gate_enabled, monkeypatch):
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο")
        page, _ = reading_intake.get_page_view(db, story.id, 1)
        word = db.query(PageWord).filter(PageWord.page_id == page.id).first()
        original_lemma_id = word.lemma_id

        def fake_call(chunk, language_name):
            return [Verdict(pageword_id=c.pageword_id, verdict="unclear",
                            reason="OCR garbage")
                    for c in chunk]
        monkeypatch.setattr(lemma_quality, "_call_claude", fake_call)
        monkeypatch.setenv("POLYGLOT_QG_SKIP_IDENTITY", "0")

        corrected = lemma_quality.verify_page_mappings(db, page, force=True)
        assert corrected == 0
        db.refresh(word)
        assert word.lemma_id == original_lemma_id  # unchanged
        assert word.quality_note == "OCR garbage"
        assert word.verified_at is not None
        db.refresh(page)
        assert page.quality_gate_failures == 1


def test_skips_function_words(tmp_db, force_gate_enabled, monkeypatch):
    """Articles, common prepositions etc. should not consume LLM budget."""
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el",
                                            body="ο και σε")  # all function words
        page, _ = reading_intake.get_page_view(db, story.id, 1)

        calls = []
        def fake_call(chunk, language_name):
            calls.append(chunk)
            return [Verdict(pageword_id=c.pageword_id, verdict="ok") for c in chunk]
        monkeypatch.setattr(lemma_quality, "_call_claude", fake_call)

        lemma_quality.verify_page_mappings(db, page, force=True)
        # Either no calls were made (everything filtered) or what was sent
        # excluded the function-word forms.
        for chunk in calls:
            assert all(c.surface.lower() not in {"ο", "και", "σε"} for c in chunk)


def test_skipped_when_already_verified(tmp_db, force_gate_enabled, monkeypatch):
    """Idempotency — second call without force should be a no-op."""
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο")
        page, _ = reading_intake.get_page_view(db, story.id, 1)

        calls = []
        def fake_call(chunk, language_name):
            calls.append(chunk)
            return [Verdict(pageword_id=c.pageword_id, verdict="ok") for c in chunk]
        monkeypatch.setattr(lemma_quality, "_call_claude", fake_call)
        monkeypatch.setenv("POLYGLOT_QG_SKIP_IDENTITY", "0")

        lemma_quality.verify_page_mappings(db, page, force=True)
        first_calls = len(calls)
        # Second pass — without force, should skip
        lemma_quality.verify_page_mappings(db, page)
        assert len(calls) == first_calls  # no additional calls


def test_llm_failure_does_not_crash(tmp_db, force_gate_enabled, monkeypatch):
    """If Claude returns None (subprocess failure), gate should leave tokens
    unverified but not raise — caller can retry later."""
    with tmp_db() as db:
        story = reading_intake.import_paste(db, language_code="el", body="βιβλίο σπίτι")
        page, _ = reading_intake.get_page_view(db, story.id, 1)

        monkeypatch.setattr(lemma_quality, "_call_claude", lambda chunk, language_name: None)
        monkeypatch.setenv("POLYGLOT_QG_SKIP_IDENTITY", "0")

        corrected = lemma_quality.verify_page_mappings(db, page, force=True)
        assert corrected == 0
        db.refresh(page)
        assert page.mappings_verified_at is not None  # still stamped (gate ran)
