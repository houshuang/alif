"""Pre-read: automatic word plan for upcoming pages (services/preread_service.py,
routers/preread.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Lemma, PrereadSet, SnapPage, UserLemmaKnowledge
from app.routers import preread as pr
from app.services import preread_service as ps
from app.services.attention_policy import LEARNER_PICK_SOURCES
from app.services.fsrs_service import create_new_card


def _word(bare, *, lemma_id=None, count=1, rank=None, gloss="x", proper=False, surface=None):
    return {
        "surface": surface or bare, "surface_forms": [surface or bare],
        "lemma_ar": bare, "lemma_ar_bare": bare, "gloss_en": gloss, "pos": "noun",
        "is_proper_noun": proper, "freq_rank": rank, "count_in_text": count,
        "lemma_id": lemma_id, "transliteration": "t", "register": None, "dialect": None,
    }


def _known_card(due: datetime, stability: float) -> dict:
    card = create_new_card()
    card.update(state=2, step=None, stability=stability, difficulty=5.0,
                due=due.isoformat(), last_review=(due - timedelta(days=stability)).isoformat())
    return card


def test_preread_is_a_learner_pick_source():
    assert "preread" in LEARNER_PICK_SOURCES


def test_score_rewards_count_importance_and_frequency():
    base = ps.learn_score(1, None, 1)
    assert ps.learn_score(4, None, 1) > base
    assert ps.learn_score(1, None, 3) > base
    assert ps.learn_score(1, 800, 1) > ps.learn_score(1, 20000, 1) > base
    assert ps.worth_it(None) and ps.worth_it(1) and not ps.worth_it(0)
    assert ps.learn_quota(1) == ps.LEARN_MIN
    assert ps.learn_quota(10) == 60
    assert ps.learn_quota(30) == ps.LEARN_MAX


def test_analyze_classifies_and_selects(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    known_solid = Lemma(lemma_ar="بيت", lemma_ar_bare="بيت", gloss_en="house", pos="noun")
    known_shaky = Lemma(lemma_ar="حدود", lemma_ar_bare="حدود", gloss_en="borders", pos="noun")
    acquiring = Lemma(lemma_ar="سيارة", lemma_ar_bare="سيارة", gloss_en="car", pos="noun")
    name = Lemma(lemma_ar="مروان", lemma_ar_bare="مروان", gloss_en="Marwan", pos="noun",
                 word_category="proper_name")
    enc = Lemma(lemma_ar="محرك", lemma_ar_bare="محرك", gloss_en="engine", pos="noun")
    db_session.add_all([known_solid, known_shaky, acquiring, name, enc])
    db_session.flush()
    db_session.add_all([
        UserLemmaKnowledge(lemma_id=known_solid.lemma_id, knowledge_state="known",
                           fsrs_card_json=_known_card(now + timedelta(days=30), 60.0)),
        UserLemmaKnowledge(lemma_id=known_shaky.lemma_id, knowledge_state="known",
                           fsrs_card_json=_known_card(now - timedelta(days=20), 2.0)),
        UserLemmaKnowledge(lemma_id=acquiring.lemma_id, knowledge_state="acquiring",
                           acquisition_box=1),
        UserLemmaKnowledge(lemma_id=enc.lemma_id, knowledge_state="encountered"),
    ])
    db_session.commit()

    words = [
        _word("بيت", lemma_id=known_solid.lemma_id, count=3, rank=200),
        _word("حدود", lemma_id=known_shaky.lemma_id, count=4, rank=800),
        _word("سيارة", lemma_id=acquiring.lemma_id),
        _word("مروان", lemma_id=name.lemma_id, count=9),
        _word("ماكرون", count=5, proper=True, gloss="Macron"),
        _word("محرك", lemma_id=enc.lemma_id, count=4, rank=4336),
        _word("مبضع", count=1, rank=None),          # rare, once, importance 1 -> 3.0
        _word("زخرف", count=3, rank=900),           # rated 0: wrong sense / decorative
        _word("غامض", count=1, rank=None, gloss=""),  # no gloss: cannot be created
    ]
    monkeypatch.setattr("app.routers.discover.discover_words_in_text", lambda *a, **k: [dict(w) for w in words])
    ratings = {"حدود": 2, "محرك": 2, "مبضع": 1, "زخرف": 0}

    def fake_rate(text, items):
        return {it["index"]: ratings.get(it["lemma_ar"], 1) for it in items}
    monkeypatch.setattr(ps, "rate_importance", fake_rate)

    plan = ps.analyze_pages(db_session, ["محرك بيت حدود", "مبضع زخرف ماكرون مروان"])
    learn = [w["lemma_ar_bare"] for w in plan["learn"]]
    assert learn == ["محرك", "مبضع"]  # page order; زخرف dropped (importance 0)
    assert [w["lemma_ar_bare"] for w in plan["shaky"]] == ["حدود"]
    assert {w["lemma_ar_bare"] for w in plan["names"]} == {"مروان", "ماكرون"}
    assert plan["learn"][1]["first_page"] == 2
    s = plan["stats"]
    assert (s["solid"], s["acquiring"], s["no_gloss"]) == (1, 1, 1)


def test_analyze_without_ratings_falls_back_to_count_and_frequency(db_session, monkeypatch):
    words = [_word("محرك", count=4, rank=4336), _word("مبضع", count=1)]
    monkeypatch.setattr("app.routers.discover.discover_words_in_text", lambda *a, **k: [dict(w) for w in words])
    monkeypatch.setattr(ps, "rate_importance", lambda text, items: {})
    plan = ps.analyze_pages(db_session, ["محرك مبضع"])
    assert [w["lemma_ar_bare"] for w in plan["learn"]] == ["محرك", "مبضع"]


def test_pull_forward_only_moves_future_fsrs_due(db_session):
    now = datetime.now(timezone.utc)
    future = Lemma(lemma_ar="ا", lemma_ar_bare="اا", gloss_en="a", pos="noun")
    past = Lemma(lemma_ar="ب", lemma_ar_bare="بب", gloss_en="b", pos="noun")
    acq = Lemma(lemma_ar="ج", lemma_ar_bare="جج", gloss_en="c", pos="noun")
    db_session.add_all([future, past, acq])
    db_session.flush()
    db_session.add_all([
        UserLemmaKnowledge(lemma_id=future.lemma_id, knowledge_state="known",
                           fsrs_card_json=_known_card(now + timedelta(days=9), 10.0)),
        UserLemmaKnowledge(lemma_id=past.lemma_id, knowledge_state="known",
                           fsrs_card_json=_known_card(now - timedelta(days=1), 10.0)),
        UserLemmaKnowledge(lemma_id=acq.lemma_id, knowledge_state="acquiring", acquisition_box=2),
    ])
    db_session.commit()
    moved = ps.pull_forward(db_session, [future.lemma_id, past.lemma_id, acq.lemma_id])
    db_session.commit()
    assert moved == [future.lemma_id]
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=future.lemma_id).one()
    due = datetime.fromisoformat(ulk.fsrs_card_json["due"])
    assert due <= datetime.now(timezone.utc)


def test_upload_and_start_flow(client, db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "_image_dir", lambda sid: tmp_path / str(sid))
    started = []
    monkeypatch.setattr("fastapi.BackgroundTasks.add_task",
                        lambda self, fn, *a, **k: started.append((fn, a)))
    sid = client.post("/api/preread").json()["preread_id"]
    assert client.post(f"/api/preread/{sid}/start").status_code == 400  # no pages yet
    for i in range(2):
        r = client.post(f"/api/preread/{sid}/pages", files={"file": (f"{i}.jpg", b"img", "image/jpeg")})
        assert r.status_code == 200
    assert r.json()["page_count"] == 2
    r = client.post(f"/api/preread/{sid}/start")
    assert r.json()["status"] == "reading"
    assert started == [(pr.run_preread, (sid,))]
    # A running job is not started twice; pages cannot be added after start.
    client.post(f"/api/preread/{sid}/start")
    assert len(started) == 1
    r = client.post(f"/api/preread/{sid}/pages", files={"file": ("x.jpg", b"img", "image/jpeg")})
    assert r.status_code == 409
    # A job that stopped moving (server restart) may be started again.
    row = db_session.get(PrereadSet, sid)
    row.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()
    client.post(f"/api/preread/{sid}/start")
    assert len(started) == 2


def test_run_preread_reads_pages_and_enters_words_outside_budget(client, db_session, monkeypatch, tmp_path):
    from app.services import acquisition_service

    now = datetime.now(timezone.utc)
    shaky = Lemma(lemma_ar="حُدُود", lemma_ar_bare="حدود", gloss_en="borders", pos="noun")
    enc = Lemma(lemma_ar="مُحَرِّك", lemma_ar_bare="محرك", gloss_en="engine", pos="noun")
    db_session.add_all([shaky, enc])
    db_session.flush()
    db_session.add_all([
        UserLemmaKnowledge(lemma_id=shaky.lemma_id, knowledge_state="known",
                           fsrs_card_json=_known_card(now + timedelta(days=5), 3.0)),
        UserLemmaKnowledge(lemma_id=enc.lemma_id, knowledge_state="encountered"),
    ])
    db_session.commit()

    images = tmp_path / "imgs"
    images.mkdir()
    pages = []
    for i, text in enumerate(["المحرك تعطل", "على الحدود"], 1):
        f = images / f"{i}.jpg"
        f.write_bytes(text.encode())
        pages.append({"page": i, "image": str(f), "snap_id": None, "error": None})
    row = PrereadSet(status="reading", pages_json=pages)
    db_session.add(row)
    db_session.commit()
    sid = row.id

    monkeypatch.setattr(pr, "_image_dir", lambda s: images)
    monkeypatch.setattr("app.services.ocr_service.extract_text_and_translation",
                        lambda b: {"arabic_text": b.decode(), "translation_en": "en", "sentences": []})
    # Recovery mode: the ordinary budget is zero.
    monkeypatch.setattr(acquisition_service, "_recovery_mode_intro_budget", lambda *a: 0)
    material = []
    monkeypatch.setattr(pr, "_prepare_material", lambda new, ex: material.append((new, ex)))
    seen_texts = []

    def fake_analyze(db, texts):
        seen_texts.extend(texts)
        return {
            "learn": [_word("محرك", lemma_id=enc.lemma_id, count=1, gloss="engine"),
                      _word("تعطل", count=1, gloss="to break down")],
            "shaky": [_word("حدود", lemma_id=shaky.lemma_id, count=1)],
            "names": [],
            "stats": {"pages": len(texts), "learn": 2, "shaky": 1},
        }
    monkeypatch.setattr(pr, "analyze_pages", fake_analyze)

    pr.run_preread(sid)

    db_session.expire_all()
    row = db_session.get(PrereadSet, sid)
    assert row.status == "done", row.error
    assert seen_texts == ["المحرك تعطل", "على الحدود"]  # page order
    assert db_session.query(SnapPage).count() == 2
    assert all(p["snap_id"] for p in row.pages_json)
    assert not images.exists()  # photos are deleted once read

    for bare in ("محرك", "تعطل"):
        lem = db_session.query(Lemma).filter_by(lemma_ar_bare=bare).one()
        ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lem.lemma_id).one()
        assert (ulk.knowledge_state, ulk.source) == ("acquiring", "preread")
    stats = row.result_json["stats"]
    assert (stats["added"], stats["created"], stats["pulled_forward"]) == (2, 1, 1)
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=shaky.lemma_id).one()
    assert datetime.fromisoformat(ulk.fsrs_card_json["due"]) <= datetime.now(timezone.utc)
    new_ids, existing_ids = material[0]
    assert len(new_ids) == 1 and existing_ids == [enc.lemma_id]

    detail = client.get(f"/api/preread/{sid}").json()
    assert detail["pages_read"] == 2 and detail["result"]["stats"]["added"] == 2
    assert client.get("/api/preread").json()["prereads"][0]["preread_id"] == sid


def test_run_preread_fails_cleanly_when_no_page_is_readable(db_session, monkeypatch, tmp_path):
    f = tmp_path / "1.jpg"
    f.write_bytes(b"x")
    row = PrereadSet(status="reading", pages_json=[{"page": 1, "image": str(f), "snap_id": None, "error": None}])
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr("app.services.ocr_service.extract_text_and_translation",
                        lambda b: {"arabic_text": "", "translation_en": ""})
    pr.run_preread(row.id)
    db_session.expire_all()
    row = db_session.get(PrereadSet, row.id)
    assert row.status == "failed"
    assert row.pages_json[0]["error"] == "No Arabic text found"
