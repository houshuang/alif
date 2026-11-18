"""Automatic introduction runs after real reviews, not in session builds."""
from datetime import datetime, timezone

from app.models import Lemma, UserLemmaKnowledge
from app.services import intake_pass
from app.services import sentence_selector as ss


class _FakeSession:
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


def _patch_pass(monkeypatch, introduced):
    calls = {"refresh": 0, "introduce": 0, "warm": 0}
    monkeypatch.setattr(intake_pass, "SessionLocal", _FakeSession)
    monkeypatch.setattr(intake_pass, "_last_session_id", None)
    monkeypatch.setattr("app.services.automatic_attention.refresh_attention",
                        lambda db: calls.__setitem__("refresh", calls["refresh"] + 1))

    def introduce(db):
        calls["introduce"] += 1
        return introduced

    monkeypatch.setattr(ss, "introduce_reserved_words", introduce)
    monkeypatch.setattr("app.services.material_generator.warm_sentence_cache",
                        lambda: calls.__setitem__("warm", calls["warm"] + 1))
    return calls


def test_pass_runs_once_per_session_and_warms_sentences(monkeypatch):
    calls = _patch_pass(monkeypatch, [7])
    assert intake_pass.run_intake_pass("s1") == [7]
    assert intake_pass.run_intake_pass("s1") == []
    assert intake_pass.run_intake_pass(None) == []
    assert intake_pass.run_intake_pass("s2") == [7]
    assert calls == {"refresh": 2, "introduce": 2, "warm": 2}


def test_pass_without_introductions_does_not_generate(monkeypatch):
    calls = _patch_pass(monkeypatch, [])
    assert intake_pass.run_intake_pass("s1") == []
    assert calls == {"refresh": 1, "introduce": 1, "warm": 0}


def test_reserved_slots_close_when_acquisition_is_full(db_session, monkeypatch):
    seen = []
    monkeypatch.setattr(ss, "_auto_introduce_words",
                        lambda db, slots, known, now, skip_material_gen=False: seen.append(slots) or [])
    now = datetime.now(timezone.utc)
    ss.introduce_reserved_words(db_session, now)
    assert seen[-1] > 0

    lemmas = [Lemma(lemma_ar=f"كلمة{i}", lemma_ar_bare=f"كلمة{i}", gloss_en=f"w{i}", pos="noun")
              for i in range(ss.MID_ACCURACY_INTRO_BACKLOG_CAP + 1)]
    db_session.add_all(lemmas)
    db_session.flush()
    db_session.add_all([UserLemmaKnowledge(lemma_id=l.lemma_id, knowledge_state="acquiring") for l in lemmas])
    db_session.commit()
    ss.introduce_reserved_words(db_session, now)
    assert seen[-1] == 0


def test_sync_schedules_the_pass_for_the_reviewed_session(client, db_session, monkeypatch):
    from starlette.background import BackgroundTasks

    scheduled = []
    monkeypatch.setattr(
        BackgroundTasks, "add_task",
        lambda self, fn, *args: scheduled.extend(args) if fn is intake_pass.run_intake_pass else None,
    )
    monkeypatch.setattr("app.routers.review.submit_sentence_review",
                        lambda db, **kw: {"word_results": [], "duplicate": kw["client_review_id"] == "dup"})
    def review(cid):
        return {"type": "sentence", "client_review_id": cid, "payload": {
            "sentence_id": 1, "primary_lemma_id": 1, "comprehension_signal": "understood",
            "session_id": "sess-a"}}
    resp = client.post("/api/review/sync", json={"reviews": [review("a"), review("b")]})
    assert resp.json()["results"][0]["status"] == "ok", resp.text
    assert scheduled == ["sess-a"]
    client.post("/api/review/sync", json={"reviews": [review("dup")]})
    assert scheduled == ["sess-a"]
