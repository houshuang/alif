"""Tests for the Dragoman vocabulary-discovery router (app/routers/discover.py)."""
from datetime import datetime, timezone

import pytest

from app.models import Lemma, UserLemmaKnowledge
from app.routers import discover


@pytest.fixture
def seeded(db_session):
    """A known word (مكتبة, acquiring) so we can prove discovery excludes it."""
    lem = Lemma(
        lemma_ar="مَكْتَبَة", lemma_ar_bare="مكتبة", gloss_en="library",
        pos="noun", source="frequency_core",
    )
    db_session.add(lem)
    db_session.flush()
    db_session.add(UserLemmaKnowledge(lemma_id=lem.lemma_id, knowledge_state="acquiring"))
    db_session.commit()
    return db_session


def _no_llm_gloss(monkeypatch, mapping):
    """Stub the LLM gloss step: mapping is {bare: (gloss, pos, is_proper)}."""
    def fake_gloss(items):
        out = {}
        for it in items:
            g, pos, proper = mapping.get(it["bare"], ("x", "noun", False))
            out[it["index"]] = {
                "gloss_en": g, "pos": pos, "transliteration": "t", "is_proper_noun": proper,
            }
        return out
    monkeypatch.setattr(discover, "_gloss", fake_gloss)


def _rich_gloss(monkeypatch, mapping):
    """Stub gloss with full fields. mapping: {bare: {gloss_en,pos,is_proper_noun,
    register,dialect,lemma_ar}} (any subset; sensible defaults fill the rest)."""
    def fake_gloss(items):
        out = {}
        for it in items:
            m = mapping.get(it["bare"], {})
            g = {
                "gloss_en": m.get("gloss_en", "x"),
                "pos": m.get("pos", "noun"),
                "transliteration": m.get("transliteration", "t"),
                "is_proper_noun": m.get("is_proper_noun", False),
                "register": m.get("register"),
                "dialect": m.get("dialect"),
            }
            if "lemma_ar" in m:
                g["lemma_ar"] = m["lemma_ar"]
            out[it["index"]] = g
        return out
    monkeypatch.setattr(discover, "_gloss", fake_gloss)


