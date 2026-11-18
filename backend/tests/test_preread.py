"""Pre-read: automatic word plan for upcoming pages (services/preread_service.py,
routers/preread.py)."""
from datetime import datetime, timedelta, timezone

import pytest


from app.models import Lemma, PrereadSet, SnapPage, UserLemmaKnowledge
from app.routers import preread as pr
from app.services import preread_service as ps
from app.services.attention_policy import LEARNER_PICK_SOURCES
from app.services.fsrs_service import create_new_card


_real_check_senses = ps.check_senses


@pytest.fixture(autouse=True)
def _senses_fit(monkeypatch):
    monkeypatch.setattr(ps, "check_senses", lambda text, items: {})


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
    monkeypatch.setattr(pr, "_prepare_material", lambda new, ex, hooks=None: material.append((new, ex)))
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


def test_active_priority_ids_cover_recent_batches_only(db_session):
    now = datetime.now(timezone.utc)
    db_session.add_all([
        PrereadSet(status="done", finished_at=now - timedelta(days=1), result_json={
            "learn": [{"lemma_id": 1, "added": True}, {"lemma_id": 2, "added": False}],
            "shaky": [{"lemma_id": 3}],
            "names": [{"lemma_id": 4}, {"lemma_id": 7, "added": True}]}),
        PrereadSet(status="done", finished_at=now - timedelta(days=ps.PRIORITY_DAYS + 1),
                   result_json={"learn": [{"lemma_id": 5, "added": True}], "shaky": []}),
        PrereadSet(status="failed", finished_at=now, result_json={"learn": [{"lemma_id": 6, "added": True}]}),
    ])
    db_session.commit()
    assert ps.active_priority_ids(db_session, now) == {1, 3, 7}


def test_unseen_words_keep_priority_after_the_batch_window(db_session):
    now = datetime.now(timezone.utc)
    lemmas = [Lemma(lemma_ar=f"كلمة{i}", lemma_ar_bare=f"كلمة{i}", gloss_en=f"w{i}", pos="noun") for i in range(4)]
    db_session.add_all(lemmas)
    db_session.commit()
    unseen, seen, graduated, stale = (l.lemma_id for l in lemmas)
    db_session.add_all([
        UserLemmaKnowledge(lemma_id=unseen, knowledge_state="acquiring", times_seen=0),
        UserLemmaKnowledge(lemma_id=seen, knowledge_state="acquiring", times_seen=1),
        UserLemmaKnowledge(lemma_id=graduated, knowledge_state="known", times_seen=0),
        UserLemmaKnowledge(lemma_id=stale, knowledge_state="acquiring", times_seen=0),
        PrereadSet(status="done", finished_at=now - timedelta(days=ps.PRIORITY_DAYS + 1), result_json={
            "learn": [{"lemma_id": unseen, "added": True}, {"lemma_id": seen, "added": True}],
            "names": [{"lemma_id": graduated, "added": True}],
            "shaky": [{"lemma_id": 999}]}),
        PrereadSet(status="done", finished_at=now - timedelta(days=ps.UNSEEN_PRIORITY_DAYS + 1),
                   result_json={"learn": [{"lemma_id": stale, "added": True}]}),
    ])
    db_session.commit()
    assert ps.active_priority_ids(db_session, now) == {unseen}


def test_same_lemma_under_two_keys_is_one_candidate(db_session, monkeypatch):
    lem = Lemma(lemma_ar="لَفَّ", lemma_ar_bare="لف", gloss_en="to wrap", pos="verb")
    db_session.add(lem)
    db_session.commit()
    words = [_word("لف", lemma_id=lem.lemma_id, count=1, surface="لف"),
             _word("يلف", lemma_id=lem.lemma_id, count=2, surface="يلف")]
    monkeypatch.setattr("app.routers.discover.discover_words_in_text", lambda *a, **k: [dict(w) for w in words])
    monkeypatch.setattr(ps, "rate_importance", lambda text, items: {it["index"]: 2 for it in items})
    plan = ps.analyze_pages(db_session, ["لف يلف يلف"])
    assert len(plan["learn"]) == 1
    assert plan["learn"][0]["count_in_text"] == 3


