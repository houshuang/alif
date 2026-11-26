"""Add contexts: the sentence a word was added from, and the later check."""
from app.models import Lemma, UserLemmaKnowledge, WordAddContext
from app.routers import discover
from app.services import add_context, lookup_sense, preread_service as ps
from app.services.lookup_sense import validate_verdict


def _lemma(db, ar, bare, gloss, pos, state=None):
    lem = Lemma(lemma_ar=ar, lemma_ar_bare=bare, gloss_en=gloss, pos=pos, source="frequency_core")
    db.add(lem)
    db.flush()
    if state:
        db.add(UserLemmaKnowledge(lemma_id=lem.lemma_id, knowledge_state=state))
    db.commit()
    return lem


def _gated(v, res):
    """What check_lookup_sense returns after the gloss gate: a widening is
    approved unless the test says otherwise."""
    if v and v["verdict"] == "broader":
        v["approved"] = res.get("approved", True)
    return v


def _stub_sense(monkeypatch, res):
    monkeypatch.setattr(lookup_sense, "check_lookup_sense",
                        lambda sentence, surface, entry: _gated(validate_verdict(res, entry), res))


def test_add_records_context_and_schedules_check(client, db_session, monkeypatch):
    monkeypatch.setattr(discover, "_gate_and_generate", lambda ids: None)
    from starlette.background import BackgroundTasks

    scheduled = []
    monkeypatch.setattr(BackgroundTasks, "add_task",
                        lambda self, func, *args, **kw: scheduled.append((func, args)))
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "منفى", "lemma_ar": "مَنْفَى", "gloss_en": "exile", "pos": "noun",
        "source": "snap", "surface": "المنفى", "context_ar": "عاش في المنفى سنوات", "snap_id": 7})
    body = r.json()
    row = db_session.get(WordAddContext, body["context_id"])
    assert (row.lemma_id, row.source, row.surface, row.snap_id) == (body["lemma_id"], "snap", "المنفى", 7)
    assert row.sentence_ar == "عاش في المنفى سنوات" and row.gloss_at_add == "exile"
    assert scheduled[0] == (discover.verify_add_contexts, ([row.id],))


def test_add_without_context_records_nothing(client, db_session, monkeypatch):
    monkeypatch.setattr(discover, "_gate_and_generate", lambda ids: None)
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "رصيف", "lemma_ar": "رَصِيف", "gloss_en": "pavement", "pos": "noun"})
    assert r.json()["context_id"] is None
    assert db_session.query(WordAddContext).count() == 0


def _context(db, lem, sentence="كانت لها الحصة الأكبر"):
    row = WordAddContext(lemma_id=lem.lemma_id, source="snap", surface="الحصة",
                         sentence_ar=sentence, gloss_at_add=lem.gloss_en)
    db.add(row)
    db.commit()
    return row.id


def test_verify_broader_widens_gloss(db_session, monkeypatch):
    lem = _lemma(db_session, "حِصَّة", "حصة", "class period", "noun", "acquiring")
    cid = _context(db_session, lem)
    _stub_sense(monkeypatch, {"verdict": "broader", "gloss_en": "share, portion; class period"})
    assert add_context.verify_add_contexts([cid]) == {"broader": 1}
    db_session.expire_all()
    assert db_session.get(Lemma, lem.lemma_id).gloss_en == "share, portion; class period"
    row = db_session.get(WordAddContext, cid)
    assert row.check_verdict == "broader" and row.check_json["written"] is True
    # A checked row is not checked again.
    assert add_context.verify_add_contexts([cid]) == {}


def test_verify_different_is_recorded_not_applied(db_session, monkeypatch):
    lem = _lemma(db_session, "مَاضٍ", "ماض", "past", "adj", "acquiring")
    cid = _context(db_session, lem, "فقد أمضت ليالي")
    _stub_sense(monkeypatch, {"verdict": "different", "lemma_ar": "أَمْضَى",
                              "gloss_en": "to spend (time)", "pos": "verb"})
    assert add_context.verify_add_contexts([cid]) == {"different": 1}
    db_session.expire_all()
    assert db_session.get(Lemma, lem.lemma_id).gloss_en == "past"
    row = db_session.get(WordAddContext, cid)
    assert row.check_verdict == "different" and row.check_json["lemma_ar"] == "أَمْضَى"


def test_verify_failure_leaves_row_unchecked(db_session, monkeypatch):
    lem = _lemma(db_session, "وَقْت", "وقت", "time", "noun")
    cid = _context(db_session, lem)
    _stub_sense(monkeypatch, None)
    assert add_context.verify_add_contexts([cid]) == {"failed": 1}
    db_session.expire_all()
    assert db_session.get(WordAddContext, cid).checked_at is None


def test_preread_holds_back_matched_words_when_check_fails(monkeypatch):
    monkeypatch.setattr(ps, "check_senses", lambda text, items, **kw: None)
    learn = [{"lemma_id": 5, "lemma_ar": "مَاضٍ", "surface": "أمضت"},
             {"lemma_id": None, "lemma_ar": "جَدِيد", "surface": "جديد"}]
    shaky = [{"lemma_id": 6, "lemma_ar": "قَرْن", "surface": "القرن"}]
    out_learn, out_shaky, n_checked, held = ps._apply_senses("نص", learn, shaky)
    assert [w["surface"] for w in out_learn] == ["جديد"]
    assert out_shaky == [] and (n_checked, held) == (0, 2)


def test_snap_chips_sense_check(db_session, monkeypatch):
    wrong = _lemma(db_session, "مَاضٍ", "ماض", "past", "adj")
    narrow = _lemma(db_session, "حِصَّة", "حصة", "class period", "noun")
    learning_wrong = _lemma(db_session, "أَب", "اب", "father", "noun")
    _lemma(db_session, "اِبْن", "ابن", "son", "noun", "known")
    words = [
        {"surface": "أمضت", "lemma_ar": "مَاضٍ", "gloss_en": "past", "pos": "adj", "lemma_id": wrong.lemma_id},
        {"surface": "الحصة", "lemma_ar": "حِصَّة", "gloss_en": "class period", "pos": "noun",
         "lemma_id": narrow.lemma_id},
        {"surface": "ابنها", "lemma_ar": "أَب", "gloss_en": "father", "pos": "noun",
         "lemma_id": learning_wrong.lemma_id},
        {"surface": "منفى", "lemma_ar": "مَنْفَى", "gloss_en": "exile", "pos": "noun", "lemma_id": None},
    ]

    def fake(text, items, **kw):
        by = {it["lemma_ar"]: it["index"] for it in items}
        return {
            by["مَاضٍ"]: {"verdict": "different", "lemma_ar": "أَمْضَى", "gloss_en": "to spend (time)", "pos": "verb"},
            by["حِصَّة"]: {"verdict": "broader", "gloss_en": "share; class period", "approved": True},
            by["أَب"]: {"verdict": "different", "lemma_ar": "اِبْن", "gloss_en": "son", "pos": "noun"},
        }
    monkeypatch.setattr(ps, "check_senses", fake)
    out = discover._sense_check_chips(db_session, "نص", words)
    assert [w["surface"] for w in out] == ["أمضت", "الحصة", "منفى"]  # ابن is already known
    assert out[0]["lemma_ar"] == "أَمْضَى" and out[0]["lemma_id"] is None
    assert out[0]["sense_from"]["lemma_id"] == wrong.lemma_id
    assert out[1]["gloss_en"] == "share; class period"
    db_session.expire_all()
    assert db_session.get(Lemma, narrow.lemma_id).gloss_en == "share; class period"
