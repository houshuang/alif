"""Tests for suspend/unsuspend, content flags, and suspended word filtering."""

from datetime import datetime, timezone

from app.models import (
    ActivityLog,
    ContentFlag,
    Lemma,
    Root,
    Sentence,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.fsrs_service import create_new_card, reactivate_if_suspended


def _seed_word(db, arabic="كَلْب", bare="كلب", gloss="dog", state="learning"):
    lemma = Lemma(lemma_ar=arabic, lemma_ar_bare=bare, gloss_en=gloss, source="test")
    db.add(lemma)
    db.flush()
    knowledge = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state=state,
        fsrs_card_json=create_new_card(),
        source="test",
        times_seen=1,
        times_correct=0,
    )
    db.add(knowledge)
    db.commit()
    return lemma


def _seed_sentence(db, lemma, text="الكَلْبُ كَبِيرٌ"):
    sentence = Sentence(
        arabic_text=text,
        arabic_diacritized=text,
        english_translation="The dog is big",
        transliteration="al-kalb kabir",
        target_lemma_id=lemma.lemma_id,
    )
    db.add(sentence)
    db.flush()
    sw = SentenceWord(
        sentence_id=sentence.id,
        position=1,
        surface_form="الكَلْبُ",
        lemma_id=lemma.lemma_id,
        is_target_word=True,
    )
    db.add(sw)
    db.commit()
    return sentence


# --- Suspend/Unsuspend Endpoint Tests ---

def test_suspend_word(client, db_session):
    lemma = _seed_word(db_session)
    resp = client.post(f"/api/words/{lemma.lemma_id}/suspend")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "suspended"
    assert data["previous_state"] == "learning"

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "suspended"


def test_suspend_already_suspended(client, db_session):
    lemma = _seed_word(db_session, state="learning")
    client.post(f"/api/words/{lemma.lemma_id}/suspend")
    resp = client.post(f"/api/words/{lemma.lemma_id}/suspend")
    assert resp.status_code == 200
    assert resp.json()["already_suspended"] is True


def test_suspend_no_ulk(client, db_session):
    """Suspend a word that has no ULK record yet."""
    lemma = Lemma(lemma_ar="جَدِيد", lemma_ar_bare="جديد", gloss_en="new", source="test")
    db_session.add(lemma)
    db_session.commit()

    resp = client.post(f"/api/words/{lemma.lemma_id}/suspend")
    assert resp.status_code == 200
    assert resp.json()["state"] == "suspended"

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk is not None
    assert ulk.knowledge_state == "suspended"


def test_suspend_not_found(client):
    resp = client.post("/api/words/99999/suspend")
    assert resp.status_code == 404


def test_unsuspend_word(client, db_session):
    lemma = _seed_word(db_session, state="learning")
    client.post(f"/api/words/{lemma.lemma_id}/suspend")

    resp = client.post(f"/api/words/{lemma.lemma_id}/unsuspend")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "learning"
    assert data["was_suspended"] is True

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "learning"
    assert ulk.fsrs_card_json is not None


def test_unsuspend_not_suspended(client, db_session):
    lemma = _seed_word(db_session, state="learning")
    resp = client.post(f"/api/words/{lemma.lemma_id}/unsuspend")
    assert resp.status_code == 200
    assert resp.json()["was_suspended"] is False


def test_unsuspend_not_found(client):
    resp = client.post("/api/words/99999/unsuspend")
    assert resp.status_code == 404


# --- Reactivation Helper Tests ---

def test_reactivate_if_suspended(db_session):
    lemma = _seed_word(db_session, state="learning")
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    ulk.knowledge_state = "suspended"
    db_session.commit()

    result = reactivate_if_suspended(db_session, lemma.lemma_id, "textbook_scan")
    assert result is True

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "learning"
    assert ulk.source == "textbook_scan"


