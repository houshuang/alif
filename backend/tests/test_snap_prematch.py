"""Background page pre-match for Snap (app/services/snap_prematch.py)."""
from app.models import Lemma, SnapPage, UserLemmaKnowledge
from app.routers import discover
from app.services import preread_service, snap_prematch


def _lemma(db, ar, bare, gloss, pos, state=None):
    lem = Lemma(lemma_ar=ar, lemma_ar_bare=bare, gloss_en=gloss, pos=pos, source="frequency_core")
    db.add(lem)
    db.flush()
    if state:
        db.add(UserLemmaKnowledge(lemma_id=lem.lemma_id, knowledge_state=state))
    db.commit()
    return lem


def _page(db, text):
    page = SnapPage(arabic_text=text, translation_en="t")
    db.add(page)
    db.commit()
    return page


def _stub_checks(monkeypatch, by_form, calls=None):
    """check_senses stub: by_form maps a surface to a raw mismatch row, or to
    "fail" to fail that surface's whole chunk."""
    def fake(text, items):
        if calls is not None:
            calls.append([it["forms"][0] for it in items])
        if any(by_form.get(it["forms"][0]) == "fail" for it in items):
            return None
        return {it["index"]: by_form[it["forms"][0]] for it in items if it["forms"][0] in by_form}
    monkeypatch.setattr(preread_service, "check_senses", fake)


def _stub_gloss(monkeypatch, glosses):
    def fake(items):
        return {it["index"]: {"gloss_en": glosses.get(it["bare"], ""), "pos": "noun",
                              "transliteration": "t", "is_proper_noun": False} for it in items}
    monkeypatch.setattr(discover, "_gloss", fake)


def test_each_surface_is_checked_on_its_own(db_session, monkeypatch):
    """Two surfaces the resolver put under one entry get separate verdicts: one
    fits, the other is a different word that is already in the vocabulary
    (as عيون and عيّنت both resolved to عَيْن on a real page)."""
    past = _lemma(db_session, "مَاضٍ", "ماض", "past", "adj", "known")
    spend = _lemma(db_session, "أَمْضَى", "امضى", "to spend (time)", "verb")
    page = _page(db_session, "الماضي أمضت")
    monkeypatch.setattr(discover, "lookup_lemma_id", lambda tok, *a, **k: past.lemma_id
                        if tok in ("الماضي", "أمضت") else None)
    calls = []
    _stub_checks(monkeypatch, {"أمضت": {"index": 1, "verdict": "different", "lemma_ar": "أَمْضَى",
                                        "gloss_en": "to spend (time)", "pos": "verb"}}, calls)
    _stub_gloss(monkeypatch, {})

    out = snap_prematch.prematch_page(page.id)

    assert sorted(calls[0]) == sorted(["الماضي", "أمضت"])
    assert out["status"] == "ready"
    assert out["words"]["الماضي"]["sense"] == {"verdict": "fits"}
    assert out["words"]["الماضي"]["word"]["lemma_id"] == past.lemma_id
    diff = out["words"]["أمضت"]["sense"]
    assert diff["verdict"] == "different"
    assert diff["word"]["lemma_id"] == spend.lemma_id


def test_broader_is_a_proposal_and_new_words_are_glossed(db_session, monkeypatch):
    lem = _lemma(db_session, "حِصَّة", "حصة", "class period", "noun", "known")
    page = _page(db_session, "الحصة دستور")
    _stub_checks(monkeypatch, {"الحصة": {"index": 0, "verdict": "broader",
                                         "gloss_en": "share, portion; class period"}})
    _stub_gloss(monkeypatch, {"دستور": "constitution"})

    out = snap_prematch.prematch_page(page.id)

    sense = out["words"]["الحصة"]["sense"]
    assert sense["verdict"] == "broader" and sense["written"] is False
    assert sense["gloss_en"] == "share, portion; class period"
    db_session.refresh(lem)
    assert lem.gloss_en == "class period"  # the stored gloss is never written
    new = out["words"]["دستور"]
    assert "sense" not in new
    assert new["word"]["gloss_en"] == "constitution"
    assert new["word"]["lemma_id"] is None


