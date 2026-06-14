"""Tests for the Dragoman vocabulary-discovery router (app/routers/discover.py)."""
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


def test_words_excludes_known_via_hardened_lookup(client, seeded, monkeypatch):
    """A word already in the vocabulary (even clitic-attached) is not suggested."""
    _no_llm_gloss(monkeypatch, {"دستور": ("constitution", "noun", False)})
    # المكتبة = ال + مكتبة (known); وبالدستور = و+ب+ال+دستور (new) → only دستور suggested.
    r = client.post("/api/discover/words", json={"text": "زرت المكتبة وبالدستور", "count": 8})
    assert r.status_code == 200
    bares = {w["lemma_ar_bare"] for w in r.json()["words"]}
    assert "مكتبة" not in bares
    assert "دستور" in bares


def test_words_drops_proper_nouns(client, db_session, monkeypatch):
    _no_llm_gloss(monkeypatch, {"دستور": ("constitution", "noun", True)})
    r = client.post("/api/discover/words", json={"text": "الدستور مهم", "count": 8})
    assert r.status_code == 200
    assert all(not w.get("is_proper_noun") for w in r.json()["words"])
    assert "دستور" not in {w["lemma_ar_bare"] for w in r.json()["words"]}


def test_add_creates_introduces_and_bypasses_cap(client, db_session):
    r = client.post("/api/discover/add", json={
        "lemma_ar_bare": "دستور", "lemma_ar": "دُسْتُور",
        "gloss_en": "constitution", "pos": "noun", "transliteration": "dustūr",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["state"] == "acquiring"  # introduced immediately, not cap-deferred
    lem = db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "دستور").first()
    assert lem is not None and lem.source == "dragoman" and lem.gloss_en == "constitution"
    ulk = db_session.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == lem.lemma_id
    ).first()
    assert ulk is not None and ulk.knowledge_state == "acquiring"


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


def test_add_batch_dedupes_repeated_word(client, db_session):
    r = client.post("/api/discover/add-batch", json={"words": [
        {"lemma_ar_bare": "دستور", "gloss_en": "constitution", "pos": "noun"},
        {"lemma_ar_bare": "دستور", "gloss_en": "constitution", "pos": "noun"},
    ]})
    assert r.status_code == 200
    rows = db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "دستور").all()
    assert len(rows) == 1  # second add resolved to the first via in-batch lookup