def _snap(client, db_session, monkeypatch):
    """POST /snap, then run its background step (suggested words) as the server
    would after the response, and return (response body, reopened page)."""
    from starlette.background import BackgroundTasks

    tasks = []
    monkeypatch.setattr(BackgroundTasks, "add_task",
                        lambda self, func, *args, **kw: tasks.append((func, args)))
    r = client.post("/api/discover/snap", files={"file": ("page.jpg", b"\xff\xd8img", "image/jpeg")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["words"] == [] and body["words_pending"] is True
    assert [f for f, _ in tasks] == [discover._finish_snap]
    discover._store_snap_chips(*tasks[0][1])
    db_session.expire_all()
    page = client.get(f"/api/discover/snaps/{body['snap_id']}").json()
    assert page["words_pending"] is False
    return body, page


def _seed_faqad_collision(db_session):
    now = datetime.now(timezone.utc)
    qad = Lemma(
        lemma_ar="قَدْ",
        lemma_ar_bare="قد",
        gloss_en="already",
        pos="particle",
        gates_completed_at=now,
    )
    loss = Lemma(
        lemma_ar="فَقْد",
        lemma_ar_bare="فقد",
        gloss_en="loss",
        pos="noun",
        gates_completed_at=now,
    )
    db_session.add_all([qad, loss])
    db_session.commit()
    return qad, loss


def test_oov_dropped_by_default(client, db_session, monkeypatch):
    """Without include_oov, an out-of-CAMeL-vocab word is NOT surfaced (Dragoman default)."""
    _rich_gloss(monkeypatch, {})
    r = client.post("/api/discover/words", json={"text": "رأيت كسها على السرير", "count": 10})
    assert r.status_code == 200
    bares = {w["lemma_ar_bare"] for w in r.json()["words"]}
    assert "كس" not in bares


def test_oov_surface_fallback_aggregates(client, db_session, monkeypatch):
    """include_oov: cliticized OOV forms aggregate to one fallback lemma with all surfaces."""
    _rich_gloss(monkeypatch, {"كس": {"gloss_en": "vulva", "register": "vulgar", "dialect": "gulf"}})
    r = client.post("/api/discover/words", json={
        "text": "كسها وكسها والكس", "count": 10, "include_oov": True})
    assert r.status_code == 200
    ks = [w for w in r.json()["words"] if w["lemma_ar_bare"] == "كس"]
    assert len(ks) == 1
    w = ks[0]
    assert w["count_in_text"] == 3
    assert set(w["surface_forms"]) == {"كسها", "وكسها", "والكس"}
    assert w["lemma_source"] == "surface_fallback"
    assert w["register"] == "vulgar" and w["dialect"] == "gulf"
    assert w["root"] is None  # no CAMeL analysis for OOV


def test_additive_fields_for_camel_word(client, db_session, monkeypatch):
    """A genuine MSA OOV-but-analyzable word carries root + example_ar + surface_forms."""
    _rich_gloss(monkeypatch, {"اعلن": {"gloss_en": "announce", "pos": "verb"}})
    r = client.post("/api/discover/words", json={
        "text": "أعلنت الوزارة ذلك", "count": 10})
    assert r.status_code == 200
    w = next(w for w in r.json()["words"] if w["lemma_ar_bare"] == "اعلن")
    assert w["lemma_source"] == "camel"
    assert w["root"] and "ع" in w["root"]          # CAMeL root ع ل ن
    assert "أعلنت" in w["surface_forms"]
    assert w["example_ar"] and "أعلنت" in w["example_ar"]


def test_gloss_can_correct_lemma(client, db_session, monkeypatch):
    """When the gloss step returns a corrected lemma_ar, the output adopts it."""
    _rich_gloss(monkeypatch, {"خطا": {"gloss_en": "step", "lemma_ar": "خَطْوَة"}})
    r = client.post("/api/discover/words", json={
        "text": "خطا كبيرة جدا", "count": 10, "include_oov": True})
    assert r.status_code == 200
    w = next(w for w in r.json()["words"] if w["lemma_ar"] == "خَطْوَة")
    assert w["lemma_ar_bare"] == "خطوة"  # recomputed from the correction


def test_gloss_correction_to_known_word_is_filtered(client, db_session, monkeypatch):
    """If the gloss step corrects an OOV form to a word already in the vocabulary, it
    must NOT be offered (else /add would report it 'already known')."""
    lem = Lemma(lemma_ar="خَطْوَة", lemma_ar_bare="خطوة", gloss_en="step",
                pos="noun", source="frequency_core")
    db_session.add(lem)
    db_session.flush()
    db_session.add(UserLemmaKnowledge(lemma_id=lem.lemma_id, knowledge_state="known"))
    db_session.commit()
    # "خطا" is OOV; the gloss step "corrects" it to the known خَطْوَة.
    _rich_gloss(monkeypatch, {"خطا": {"gloss_en": "step", "lemma_ar": "خَطْوَة"}})
    r = client.post("/api/discover/words", json={
        "text": "خطا كبيرة جدا", "count": 10, "include_oov": True})
    assert r.status_code == 200
    assert all(w["lemma_ar_bare"] != "خطوة" for w in r.json()["words"])


def test_words_preserves_exact_faqad_surface_identity(db_session):
    qad, loss = _seed_faqad_collision(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=qad.lemma_id,
        knowledge_state="known",
    ))
    db_session.commit()

    words = discover.discover_words_in_text(db_session, "فَقَدْ", count=8)

    assert words == []
    assert db_session.query(UserLemmaKnowledge).filter_by(
        lemma_id=loss.lemma_id
    ).count() == 0


def test_words_unresolved_exact_alias_does_not_fall_through(
    db_session,
    monkeypatch,
):
    db_session.add(Lemma(
        lemma_ar="فَقْد",
        lemma_ar_bare="فقد",
        gloss_en="loss",
        pos="noun",
        gates_completed_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    monkeypatch.setattr(
        discover,
        "_classify",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("exact alias must not reach CAMeL/OOV classification")
        ),
    )

    assert discover.discover_words_in_text(
        db_session,
        "فَقَدْ",
        count=8,
        include_oov=True,
    ) == []


def test_proper_noun_dropped_in_learn_mode(client, db_session, monkeypatch):
    """Default (learn-next) mode drops names — they aren't vocabulary to schedule."""
    _rich_gloss(monkeypatch, {"لندن": {"gloss_en": "London", "is_proper_noun": True}})
    r = client.post("/api/discover/words", json={"text": "لندن مدينة جميلة", "count": 10})
    assert r.status_code == 200
    assert all(w["lemma_ar_bare"] != "لندن" for w in r.json()["words"])


def test_proper_noun_kept_but_flagged_in_glossary_mode(client, db_session, monkeypatch):
    """Glossary mode (include_oov) keeps names so the model's over-tagging of vulgar
    OOV nouns as 'proper' can't silently drop content; the consumer filters on the flag."""
    _rich_gloss(monkeypatch, {"لندن": {"gloss_en": "London", "is_proper_noun": True}})
    r = client.post("/api/discover/words", json={
        "text": "لندن مدينة جميلة", "count": 10, "include_oov": True})
    assert r.status_code == 200
    london = next(w for w in r.json()["words"] if w["lemma_ar_bare"] == "لندن")
    assert london["is_proper_noun"] is True


def test_add_persists_register_dialect(client, db_session):
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "كس", "lemma_ar": "كُسّ", "gloss_en": "vulva",
        "pos": "noun", "register": "vulgar", "dialect": "gulf"})
    assert r.status_code == 200
    lem = db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "كس").first()
    assert lem is not None
    assert lem.register == "vulgar" and lem.dialect == "gulf"