def test_failed_check_and_failed_gloss_leave_words_to_the_tap(db_session, monkeypatch):
    _lemma(db_session, "حِصَّة", "حصة", "class period", "noun")
    page = _page(db_session, "الحصة دستور")
    _stub_checks(monkeypatch, {"الحصة": "fail"})
    _stub_gloss(monkeypatch, {})  # empty gloss: the model call failed

    out = snap_prematch.prematch_page(page.id)

    assert out["status"] == "ready"
    assert out["failed_checks"] == 1
    assert out["words"] == {}


def test_endpoint_starts_a_pass_and_reports_current_states(client, db_session, monkeypatch):
    from starlette.background import BackgroundTasks

    lem = _lemma(db_session, "وَقْت", "وقت", "time", "noun")
    page = _page(db_session, "الوقت")
    _stub_checks(monkeypatch, {})
    _stub_gloss(monkeypatch, {})
    scheduled = []
    monkeypatch.setattr(BackgroundTasks, "add_task",
                        lambda self, func, *args, **kw: scheduled.append((func, args)))

    assert client.get(f"/api/discover/snaps/{page.id}/prematch").json() == {"status": "pending"}
    assert scheduled == [(snap_prematch.run_prematch, (page.id,))]
    snap_prematch.run_prematch(page.id)
    db_session.add(UserLemmaKnowledge(lemma_id=lem.lemma_id, knowledge_state="acquiring"))
    db_session.commit()
    db_session.expire_all()  # the test client shares this session; requests normally don't

    body = client.get(f"/api/discover/snaps/{page.id}/prematch").json()
    assert body["status"] == "ready"
    assert body["words"]["الوقت"]["sense"] == {"verdict": "fits"}
    assert body["words"]["الوقت"]["word"]["knowledge_state"] == "acquiring"
    assert len(scheduled) == 1  # a finished pass is not started again
    assert client.get("/api/discover/snaps/999999/prematch").status_code == 404


def test_failed_pass_is_retried_after_a_while():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    failed = snap_prematch._failed()
    assert snap_prematch.needs_pass(None)
    assert snap_prematch.needs_pass({"status": "ready", "version": 0})
    assert not snap_prematch.needs_pass({"status": "ready", "version": snap_prematch.PREMATCH_VERSION})
    assert not snap_prematch.needs_pass(failed, now)
    assert snap_prematch.needs_pass(failed, now + timedelta(minutes=11))


def test_snap_schedules_the_pass(client, db_session, monkeypatch):
    from starlette.background import BackgroundTasks

    monkeypatch.setattr(discover, "extract_text_and_translation", lambda b: {
        "arabic_text": "الوقت", "translation_en": "Time."})
    monkeypatch.setattr(discover, "discover_words_in_text", lambda *a, **k: [])
    scheduled = []
    monkeypatch.setattr(BackgroundTasks, "add_task",
                        lambda self, func, *args, **kw: scheduled.append((func, args)))

    snap_id = client.post(
        "/api/discover/snap", files={"file": ("p.jpg", b"img", "image/jpeg")}
    ).json()["snap_id"]

    assert [f for f, _ in scheduled] == [discover._finish_snap]
    assert scheduled[0][1][0] == snap_id


def test_finish_snap_runs_prematch_then_picks_chips_from_it(monkeypatch):
    calls = []
    monkeypatch.setattr(snap_prematch, "run_prematch", lambda sid: calls.append(("prematch", sid)))
    monkeypatch.setattr(discover, "_store_chips_from_prematch",
                        lambda *a: calls.append(("chips", a)))
    discover._finish_snap(7, 5, "common_first", True)
    assert calls == [("prematch", 7), ("chips", (7, 5, "common_first", True))]


