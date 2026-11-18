"""Added words whose saved sentence uses a different word: list, swap, keep."""
from starlette.background import BackgroundTasks

from app.models import Lemma, UserLemmaKnowledge, WordAddContext
from app.routers import discover


def _lemma(db, ar, bare, gloss, pos, state=None, box=None):
    lem = Lemma(lemma_ar=ar, lemma_ar_bare=bare, gloss_en=gloss, pos=pos, source="frequency_core")
    db.add(lem)
    db.flush()
    if state:
        db.add(UserLemmaKnowledge(lemma_id=lem.lemma_id, knowledge_state=state, acquisition_box=box))
    db.commit()
    return lem


def _different(db, lem, proposed_ar, gloss, pos, introduced=True):
    row = WordAddContext(
        lemma_id=lem.lemma_id, source="snap", surface="أمضت", sentence_ar="فقد أمضت ليالي طويلة",
        gloss_at_add=lem.gloss_en, introduced_by_add=introduced, check_verdict="different",
        check_json={"verdict": "different", "lemma_ar": proposed_ar, "gloss_en": gloss,
                    "pos": pos, "checked_lemma_id": lem.lemma_id},
    )
    db.add(row)
    db.commit()
    return row.id


def _no_background(monkeypatch):
    scheduled = []
    monkeypatch.setattr(BackgroundTasks, "add_task",
                        lambda self, func, *args, **kw: scheduled.append((func, args)))
    return scheduled


def test_list_shows_added_and_proposed(client, db_session):
    wrong = _lemma(db_session, "مَاضٍ", "ماض", "past", "adj", "acquiring", 1)
    cid = _different(db_session, wrong, "أَمْضَى", "to spend (time)", "verb")
    fits = WordAddContext(lemma_id=wrong.lemma_id, source="snap", sentence_ar="x", check_verdict="fits")
    db_session.add(fits)
    db_session.commit()
    body = client.get("/api/discover/add-checks").json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["id"] == cid and item["surface"] == "أمضت"
    assert item["added"]["lemma_id"] == wrong.lemma_id and item["added"]["knowledge_state"] == "acquiring"
    assert item["proposed"]["lemma_id"] is None and item["proposed"]["lemma_ar"] == "أَمْضَى"


def test_swap_creates_proposed_and_releases_wrong(client, db_session, monkeypatch):
    scheduled = _no_background(monkeypatch)
    wrong = _lemma(db_session, "مَاضٍ", "ماض", "past", "adj", "acquiring", 1)
    cid = _different(db_session, wrong, "أَمْضَى", "to spend (time)", "verb")
    r = client.post(f"/api/discover/add-checks/{cid}", json={"action": "swap"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True and body["released_lemma_id"] == wrong.lemma_id
    db_session.expire_all()
    new = db_session.get(Lemma, body["lemma_id"])
    assert (new.lemma_ar, new.gloss_en) == ("أَمْضَى", "to spend (time)")
    new_ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=new.lemma_id).one()
    assert new_ulk.knowledge_state == "acquiring"
    old_ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=wrong.lemma_id).one()
    assert old_ulk.knowledge_state == "encountered" and old_ulk.acquisition_box is None
    row = db_session.get(WordAddContext, cid)
    assert row.resolution == "swapped" and row.check_json["swapped_to"] == new.lemma_id
    # The new word keeps the sentence too, and gets checked and gated.
    new_ctx = db_session.query(WordAddContext).filter_by(lemma_id=new.lemma_id).one()
    assert new_ctx.sentence_ar == "فقد أمضت ليالي طويلة"
    funcs = [f for f, _ in scheduled]
    assert discover.verify_add_contexts in funcs and discover._gate_and_generate in funcs
    # Resolved rows leave the list and cannot be resolved twice.
    assert client.get("/api/discover/add-checks").json()["count"] == 0
    assert client.post(f"/api/discover/add-checks/{cid}", json={"action": "swap"}).status_code == 409


def test_swap_to_existing_word_keeps_studied_wrong_word(client, db_session, monkeypatch):
    _no_background(monkeypatch)
    wrong = _lemma(db_session, "مَاضٍ", "ماض", "past", "adj", "known")
    right = _lemma(db_session, "أَمْضَى", "امضى", "to spend (time)", "verb")
    cid = _different(db_session, wrong, "أَمْضَى", "to spend (time)", "verb", introduced=False)
    body = client.post(f"/api/discover/add-checks/{cid}", json={"action": "swap"}).json()
    assert body["lemma_id"] == right.lemma_id and body["created"] is False
    assert body["released_lemma_id"] is None
    db_session.expire_all()
    assert db_session.query(UserLemmaKnowledge).filter_by(lemma_id=wrong.lemma_id).one().knowledge_state == "known"
    assert db_session.query(UserLemmaKnowledge).filter_by(lemma_id=right.lemma_id).one().knowledge_state == "acquiring"


def test_keep_only_records(client, db_session):
    wrong = _lemma(db_session, "مَاضٍ", "ماض", "past", "adj", "acquiring", 1)
    cid = _different(db_session, wrong, "أَمْضَى", "to spend (time)", "verb")
    assert client.post(f"/api/discover/add-checks/{cid}", json={"action": "keep"}).json()["action"] == "keep"
    db_session.expire_all()
    assert db_session.get(WordAddContext, cid).resolution == "kept"
    assert db_session.query(UserLemmaKnowledge).filter_by(lemma_id=wrong.lemma_id).one().knowledge_state == "acquiring"
    assert db_session.query(Lemma).count() == 1


def test_add_records_whether_it_started_the_word(client, db_session, monkeypatch):
    _no_background(monkeypatch)
    _lemma(db_session, "مَنْفَى", "منفى", "exile", "noun", "known")
    known = client.post("/api/discover/add", json={
        "lemma_ar_bare": "منفى", "lemma_ar": "مَنْفَى", "gloss_en": "exile", "pos": "noun",
        "source": "snap", "surface": "المنفى", "context_ar": "عاش في المنفى"}).json()
    fresh = client.post("/api/discover/add", json={
        "lemma_ar_bare": "رصيف", "lemma_ar": "رَصِيف", "gloss_en": "pavement", "pos": "noun",
        "source": "snap", "surface": "الرصيف", "context_ar": "وقف على الرصيف"}).json()
    assert db_session.get(WordAddContext, known["context_id"]).introduced_by_add is False
    assert db_session.get(WordAddContext, fresh["context_id"]).introduced_by_add is True
