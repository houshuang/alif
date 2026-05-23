"""Smoke + shape tests for /api/stats.

Verifies the expanded payload (today, leitner, fsrs, history_14d, frequency)
renders for both an empty DB and one seeded with lemmas / reviews / pages /
frequency entries.
"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    Lemma, UserLemmaKnowledge, ReviewLog, Story, Page,
    FrequencyEntry,
)


def _client(tmp_db):
    test_session_factory = tmp_db

    def _override():
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app), test_session_factory


def _cleanup():
    app.dependency_overrides.clear()


def test_stats_empty_language(tmp_db):
    client, _ = _client(tmp_db)
    try:
        r = client.get("/api/stats?language_code=el")
        assert r.status_code == 200
        body = r.json()
        assert body["language_code"] == "el"
        assert body["total_lemmas"] == 0
        assert body["new"] == 0
        assert body["by_state"]["known"] == 0
        assert body["leitner"]["total_acquiring"] == 0
        assert body["fsrs"]["tracked"] == 0
        assert body["recovery"]["pre_known"] == 0
        assert body["recovery"]["recovered_once"] == 0
        assert body["today"]["reviews"] == 0
        assert body["today"]["streak"] == 0
        assert len(body["history_14d"]) == 14
        assert body["frequency"] is None
        assert body["stories"] == []
    finally:
        _cleanup()


def test_stats_rejects_unknown_language(tmp_db):
    client, _ = _client(tmp_db)
    try:
        r = client.get("/api/stats?language_code=xx")
        assert r.status_code == 400
    finally:
        _cleanup()


def test_stats_counts_lemmas_by_state(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            for form, state in [
                ("α", "known"), ("β", "known"), ("γ", "acquiring"),
                ("δ", "learning"), ("ε", "encountered"), ("ζ", "lapsed"),
            ]:
                lemma = Lemma(language_code="el", lemma_form=form, lemma_bare=form,
                              source="test")
                db.add(lemma)
                db.flush()
                db.add(UserLemmaKnowledge(
                    lemma_id=lemma.lemma_id, knowledge_state=state,
                ))
            # One lemma with no ULK → counts as "new" (unseen).
            db.add(Lemma(language_code="el", lemma_form="η", lemma_bare="η", source="test"))
            db.commit()

        r = client.get("/api/stats?language_code=el")
        body = r.json()
        assert body["total_lemmas"] == 7
        assert body["new"] == 1
        assert body["by_state"]["known"] == 2
        assert body["by_state"]["acquiring_only"] == 1
        assert body["by_state"]["learning"] == 1
        assert body["by_state"]["acquiring"] == 2  # legacy aggregate
        assert body["by_state"]["encountered"] == 1
        assert body["by_state"]["lapsed"] == 1
    finally:
        _cleanup()


def test_stats_leitner_and_fsrs(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            now = datetime.utcnow()
            # Three acquiring lemmas across boxes, one of them due
            for form, box, due_offset_h in [
                ("a", 1, -1), ("b", 2, 5), ("c", 3, 24),
            ]:
                lemma = Lemma(language_code="el", lemma_form=form, lemma_bare=form,
                              source="test")
                db.add(lemma)
                db.flush()
                db.add(UserLemmaKnowledge(
                    lemma_id=lemma.lemma_id,
                    knowledge_state="acquiring",
                    acquisition_box=box,
                    acquisition_next_due=now + timedelta(hours=due_offset_h),
                ))
            # Two FSRS-tracked lemmas with different stabilities
            for form, stab in [("d", 0.5), ("e", 12.0)]:
                lemma = Lemma(language_code="el", lemma_form=form, lemma_bare=form,
                              source="test")
                db.add(lemma)
                db.flush()
                db.add(UserLemmaKnowledge(
                    lemma_id=lemma.lemma_id,
                    knowledge_state="known",
                    fsrs_card_json={"stability": stab, "difficulty": 5.0, "state": 2},
                ))
            db.commit()

        body = client.get("/api/stats?language_code=el").json()
        assert body["leitner"]["box_1"] == 1
        assert body["leitner"]["box_2"] == 1
        assert body["leitner"]["box_3"] == 1
        assert body["leitner"]["total_acquiring"] == 3
        assert body["leitner"]["due_now"] == 1

        assert body["fsrs"]["tracked"] == 2
        buckets = {b["label"]: b["count"] for b in body["fsrs"]["stability_buckets"]}
        assert buckets["<1d"] == 1
        assert buckets["7-21d"] == 1
    finally:
        _cleanup()


def test_stats_today_activity_and_history(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            lemma = Lemma(language_code="el", lemma_form="x", lemma_bare="x",
                          source="test")
            db.add(lemma); db.flush()
            now = datetime.utcnow()
            db.add(ReviewLog(lemma_id=lemma.lemma_id, rating=3, reviewed_at=now))
            db.add(ReviewLog(lemma_id=lemma.lemma_id, rating=3,
                             reviewed_at=now - timedelta(days=2)))
            # Page viewed today
            story = Story(language_code="el", title="t", source="paste", body_src="x")
            db.add(story); db.flush()
            db.add(Page(story_id=story.id, page_number=1, body_src="x", viewed_at=now))
            # ULK introduced today
            db.add(UserLemmaKnowledge(
                lemma_id=lemma.lemma_id, knowledge_state="acquiring",
                introduced_at=now,
            ))
            db.commit()

        body = client.get("/api/stats?language_code=el").json()
        assert body["today"]["reviews"] == 1
        assert body["today"]["pages_read"] == 1
        assert body["today"]["new_lemmas"] == 1
        assert body["today"]["streak"] >= 1
        # history_14d has today and day -2 as non-zero entries
        assert any(d["reviews"] == 1 for d in body["history_14d"])
    finally:
        _cleanup()


def test_stats_frequency_block(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            lemma = Lemma(language_code="el", lemma_form="ο", lemma_bare="ο",
                          source="test")
            db.add(lemma); db.flush()
            db.add(UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="known"))
            db.add(FrequencyEntry(
                language_code="el", source="subtlex_gr", rank=1,
                lemma_key="ο", display_form="ο", lemma_id=lemma.lemma_id,
            ))
            # An unlinked frequency entry
            db.add(FrequencyEntry(
                language_code="el", source="subtlex_gr", rank=2,
                lemma_key="η", display_form="η", lemma_id=None,
            ))
            db.commit()

        body = client.get("/api/stats?language_code=el").json()
        assert body["frequency"] is not None
        assert body["frequency"]["source"] == "subtlex_gr"
        assert body["frequency"]["total_entries"] == 2
        bands = body["frequency"]["bands"]
        assert len(bands) >= 1
        # The 100-band (or fallback band) should report 1 learned, 1 unmapped
        first = bands[0]
        assert first["learned"] == 1
        assert first["unmapped"] == 1
    finally:
        _cleanup()


def test_stats_recovery_block(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            now = datetime.utcnow()

            pre = Lemma(language_code="el", lemma_form="pre", lemma_bare="pre", source="test")
            db.add(pre); db.flush()
            db.add(UserLemmaKnowledge(
                lemma_id=pre.lemma_id,
                knowledge_state="known",
                knowledge_origin="pre_known",
            ))

            cog = Lemma(language_code="el", lemma_form="cog", lemma_bare="cog", source="test")
            db.add(cog); db.flush()
            db.add(UserLemmaKnowledge(
                lemma_id=cog.lemma_id,
                knowledge_state="known",
                knowledge_origin="cognate_known",
            ))

            recovered = Lemma(language_code="el", lemma_form="rec", lemma_bare="rec", source="test")
            db.add(recovered); db.flush()
            db.add(UserLemmaKnowledge(
                lemma_id=recovered.lemma_id,
                knowledge_state="learning",
                first_failed_at=now - timedelta(days=3),
                first_correct_after_failure_at=now - timedelta(days=2),
                graduated_at=now - timedelta(days=2),
                fsrs_card_json={"stability": 30.0, "difficulty": 5.0, "state": 1},
            ))

            open_word = Lemma(language_code="el", lemma_form="open", lemma_bare="open", source="test")
            db.add(open_word); db.flush()
            db.add(UserLemmaKnowledge(
                lemma_id=open_word.lemma_id,
                knowledge_state="acquiring",
                first_failed_at=now,
                failure_count=1,
            ))
            db.commit()

        body = client.get("/api/stats?language_code=el").json()
        rec = body["recovery"]
        assert rec["pre_known"] == 1
        assert rec["cognate_known"] == 1
        assert rec["ever_failed"] == 2
        assert rec["recovered_once"] == 1
        assert rec["graduated_after_failure"] == 1
        assert rec["stable_after_failure_21d"] == 1
        assert rec["failed_not_yet_recovered"] == 1
        assert rec["still_acquiring_after_failure"] == 1
        assert body["today"]["marked_unknown"] == 1
    finally:
        _cleanup()


def test_backfill_knowledge_lifecycle(tmp_db):
    from app.services.knowledge_lifecycle import backfill_knowledge_lifecycle

    with tmp_db() as db:
        now = datetime.utcnow()
        pre = Lemma(language_code="el", lemma_form="pre", lemma_bare="pre", source="test")
        db.add(pre); db.flush()
        db.add(UserLemmaKnowledge(
            lemma_id=pre.lemma_id,
            knowledge_state="known",
            source="reading_intake",
        ))

        failed = Lemma(language_code="el", lemma_form="fail", lemma_bare="fail", source="test")
        db.add(failed); db.flush()
        db.add(UserLemmaKnowledge(
            lemma_id=failed.lemma_id,
            knowledge_state="learning",
            source="reading_intake",
            entered_acquiring_at=now - timedelta(days=2),
        ))
        db.add(ReviewLog(
            lemma_id=failed.lemma_id,
            rating=1,
            reviewed_at=now - timedelta(days=1),
        ))
        db.add(ReviewLog(
            lemma_id=failed.lemma_id,
            rating=3,
            reviewed_at=now,
        ))
        db.commit()

        result = backfill_knowledge_lifecycle(db)

        assert result["changed"] > 0
        db.refresh(pre.knowledge)
        assert pre.knowledge.knowledge_origin == "pre_known"

        db.refresh(failed.knowledge)
        assert failed.knowledge.knowledge_origin == "marked_unknown"
        assert failed.knowledge.first_failed_at is not None
        assert failed.knowledge.failure_count == 2  # inferred red tap + review Again
        assert failed.knowledge.first_correct_after_failure_at is not None