def test_add_exact_vocalized_alias_reuses_destination(client, db_session):
    qad, loss = _seed_faqad_collision(db_session)

    response = client.post("/api/discover/add", json={
        "lemma_ar_bare": "فقد",
        "lemma_ar": "فَقَدْ",
        "gloss_en": "already",
        "pos": "particle",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is False
    assert body["lemma_id"] == qad.lemma_id
    assert db_session.query(Lemma).count() == 2
    assert db_session.query(UserLemmaKnowledge).filter_by(
        lemma_id=loss.lemma_id
    ).count() == 0


def test_add_unresolved_exact_alias_refuses_creation(client, db_session):
    loss = Lemma(
        lemma_ar="فَقْد",
        lemma_ar_bare="فقد",
        gloss_en="loss",
        pos="noun",
        gates_completed_at=datetime.now(timezone.utc),
    )
    db_session.add(loss)
    db_session.commit()

    response = client.post("/api/discover/add", json={
        "lemma_ar_bare": "فقد",
        "lemma_ar": "فَقَدْ",
        "gloss_en": "already",
        "pos": "particle",
    })

    assert response.status_code == 400
    assert "exact running-text alias destination is unavailable" in (
        response.json()["detail"]
    )
    assert db_session.query(Lemma).count() == 1
    assert db_session.query(UserLemmaKnowledge).count() == 0


@pytest.mark.parametrize("include_collision", [False, True])
def test_add_exact_alias_conflicting_sense_cannot_reroute_or_create(
    client,
    db_session,
    include_collision,
):
    qad = Lemma(
        lemma_ar="قَدْ",
        lemma_ar_bare="قد",
        gloss_en="already",
        pos="particle",
        gates_completed_at=datetime.now(timezone.utc),
    )
    db_session.add(qad)
    if include_collision:
        db_session.add(Lemma(
            lemma_ar="فَقْد",
            lemma_ar_bare="فقد",
            gloss_en="loss",
            pos="noun",
            gates_completed_at=datetime.now(timezone.utc),
        ))
    db_session.commit()
    before_ids = {
        lemma.lemma_id for lemma in db_session.query(Lemma).all()
    }

    response = client.post("/api/discover/add", json={
        "lemma_ar_bare": "فقد",
        "lemma_ar": "فَقَدْ",
        "gloss_en": "loss",
        "pos": "noun",
    })

    assert response.status_code == 400
    assert "supplied sense conflicts with exact running-text alias" in (
        response.json()["detail"]
    )
    assert {
        lemma.lemma_id for lemma in db_session.query(Lemma).all()
    } == before_ids
    assert db_session.query(UserLemmaKnowledge).count() == 0


def test_words_excludes_known_via_hardened_lookup(client, seeded, monkeypatch):
    """A word already in the vocabulary (even clitic-attached) is not suggested."""
    _no_llm_gloss(monkeypatch, {"دستور": ("constitution", "noun", False)})
    # المكتبة = ال + مكتبة (known); وبالدستور = و+ب+ال+دستور (new) → only دستور suggested.
    r = client.post("/api/discover/words", json={"text": "زرت المكتبة وبالدستور", "count": 8})
    assert r.status_code == 200
    bares = {w["lemma_ar_bare"] for w in r.json()["words"]}
    assert "مكتبة" not in bares
    assert "دستور" in bares


def test_words_includes_corpus_lemma_not_yet_in_learning(client, db_session, monkeypatch):
    """A corpus row is lexical metadata, not proof that the learner knows the word.

    This is the photographed-book regression: the database contains most ordinary
    page vocabulary, while only UserLemmaKnowledge says what is already learning.
    """
    lem = Lemma(
        lemma_ar="دُسْتُور", lemma_ar_bare="دستور", gloss_en="constitution",
        pos="noun", source="corpus",
    )
    db_session.add(lem)
    db_session.commit()

    def fail_if_called(_items):
        raise AssertionError("a corpus lemma with a gloss should not require the gloss LLM")

    monkeypatch.setattr(discover, "_gloss", fail_if_called)
    r = client.post("/api/discover/words", json={"text": "الدستور", "count": 8})

    assert r.status_code == 200
    word = next(w for w in r.json()["words"] if w["lemma_ar_bare"] == "دستور")
    assert word["gloss_en"] == "constitution"
    assert word["lemma_source"] == "database"


def test_words_drops_proper_nouns(client, db_session, monkeypatch):
    _no_llm_gloss(monkeypatch, {"دستور": ("constitution", "noun", True)})
    r = client.post("/api/discover/words", json={"text": "الدستور مهم", "count": 8})
    assert r.status_code == 200
    assert all(not w.get("is_proper_noun") for w in r.json()["words"])
    assert "دستور" not in {w["lemma_ar_bare"] for w in r.json()["words"]}


def test_add_creates_reading_support_without_bypassing_cap(client, db_session):
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "دستور", "lemma_ar": "دُسْتُور",
        "gloss_en": "constitution", "pos": "noun", "transliteration": "dustūr",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["state"] == "encountered"  # staged, no automatic obligation
    lem = db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "دستور").first()
    assert lem is not None and lem.source == "dragoman" and lem.gloss_en == "constitution"
    ulk = db_session.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == lem.lemma_id
    ).first()
    assert ulk is not None and ulk.knowledge_state == "encountered"
    assert ulk.attention_disposition == "reading_support"