def test_practice_names_enters_names_as_learner_picks(db_session):
    stored = Lemma(lemma_ar="مروان", lemma_ar_bare="مروان", gloss_en="Marwan", pos="noun_prop",
                   word_category="proper_name")
    word = Lemma(lemma_ar="أمل", lemma_ar_bare="امل", gloss_en="hope", pos="noun")
    db_session.add_all([stored, word])
    db_session.commit()
    names = [
        _word("مروان", lemma_id=stored.lemma_id, gloss="Marwan", proper=True),
        _word("ماكرون", gloss="Macron", proper=True),
        _word("امل", gloss="Amal", proper=True),
        _word("زيد", gloss="", proper=True),
    ]

    out = ps.practice_names(db_session, names)

    db_session.expire_all()
    assert db_session.get(Lemma, stored.lemma_id).word_category == ps.PRACTICED_NAME_CATEGORY
    created = db_session.query(Lemma).filter_by(lemma_ar_bare="ماكرون").one()
    assert created.word_category == ps.PRACTICED_NAME_CATEGORY
    assert created.source == ps.PREREAD_SOURCE
    for lid in (stored.lemma_id, created.lemma_id):
        ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lid).one()
        assert ulk.knowledge_state == "acquiring"
        assert ulk.source == ps.PREREAD_SOURCE
    assert set(out["ids"]) == {stored.lemma_id, created.lemma_id}
    assert created.lemma_id in out["gate_ids"]
    # A name spelled like an ordinary word leaves that word alone.
    assert db_session.get(Lemma, word.lemma_id).word_category is None
    assert names[2]["skipped"] == "ordinary word" and not names[2]["added"]
    # No gloss, no lemma.
    assert names[3]["skipped"] == "no gloss"
    assert db_session.query(Lemma).filter_by(lemma_ar_bare="زيد").count() == 0
    assert [n["added"] for n in names] == [True, True, False, False]



def test_sense_check_widens_glosses_and_reroutes_homographs(db_session, monkeypatch):
    """A stored gloss lacking the page's sense is widened; a page word that is a
    different lexeme with the same spelling leaves the refresh list and is
    re-routed to that lexeme when the plan is applied."""
    from app.services import acquisition_service

    now = datetime.now(timezone.utc)
    qarn = Lemma(lemma_ar="قَرْن", lemma_ar_bare="قرن", gloss_en="horn", pos="noun",
                 memory_hooks_json={"mnemonic": "a rhino horn"})
    baptize = Lemma(lemma_ar="عَمَّدَ", lemma_ar_bare="عمد", gloss_en="to baptize", pos="verb")
    db_session.add_all([qarn, baptize])
    db_session.flush()
    db_session.add_all([
        UserLemmaKnowledge(lemma_id=qarn.lemma_id, knowledge_state="known",
                           fsrs_card_json=_known_card(now - timedelta(days=20), 2.0)),
        UserLemmaKnowledge(lemma_id=baptize.lemma_id, knowledge_state="known",
                           fsrs_card_json=_known_card(now - timedelta(days=20), 2.0)),
    ])
    db_session.commit()
    qarn_id, baptize_id = qarn.lemma_id, baptize.lemma_id

    words = [_word("قرن", lemma_id=qarn_id, count=3, rank=900, gloss="horn"),
             _word("عمد", lemma_id=baptize_id, count=2, rank=3000, gloss="to baptize")]
    words[1]["pos"] = "verb"
    monkeypatch.setattr("app.routers.discover.discover_words_in_text", lambda *a, **k: [dict(w) for w in words])
    monkeypatch.setattr(ps, "rate_importance", lambda text, items: {it["index"]: 2 for it in items})

    def fake_senses(text, items):
        by = {it["lemma_ar"]: it["index"] for it in items}
        return {
            by["قرن"]: {"verdict": "broader", "gloss_en": "century; horn", "lemma_ar": None, "pos": None},
            by["عمد"]: {"verdict": "different", "gloss_en": "to set about, proceed deliberately",
                       "lemma_ar": "عَمَدَ", "pos": "verb"},
        }
    monkeypatch.setattr(ps, "check_senses", fake_senses)

    plan = ps.analyze_pages(db_session, ["القرن التاسع عشر عمد إلى"])
    assert [w["lemma_ar_bare"] for w in plan["shaky"]] == ["قرن"]
    assert plan["shaky"][0]["regloss"] == {"from": "horn", "to": "century; horn"}
    assert [w["lemma_ar"] for w in plan["learn"]] == ["عَمَدَ"]
    assert plan["learn"][0]["lemma_ar_bare"] == "عمد"
    assert plan["learn"][0]["sense_from"]["lemma_id"] == baptize_id
    assert (plan["stats"]["reglossed"], plan["stats"]["sense_rerouted"]) == (1, 1)

    monkeypatch.setattr(acquisition_service, "_recovery_mode_intro_budget", lambda *a: 0)
    applied = pr._apply_plan(plan)

    db_session.expire_all()
    q = db_session.get(Lemma, qarn_id)
    assert q.gloss_en == "century; horn" and q.memory_hooks_json is None
    assert applied["hook_ids"] == [qarn_id]
    new = plan["learn"][0]
    assert new["added"] and new["lemma_id"] not in (None, baptize_id)
    assert db_session.get(Lemma, new["lemma_id"]).gloss_en == "to set about, proceed deliberately"
    assert db_session.get(Lemma, baptize_id).gloss_en == "to baptize"