def test_chips_come_from_the_prematch_answers():
    words = {
        # Studied word: no chip.
        "الوقت": {"word": {"lemma_ar": "وَقْت", "lemma_ar_bare": "وقت", "lemma_id": 1,
                           "gloss_en": "time", "knowledge_state": "known"}, "sense": {"verdict": "fits"}},
        # A rerouted word: the chip is the word used, with the wrong match noted.
        "أمضت": {"word": {"lemma_ar": "مَاضٍ", "lemma_ar_bare": "ماض", "lemma_id": 2, "gloss_en": "past"},
                 "sense": {"verdict": "different", "word": {"lemma_ar": "أَمْضَى", "lemma_ar_bare": "امضى",
                                                            "lemma_id": None, "gloss_en": "to spend (time)"}}},
        # An unwritten widening is shown as the meaning here.
        "الحصة": {"word": {"lemma_ar": "حِصَّة", "lemma_ar_bare": "حصة", "lemma_id": 3, "gloss_en": "class period"},
                  "sense": {"verdict": "broader", "gloss_en": "share; class period", "written": False}},
        "دستور": {"word": {"lemma_ar": "دُسْتُور", "lemma_ar_bare": "دستور", "lemma_id": None,
                           "gloss_en": "constitution"}},
        "باريس": {"word": {"lemma_ar": "بَارِيس", "lemma_ar_bare": "باريس", "is_proper_noun": True,
                           "gloss_en": "Paris"}},
    }
    chips = discover.chips_from_prematch("الوقت أمضت الحصة دستور دستور باريس", words, 10, "common_first")
    by = {c["lemma_ar_bare"]: c for c in chips}
    assert set(by) == {"امضى", "حصة", "دستور"}
    assert by["امضى"]["sense_from"]["lemma_ar"] == "مَاضٍ"
    assert by["حصة"]["gloss_en"] == "class period" and by["حصة"]["gloss_here"] == "share; class period"
    assert by["دستور"]["count_in_text"] == 2


def test_regloss_writes_only_gate_approved_widenings(db_session):
    from app.services.preread_service import regloss

    a = _lemma(db_session, "قَرْن", "قرن", "horn", "noun")
    b = _lemma(db_session, "حِصَّة", "حصة", "class period", "noun")
    a.memory_hooks_json = {"mnemonic": "a rhino horn"}
    db_session.commit()
    ok = {"lemma_id": a.lemma_id, "regloss": {"from": "horn", "to": "century; horn", "approved": True}}
    refused = {"lemma_id": b.lemma_id, "regloss": {"from": "class period", "to": "share; class period"}}
    regloss(db_session, [ok, refused], source="test")
    db_session.commit()
    assert ok.get("reglossed") and refused.get("regloss_refused")
    db_session.refresh(a)
    db_session.refresh(b)
    assert a.gloss_en == "century; horn" and a.memory_hooks_json == {"mnemonic": "a rhino horn"}
    assert b.gloss_en == "class period"


def test_page_pass_writes_approved_widenings_only(db_session, monkeypatch):
    qarn = _lemma(db_session, "قَرْن", "قرن", "horn", "noun", "known")
    hissa = _lemma(db_session, "حِصَّة", "حصة", "class period", "noun", "known")
    page = _page(db_session, "القرن الحصة")
    _stub_checks(monkeypatch, {
        "القرن": {"index": 0, "verdict": "broader", "gloss_en": "century; horn", "approved": True},
        "الحصة": {"index": 1, "verdict": "broader", "gloss_en": "share; class period", "approved": False},
    })
    _stub_gloss(monkeypatch, {})

    out = snap_prematch.prematch_page(page.id)

    assert out["words"]["القرن"]["sense"]["written"] is True
    assert out["words"]["الحصة"]["sense"]["written"] is False
    db_session.expire_all()
    assert db_session.get(Lemma, qarn.lemma_id).gloss_en == "century; horn"
    assert db_session.get(Lemma, hissa.lemma_id).gloss_en == "class period"