def test_add_records_custom_source(client, db_session):
    """A consumer (e.g. Bookifier) can self-identify via `source`; it lands on
    both the Lemma row and the UserLemmaKnowledge row instead of "dragoman"."""
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "دستور", "lemma_ar": "دُسْتُور",
        "gloss_en": "constitution", "pos": "noun", "source": "bookifier",
    })
    assert r.status_code == 200
    assert r.json()["source"] == "bookifier"
    lem = db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "دستور").first()
    assert lem is not None and lem.source == "bookifier"
    ulk = db_session.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == lem.lemma_id
    ).first()
    assert ulk is not None and ulk.source == "bookifier"


def test_add_rejects_glossless_word(client, db_session):
    r = client.post("/api/discover/add", json={"lemma_ar_bare": "دستور", "gloss_en": ""})
    # The endpoint refuses with a clean 400 rather than creating a gloss-less lemma.
    assert r.status_code == 400
    assert db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "دستور").first() is None


def test_add_rejects_proper_noun(client, db_session):
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "مصر", "gloss_en": "Egypt", "pos": "proper_noun",
    })
    assert r.status_code == 400
    assert db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "مصر").first() is None


def test_add_batch_isolates_failures(client, db_session):
    r = client.post("/api/discover/add-batch", json={"words": [
        {"lemma_ar_bare": "دستور", "gloss_en": "constitution", "pos": "noun"},
        {"lemma_ar_bare": "مصر", "gloss_en": "Egypt", "pos": "proper_noun"},  # rejected
        {"lemma_ar_bare": "اقتصاد", "gloss_en": "economy", "pos": "noun"},
    ]})
    assert r.status_code == 200
    added = r.json()["added"]
    assert added[0]["created"] is True
    assert "error" in added[1]
    assert added[2]["created"] is True
    # The good words persisted despite the failure between them.
    bares = {l.lemma_ar_bare for l in db_session.query(Lemma).all()}
    assert {"دستور", "اقتصاد"} <= bares
    assert "مصر" not in bares


def test_snap_returns_translation_and_words(client, seeded, monkeypatch):
    """The photo /snap endpoint stitches OCR+translation (Gemini, mocked) to the
    shared word-discovery path: it returns the page translation and the top unknown
    words, excluding ones already in the vocabulary (مكتبة from `seeded`)."""
    monkeypatch.setattr(discover, "extract_text_and_translation", lambda b: {
        "arabic_text": "زرت المكتبة وقرأت الدستور",
        "translation_en": "I visited the library and read the constitution.",
    })
    _no_llm_gloss(monkeypatch, {"دستور": ("constitution", "noun", False)})
    body, page = _snap(client, seeded, monkeypatch)
    assert body["translation_en"].startswith("I visited the library")
    assert body["arabic_text"]
    bares = {w["lemma_ar_bare"] for w in page["words"]}
    assert "دستور" in bares       # new word surfaced
    assert "مكتبة" not in bares   # already known → excluded


def test_snap_rejects_image_with_no_arabic(client, db_session, monkeypatch):
    monkeypatch.setattr(discover, "extract_text_and_translation", lambda b: {
        "arabic_text": "", "translation_en": "",
    })
    r = client.post(
        "/api/discover/snap",
        files={"file": ("blank.jpg", b"\xff\xd8fakejpeg", "image/jpeg")},
    )
    assert r.status_code == 422


