from app.models import Lemma, UserLemmaKnowledge
from app.services.fsrs_service import create_new_card


def _seed_word(db_session, arabic="كَلْب", bare="كلب", gloss="dog"):
    lemma = Lemma(lemma_ar=arabic, lemma_ar_bare=bare, gloss_en=gloss, source="test")
    db_session.add(lemma)
    db_session.flush()
    knowledge = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        fsrs_card_json=create_new_card(),
        source="test",
        times_seen=0,
        times_correct=0,
    )
    db_session.add(knowledge)
    db_session.commit()
    return lemma


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["app"] == "alif"


def test_list_words(client, db_session):
    _seed_word(db_session)
    resp = client.get("/api/words")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["lemma_ar"] == "كَلْب"


def test_list_words_filter(client, db_session):
    _seed_word(db_session)
    resp = client.get("/api/words?status=learning")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    resp = client.get("/api/words?status=known")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_get_word(client, db_session):
    lemma = _seed_word(db_session)
    resp = client.get(f"/api/words/{lemma.lemma_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["lemma_ar"] == "كَلْب"
    assert data["knowledge_state"] == "learning"


def test_get_word_not_found(client):
    resp = client.get("/api/words/9999")
    assert resp.status_code == 404


def test_review_next(client, db_session):
    _seed_word(db_session)
    resp = client.get("/api/review/next")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["lemma_ar"] == "كَلْب"


def test_review_submit(client, db_session):
    lemma = _seed_word(db_session)
    resp = client.post("/api/review/submit", json={
        "lemma_id": lemma.lemma_id,
        "rating": 3,
        "response_ms": 1500,
        "session_id": "test-session",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["lemma_id"] == lemma.lemma_id
    assert "next_due" in data


def test_analyze_word(client):
    resp = client.post("/api/analyze/word", json={"word": "كَلْب"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["word"] == "كَلْب"
    assert data["source"] == "mock"


def test_analyze_sentence(client):
    resp = client.post("/api/analyze/sentence", json={"sentence": "هذا كلب"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["words"]) == 2


def test_stats(client, db_session):
    _seed_word(db_session)
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_words"] >= 1
    assert data["learning"] >= 1


def test_import_duolingo(client, db_session):
    resp = client.post("/api/import/duolingo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] > 0
