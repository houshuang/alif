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