def test_add_batch_dedupes_repeated_word(client, db_session):
    r = client.post("/api/discover/add-batch", json={"words": [
        {"lemma_ar_bare": "دستور", "gloss_en": "constitution", "pos": "noun"},
        {"lemma_ar_bare": "دستور", "gloss_en": "constitution", "pos": "noun"},
    ]})
    assert r.status_code == 200
    rows = db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "دستور").all()
    assert len(rows) == 1  # second add resolved to the first via in-batch lookup


def test_add_batch_rebuilds_exact_alias_metadata_after_each_commit(
    client,
    db_session,
):
    people = Lemma(
        lemma_ar="نَاسٌ",
        lemma_ar_bare="ناس",
        gloss_en="people",
        pos="noun",
        gates_completed_at=datetime.now(timezone.utc),
    )
    db_session.add(people)
    db_session.commit()

    response = client.post("/api/discover/add-batch", json={"words": [
        {
            # A deliberately conflicting sense creates a second exact
            # destination identity in the first independently committed item.
            "lemma_ar_bare": "ناس",
            "lemma_ar": "نَاسٌ",
            "gloss_en": "copper",
            "pos": "noun",
        },
        {
            "lemma_ar_bare": "أناس",
            "lemma_ar": "أُنَاسٌ",
            "gloss_en": "people",
            "pos": "noun",
        },
    ]})

    assert response.status_code == 200
    added = response.json()["added"]
    assert added[0]["created"] is True
    assert added[0]["lemma_id"] != people.lemma_id
    assert "exact running-text alias destination is unavailable" in (
        added[1]["error"]
    )
    assert db_session.query(Lemma).filter(
        Lemma.lemma_ar == "أُنَاسٌ"
    ).count() == 0
    assert db_session.query(UserLemmaKnowledge).filter_by(
        lemma_id=people.lemma_id
    ).count() == 0


def test_add_new_word_shaped_like_clitic_plus_known(client, db_session):
    """Regression for the 2026-07-15 collision bug: كناس ("street sweeper")
    must be created as its own lemma, not silently no-op onto ناس ("people")
    via a ك-preposition parse. spec-2026-07-15-lookup-clitic-collision.md §7."""
    nas = Lemma(lemma_ar="نَاس", lemma_ar_bare="ناس", gloss_en="people",
                pos="noun", source="frequency_core")
    db_session.add(nas)
    db_session.flush()
    db_session.add(UserLemmaKnowledge(lemma_id=nas.lemma_id, knowledge_state="known"))
    db_session.commit()

    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "كناس", "lemma_ar": "كَنَّاس",
        "gloss_en": "street sweeper", "pos": "noun",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["lemma_id"] != nas.lemma_id
    assert body["state"] == "encountered"
    lem = db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "كناس").first()
    assert lem is not None and lem.gloss_en == "street sweeper"


def test_add_definite_clitic_form_still_resolves(client, db_session, seeded):
    """The good case citation mode must preserve: adding بالمكتبة resolves to
    the existing مكتبة lemma (ال-bearing prefix strip), no duplicate created."""
    maktaba = db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "مكتبة").first()
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "بالمكتبة", "gloss_en": "library",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is False
    assert body["lemma_id"] == maktaba.lemma_id
    assert db_session.query(Lemma).filter(
        Lemma.lemma_ar_bare == "بالمكتبة").first() is None


def test_add_homograph_creates_new_instead_of_wrong_sense(client, db_session):
    """Regression for the 2026-07-21 Kalila import finding: مَلِك "king"
    bare-matches مَلَك "angel" (known), which used to no-op as already_known —
    the word was silently never added. The sense gate must create it."""
    angel = Lemma(lemma_ar="مَلَك", lemma_ar_bare="ملك", gloss_en="angel",
                  pos="noun", source="frequency_core")
    db_session.add(angel)
    db_session.flush()
    db_session.add(UserLemmaKnowledge(lemma_id=angel.lemma_id, knowledge_state="known"))
    db_session.commit()

    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "مَلِك", "lemma_ar": "مَلِك",
        "gloss_en": "king", "pos": "noun",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["lemma_id"] != angel.lemma_id
    assert body["sense_rerouted_from"] == angel.lemma_id
    assert body["already_known"] is False
    assert body["state"] == "encountered"
    # The angel lemma's knowledge state is untouched.
    ulk = db_session.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == angel.lemma_id).first()
    assert ulk.knowledge_state == "known"