def test_reactivate_not_suspended(db_session):
    lemma = _seed_word(db_session, state="learning")
    result = reactivate_if_suspended(db_session, lemma.lemma_id, "test")
    assert result is False


def test_reactivate_no_ulk(db_session):
    result = reactivate_if_suspended(db_session, 99999, "test")
    assert result is False


# --- Suspended Word Filtering Tests ---

def test_suspended_word_skipped_in_sentence_review(client, db_session):
    """Suspended words should not get FSRS credit when reviewing a sentence."""
    lemma = _seed_word(db_session, state="suspended")
    sentence = _seed_sentence(db_session, lemma)

    resp = client.post("/api/review/submit-sentence", json={
        "sentence_id": sentence.id,
        "primary_lemma_id": lemma.lemma_id,
        "comprehension_signal": "understood",
        "review_mode": "reading",
        "missed_lemma_ids": [],
        "confused_lemma_ids": [],
        "response_ms": 1000,
    })
    assert resp.status_code == 200

    # ULK should still be suspended
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "suspended"


# --- Introduce Word with Suspended Reactivation ---

def test_introduce_word_reactivates_suspended(db_session):
    from app.services.word_selector import introduce_word

    lemma = _seed_word(db_session, state="learning")
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    ulk.knowledge_state = "suspended"
    db_session.commit()

    result = introduce_word(db_session, lemma.lemma_id, source="study")
    assert result["reactivated"] is True
    assert result["state"] == "learning"

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "learning"


# --- Content Flags Tests ---

def test_create_word_flag(client, db_session):
    lemma = _seed_word(db_session)
    resp = client.post("/api/flags", json={
        "content_type": "word_gloss",
        "lemma_id": lemma.lemma_id,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert "flag_id" in data

    flag = db_session.query(ContentFlag).filter_by(id=data["flag_id"]).first()
    assert flag is not None
    assert flag.content_type == "word_gloss"
    assert flag.lemma_id == lemma.lemma_id


def test_create_sentence_flag(client, db_session):
    lemma = _seed_word(db_session)
    sentence = _seed_sentence(db_session, lemma)

    resp = client.post("/api/flags", json={
        "content_type": "sentence_english",
        "sentence_id": sentence.id,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_flag_invalid_type(client, db_session):
    resp = client.post("/api/flags", json={
        "content_type": "invalid_type",
    })
    assert resp.status_code == 400


def test_flag_word_not_found(client, db_session):
    resp = client.post("/api/flags", json={
        "content_type": "word_gloss",
        "lemma_id": 99999,
    })
    assert resp.status_code == 404


def test_flag_missing_lemma_id(client, db_session):
    resp = client.post("/api/flags", json={
        "content_type": "word_gloss",
    })
    assert resp.status_code == 400


def test_list_flags(client, db_session):
    lemma = _seed_word(db_session)
    client.post("/api/flags", json={"content_type": "word_gloss", "lemma_id": lemma.lemma_id})
    client.post("/api/flags", json={"content_type": "word_gloss", "lemma_id": lemma.lemma_id})

    resp = client.get("/api/flags")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_flags_filter_status(client, db_session):
    lemma = _seed_word(db_session)
    client.post("/api/flags", json={"content_type": "word_gloss", "lemma_id": lemma.lemma_id})

    resp = client.get("/api/flags?status=fixed")
    assert resp.status_code == 200
    assert len(resp.json()) == 0

    resp = client.get("/api/flags?status=pending")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# --- Activity Log Tests ---

def test_activity_log_empty(client, db_session):
    resp = client.get("/api/activity")
    assert resp.status_code == 200
    assert resp.json() == []


def test_activity_log_entries(client, db_session):
    entry = ActivityLog(
        event_type="flag_resolved",
        summary="Fixed translation for كتاب",
        detail_json={"flag_id": 1},
    )
    db_session.add(entry)
    db_session.commit()

    resp = client.get("/api/activity")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["event_type"] == "flag_resolved"
    assert "كتاب" in data[0]["summary"]