def test_regloss_yields_to_a_concurrent_edit(db_session):
    lem = Lemma(lemma_ar="عُضْو", lemma_ar_bare="عضو", gloss_en="member (edited)", pos="noun")
    db_session.add(lem)
    db_session.commit()
    w = {"lemma_id": lem.lemma_id, "regloss": {"from": "organ", "to": "member; organ"}}
    assert ps.regloss(db_session, [w]) == []
    assert db_session.get(Lemma, lem.lemma_id).gloss_en == "member (edited)"
    assert not w.get("reglossed")


def test_check_senses_drops_no_op_verdicts(monkeypatch):
    items = [
        {"index": 0, "lemma_ar": "عُثْمانِيّ", "gloss_en": "Ottoman", "pos": "adj", "forms": ["العثمانية"]},
        {"index": 1, "lemma_ar": "تَزَامَنَ", "gloss_en": "to coincide", "pos": "verb", "forms": ["تزامن"]},
        {"index": 2, "lemma_ar": "قَرْنٌ", "gloss_en": "horn", "pos": "noun", "forms": ["القرن"]},
        {"index": 3, "lemma_ar": "عَمَّدَ", "gloss_en": "to baptize", "pos": "verb", "forms": ["عمد"]},
        {"index": 4, "lemma_ar": "ذَاتِيّ", "gloss_en": "personal", "pos": "adj", "forms": ["الذاتية"]},
        {"index": 5, "lemma_ar": "قَرْن", "gloss_en": "horn", "pos": "noun", "forms": ["القرن"]},
    ]
    reply = {"mismatches": [
        {"index": 0, "verdict": "broader", "gloss_en": "ottoman"},
        {"index": 1, "verdict": "different", "lemma_ar": "تَزَامَنَ", "gloss_en": "to coincide", "pos": "verb"},
        {"index": 2, "verdict": "different", "lemma_ar": "قَرْن", "gloss_en": "century", "pos": "noun"},
        {"index": 3, "verdict": "different", "lemma_ar": "عَمَدَ", "gloss_en": "to set about", "pos": "verb"},
        {"index": 9, "verdict": "broader", "gloss_en": "stray index"},
        {"index": 4, "verdict": "broader", "gloss_en": "biography-related: ذاتي = self"},
        {"index": 5, "verdict": "broader", "gloss_en": "century"},
    ]}
    prompts = []

    def fake(**kw):
        prompts.append(kw["prompt"])
        return reply
    monkeypatch.setattr("app.services.llm.generate_completion", fake)
    out = _real_check_senses("نص", items)
    assert list(out) == [3] and out[3]["lemma_ar"] == "عَمَدَ"
    assert "[in the pages: العثمانية]" in prompts[0]