def test_add_verb_does_not_attach_to_masdar_noun(client, db_session):
    """زَعَمَ (verb) bare-matches its masdar زَعْم (noun). Verb/noun POS are
    intentionally incompatible in the correction gate — the verb must become
    its own lemma, mirroring the lemma-identity design (verb ≠ masdar)."""
    masdar = Lemma(lemma_ar="زَعْم", lemma_ar_bare="زعم",
                   gloss_en="claim; allegation", pos="noun", source="frequency_core")
    db_session.add(masdar)
    db_session.commit()

    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "زَعَمَ", "lemma_ar": "زَعَمَ",
        "gloss_en": "to claim, assert", "pos": "verb",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["lemma_id"] != masdar.lemma_id
    assert body["sense_rerouted_from"] == masdar.lemma_id


def test_add_compatible_gloss_still_attaches(client, db_session):
    """The gate must not turn synonym-phrased adds into duplicates: overlapping
    gloss tokens + same POS attach to the existing lemma as before."""
    lem = Lemma(lemma_ar="حِيلَة", lemma_ar_bare="حيلة",
                gloss_en="trick/stratagem", pos="noun", source="frequency_core")
    db_session.add(lem)
    db_session.commit()

    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "حِيلَة", "lemma_ar": "حِيلَة",
        "gloss_en": "stratagem, ruse", "pos": "noun",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is False
    assert body["lemma_id"] == lem.lemma_id
    assert body["sense_rerouted_from"] is None
    assert db_session.query(Lemma).filter(
        Lemma.lemma_ar_bare == "حيلة").count() == 1


def test_add_reroutes_to_sense_compatible_sibling(client, db_session):
    """When both homographs exist, an add whose sense conflicts with the
    bare-lookup winner lands on the compatible sibling — never a duplicate,
    never the wrong sense."""
    angel = Lemma(lemma_ar="مَلَك", lemma_ar_bare="ملك", gloss_en="angel",
                  pos="noun", source="frequency_core")
    king = Lemma(lemma_ar="مَلِك", lemma_ar_bare="ملك", gloss_en="king",
                 pos="noun", source="frequency_core")
    db_session.add_all([angel, king])
    db_session.commit()

    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "مَلِك", "lemma_ar": "مَلِك",
        "gloss_en": "king, monarch", "pos": "noun",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is False
    assert body["lemma_id"] == king.lemma_id
    assert db_session.query(Lemma).filter(
        Lemma.lemma_ar_bare == "ملك").count() == 2


def test_lookup_returns_learning_word_with_state(client, seeded, monkeypatch):
    _no_llm_gloss(monkeypatch, {})
    r = client.post("/api/discover/lookup", json={"word": "المكتبة", "context": "ذهبت إلى المكتبة"})
    assert r.status_code == 200
    w = r.json()["word"]
    assert w["lemma_ar_bare"] == "مكتبة"
    assert w["knowledge_state"] == "acquiring"
    assert w["gloss_en"] == "library"


def test_lookup_function_word_is_null(client, db_session, monkeypatch):
    _no_llm_gloss(monkeypatch, {})
    r = client.post("/api/discover/lookup", json={"word": "في"})
    assert r.status_code == 200
    assert r.json()["word"] is None


def test_snap_add_bypasses_recovery_budget(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.acquisition_service._recovery_mode_intro_budget", lambda *a: 0
    )
    monkeypatch.setattr(discover, "_gate_and_generate", lambda ids: None)
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "منفى", "lemma_ar": "مَنْفَى", "gloss_en": "exile",
        "pos": "noun", "source": "snap"})
    assert r.status_code == 200
    assert r.json()["state"] == "acquiring"
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "رصيف", "lemma_ar": "رَصِيف", "gloss_en": "pavement",
        "pos": "noun"})
    assert r.json()["state"] == "encountered"


def test_every_add_is_logged_with_its_source(client, seeded, monkeypatch):
    events = []
    monkeypatch.setattr(discover, "log_interaction", lambda **kw: events.append(kw))
    monkeypatch.setattr(discover, "_gate_and_generate", lambda ids: None)
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "مكتبة", "lemma_ar": "مَكْتَبَة", "gloss_en": "library",
        "pos": "noun", "source": "snap"})
    assert r.status_code == 200
    assert events and events[-1]["event"] == "discover_word_added"
    assert events[-1]["source"] == "snap"
    assert events[-1]["created"] is False


def test_gloss_accepts_bare_list_from_cli(monkeypatch):
    monkeypatch.setattr(discover, "generate_completion", lambda **kw: [
        {"index": 0, "gloss_en": "exile", "pos": "noun"}, "junk",
    ])
    out = discover._gloss([{"index": 0, "lemma_ar": "مَنْفَى", "bare": "منفى"}])
    assert out == {0: {"index": 0, "gloss_en": "exile", "pos": "noun"}}


