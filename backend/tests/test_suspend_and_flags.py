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


# --- Rare-word suspend: cascade-deactivate sentences + canonical resolution ---

def test_suspend_cascade_deactivates_active_sentences(client, db_session):
    """Suspending a word should deactivate any active sentences targeting it."""
    lemma = _seed_word(db_session, arabic="نَادِر", bare="نادر", gloss="rare")
    s1 = _seed_sentence(db_session, lemma, text="هَذَا نَادِرٌ جِدّاً")
    s2 = _seed_sentence(db_session, lemma, text="كَلِمَةٌ نَادِرَة")
    s1.is_active = True
    s2.is_active = True
    db_session.commit()

    resp = client.post(f"/api/words/{lemma.lemma_id}/suspend")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sentences_deactivated"] == 2
    assert data["canonical_lemma_id"] == lemma.lemma_id

    db_session.refresh(s1)
    db_session.refresh(s2)
    assert s1.is_active is False
    assert s2.is_active is False


def test_suspend_resolves_canonical(client, db_session):
    """Suspending a variant should redirect to the canonical's ULK."""
    canonical = _seed_word(db_session, arabic="قَرَأَ", bare="قرا", gloss="to read")
    variant = Lemma(
        lemma_ar="اقرئيه", lemma_ar_bare="اقرئيه", gloss_en="read.imp+pron",
        source="test", canonical_lemma_id=canonical.lemma_id,
    )
    db_session.add(variant)
    db_session.commit()

    resp = client.post(f"/api/words/{variant.lemma_id}/suspend")
    assert resp.status_code == 200
    data = resp.json()
    assert data["canonical_lemma_id"] == canonical.lemma_id

    canon_ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=canonical.lemma_id).first()
    assert canon_ulk.knowledge_state == "suspended"


def test_suspend_accepts_frequency_rank_payload(client, db_session):
    """Endpoint accepts optional payload with frequency_rank — logged but not stored."""
    lemma = _seed_word(db_session, arabic="فُلْكُلُورِيّ", bare="فلكلوري", gloss="folkloric")
    resp = client.post(
        f"/api/words/{lemma.lemma_id}/suspend",
        json={"frequency_rank": 4735, "source": "rare_word_banner"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "suspended"


def test_suspend_idempotent_no_extra_cascade(client, db_session):
    """Re-suspending already-suspended word doesn't re-deactivate sentences."""
    lemma = _seed_word(db_session, arabic="مَهْجُور", bare="مهجور", gloss="abandoned")
    s = _seed_sentence(db_session, lemma)
    s.is_active = True
    db_session.commit()

    client.post(f"/api/words/{lemma.lemma_id}/suspend")
    db_session.refresh(s)
    assert s.is_active is False

    resp = client.post(f"/api/words/{lemma.lemma_id}/suspend")
    assert resp.status_code == 200
    data = resp.json()
    assert data["already_suspended"] is True
    assert data["sentences_deactivated"] == 0


# --- frequency_rank on intro cards ---

def test_intro_card_includes_frequency_fields(db_session):
    """_build_reintro_cards should populate frequency_rank from FrequencyCoreEntry."""
    from app.models import FrequencyCoreEntry
    from app.services.sentence_selector import _build_reintro_cards

    lemma = _seed_word(db_session, arabic="غَرِيب", bare="غريب", gloss="strange")
    lemma.gates_completed_at = datetime.now(timezone.utc)
    db_session.add(FrequencyCoreEntry(
        core_rank=4500, lemma_id=lemma.lemma_id, lemma_key="غريب",
        display_form="غَرِيب", broad_source_count=2,
    ))
    db_session.commit()

    cards = _build_reintro_cards(db_session, {lemma.lemma_id})
    assert len(cards) == 1
    assert cards[0]["frequency_rank"] == 4500
    assert cards[0]["frequency_source_count"] == 2


def test_intro_card_frequency_null_when_not_in_core(db_session):
    """Lemma with no FrequencyCoreEntry gets frequency_rank=None."""
    from app.services.sentence_selector import _build_reintro_cards

    lemma = _seed_word(db_session, arabic="مَخْفِيّ", bare="مخفي", gloss="hidden")
    lemma.gates_completed_at = datetime.now(timezone.utc)
    db_session.commit()

    cards = _build_reintro_cards(db_session, {lemma.lemma_id})
    assert len(cards) == 1
    assert cards[0]["frequency_rank"] is None
    assert cards[0]["frequency_source_count"] is None


def test_intro_card_frequency_uses_canonical(db_session):
    """For a variant lemma, FCE lookup should follow the canonical pointer."""
    from app.models import FrequencyCoreEntry
    from app.services.sentence_selector import _build_reintro_cards

    canon = _seed_word(db_session, arabic="قَلَم", bare="قلم", gloss="pen")
    canon.gates_completed_at = datetime.now(timezone.utc)
    db_session.add(FrequencyCoreEntry(
        core_rank=1200, lemma_id=canon.lemma_id, lemma_key="قلم",
        display_form="قَلَم", broad_source_count=4,
    ))
    variant = Lemma(
        lemma_ar="بِالقَلَم", lemma_ar_bare="بالقلم", gloss_en="with-the-pen",
        source="test", canonical_lemma_id=canon.lemma_id,
        gates_completed_at=datetime.now(timezone.utc),
    )
    db_session.add(variant)
    db_session.commit()

    cards = _build_reintro_cards(db_session, {variant.lemma_id})
    assert len(cards) == 1
    assert cards[0]["frequency_rank"] == 1200
    assert cards[0]["frequency_source_count"] == 4


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
    lemma1 = _seed_word(db_session, arabic="كَلْب", bare="كلب", gloss="dog")
    lemma2 = _seed_word(db_session, arabic="كِتَاب", bare="كتاب", gloss="book")
    client.post("/api/flags", json={"content_type": "word_gloss", "lemma_id": lemma1.lemma_id})
    client.post("/api/flags", json={"content_type": "word_gloss", "lemma_id": lemma2.lemma_id})

    resp = client.get("/api/flags")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_duplicate_flag_returns_existing(client, db_session):
    lemma = _seed_word(db_session)
    resp1 = client.post("/api/flags", json={"content_type": "word_gloss", "lemma_id": lemma.lemma_id})
    resp2 = client.post("/api/flags", json={"content_type": "word_gloss", "lemma_id": lemma.lemma_id})
    assert resp1.json()["flag_id"] == resp2.json()["flag_id"]
    assert resp2.json()["status"] == "already_flagged"


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