def test_snap_returns_page_when_word_discovery_fails(client, db_session, monkeypatch):
    monkeypatch.setattr(discover, "extract_text_and_translation", lambda b: {
        "arabic_text": "هذا كتاب", "translation_en": "This is a book.", "sentences": []})

    def boom(*a, **kw):
        raise AttributeError("'list' object has no attribute 'get'")

    monkeypatch.setattr(discover, "discover_words_in_text", boom)
    body, page = _snap(client, db_session, monkeypatch)
    assert body["translation_en"] == "This is a book."
    assert page["words"] == []


def test_adding_existing_word_schedules_material(client, db_session, monkeypatch):
    lem = Lemma(lemma_ar="مُرَشَّح", lemma_ar_bare="مرشح", gloss_en="candidate", pos="noun",
                source="frequency_core", gates_completed_at=datetime.now(timezone.utc))
    db_session.add(lem)
    db_session.commit()
    scheduled = []
    from starlette.background import BackgroundTasks

    def record(self, func, *args, **kwargs):
        if func is discover._ensure_material:
            scheduled.extend(args[0])

    monkeypatch.setattr(BackgroundTasks, "add_task", record)
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "مرشح", "lemma_ar": "مُرَشَّح", "gloss_en": "candidate",
        "pos": "noun", "source": "snap"})
    assert r.json()["state"] == "acquiring"
    assert r.json()["created"] is False
    assert scheduled == [lem.lemma_id]


def test_ensure_material_only_tops_up_missing(db_session, monkeypatch):
    from app.services import material_generator as mg

    lem = Lemma(lemma_ar="وَفِيّ", lemma_ar_bare="وفي", gloss_en="loyal", pos="adj")
    db_session.add(lem)
    db_session.commit()
    calls = []
    monkeypatch.setattr(mg, "generate_material_for_word",
                        lambda lid, needed: calls.append((lid, needed)) or 0)
    discover._ensure_material([lem.lemma_id])
    assert calls == [(lem.lemma_id, mg.MIN_SENTENCES_PER_WORD)]


def test_snap_does_not_offer_proper_names(client, db_session, monkeypatch):
    monkeypatch.setattr(discover, "extract_text_and_translation", lambda b: {
        "arabic_text": "زار ماكرون باريس", "translation_en": "Macron visited Paris.", "sentences": []})
    monkeypatch.setattr(discover, "discover_words_in_text", lambda *a, **k: [
        {"lemma_ar_bare": "ماكرون", "is_proper_noun": True},
        {"lemma_ar_bare": "زار", "is_proper_noun": False},
    ])
    _, page = _snap(client, db_session, monkeypatch)
    assert [w["lemma_ar_bare"] for w in page["words"]] == ["زار"]


def test_snap_pages_are_saved_listed_and_reopened(client, db_session, monkeypatch):
    lem = Lemma(lemma_ar="مُرَشَّح", lemma_ar_bare="مرشح", gloss_en="candidate", pos="noun")
    db_session.add(lem)
    db_session.commit()
    monkeypatch.setattr(discover, "extract_text_and_translation", lambda b: {
        "arabic_text": "فاز المرشح", "translation_en": "The candidate won.",
        "sentences": [{"p": 0, "ar": "فاز المرشح", "en": "The candidate won.", "links": []}]})
    monkeypatch.setattr(discover, "discover_words_in_text", lambda *a, **k: [
        {"lemma_ar_bare": "مرشح", "lemma_id": lem.lemma_id, "knowledge_state": None,
         "is_proper_noun": False}])
    body, _ = _snap(client, db_session, monkeypatch)
    snap_id = body["snap_id"]
    assert snap_id

    listed = client.get("/api/discover/snaps").json()["snaps"]
    assert listed[0]["snap_id"] == snap_id
    assert listed[0]["preview_en"] == "The candidate won."

    # The word was added after the snap: reopening shows its current state.
    db_session.add(UserLemmaKnowledge(lemma_id=lem.lemma_id, knowledge_state="acquiring"))
    db_session.commit()
    db_session.expire_all()
    page = client.get(f"/api/discover/snaps/{snap_id}").json()
    assert page["sentences"][0]["en"] == "The candidate won."
    assert page["words"][0]["knowledge_state"] == "acquiring"
    assert client.get("/api/discover/snaps/999999").status_code == 404


def _gated(v, res):
    """What check_lookup_sense returns after the gloss gate: a widening is
    approved unless the test says otherwise."""
    if v and v["verdict"] == "broader":
        v["approved"] = res.get("approved", True)
    return v


def _stub_sense(monkeypatch, res):
    from app.services import lookup_sense
    from app.services.lookup_sense import validate_verdict

    monkeypatch.setattr(
        lookup_sense, "check_lookup_sense",
        lambda sentence, surface, entry: _gated(validate_verdict(res, entry), res),
    )


def _lemma(db, ar, bare, gloss, pos, state=None):
    lem = Lemma(lemma_ar=ar, lemma_ar_bare=bare, gloss_en=gloss, pos=pos, source="frequency_core")
    db.add(lem)
    db.flush()
    if state:
        db.add(UserLemmaKnowledge(lemma_id=lem.lemma_id, knowledge_state=state))
    db.commit()
    return lem


def test_lookup_sense_broader_widens_gloss(client, db_session, monkeypatch):
    lem = _lemma(db_session, "حِصَّة", "حصة", "class period", "noun", "known")
    _stub_sense(monkeypatch, {"verdict": "broader", "gloss_en": "share, portion; class period"})
    r = client.post("/api/discover/lookup/sense", json={
        "lemma_id": lem.lemma_id, "surface": "الحصة", "context": "كانت لها الحصة الأكبر في ابنها"})
    body = r.json()
    assert body["verdict"] == "broader" and body["written"] is True
    db_session.refresh(lem)
    assert lem.gloss_en == "share, portion; class period"


def test_lookup_sense_broader_rejects_unrelated_rewrite(client, db_session, monkeypatch):
    lem = _lemma(db_session, "حِصَّة", "حصة", "class period", "noun")
    _stub_sense(monkeypatch, {"verdict": "broader", "gloss_en": "share"})
    r = client.post("/api/discover/lookup/sense", json={
        "lemma_id": lem.lemma_id, "surface": "الحصة", "context": "x"})
    assert r.json()["verdict"] is None
    db_session.refresh(lem)
    assert lem.gloss_en == "class period"


def test_lookup_sense_different_proposes_new_word(client, db_session, monkeypatch):
    lem = _lemma(db_session, "مَاضٍ", "ماض", "past", "adj", "known")
    _stub_sense(monkeypatch, {"verdict": "different", "lemma_ar": "أَمْضَى",
                              "gloss_en": "to spend (time)", "pos": "verb"})
    r = client.post("/api/discover/lookup/sense", json={
        "lemma_id": lem.lemma_id, "surface": "أمضت", "context": "فقد أمضت ليالي"})
    body = r.json()
    assert body["verdict"] == "different"
    assert body["word"]["lemma_id"] is None
    assert body["word"]["lemma_ar"] == "أَمْضَى"
    assert body["word"]["gloss_en"] == "to spend (time)"
    db_session.refresh(lem)
    assert lem.gloss_en == "past"  # the matched entry is left alone


def test_lookup_sense_different_finds_existing_word(client, db_session, monkeypatch):
    wrong = _lemma(db_session, "أَب", "اب", "father", "noun", "known")
    son = _lemma(db_session, "اِبْن", "ابن", "son", "noun", "known")
    _stub_sense(monkeypatch, {"verdict": "different", "lemma_ar": "اِبْن",
                              "gloss_en": "son", "pos": "noun"})
    r = client.post("/api/discover/lookup/sense", json={
        "lemma_id": wrong.lemma_id, "surface": "ابنها", "context": "في ابنها"})
    word = r.json()["word"]
    assert word["lemma_id"] == son.lemma_id
    assert word["knowledge_state"] == "known"


def test_lookup_sense_fits_and_failure(client, db_session, monkeypatch):
    lem = _lemma(db_session, "وَقْت", "وقت", "time", "noun")
    _stub_sense(monkeypatch, {"verdict": "fits"})
    r = client.post("/api/discover/lookup/sense", json={
        "lemma_id": lem.lemma_id, "surface": "الوقت", "context": "الوقت"})
    assert r.json() == {"verdict": "fits"}
    _stub_sense(monkeypatch, None)
    r = client.post("/api/discover/lookup/sense", json={
        "lemma_id": lem.lemma_id, "surface": "الوقت", "context": "الوقت"})
    assert r.json() == {"verdict": None}


def test_snap_suggestions_stop_pending_when_the_task_never_finished(client, db_session):
    from datetime import timedelta

    from app.models import SnapPage

    fresh = SnapPage(arabic_text="هذا", translation_en="This.", words_json=None)
    stale = SnapPage(arabic_text="هذا", translation_en="This.", words_json=None,
                     created_at=datetime.now(timezone.utc) - timedelta(minutes=10))
    db_session.add_all([fresh, stale])
    db_session.commit()
    assert client.get(f"/api/discover/snaps/{fresh.id}").json()["words_pending"] is True
    assert client.get(f"/api/discover/snaps/{stale.id}").json()["words_pending"] is False
