from datetime import datetime, timezone

from app.models import Lemma, UserLemmaKnowledge, Sentence, SentenceWord, ReviewLog
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
    lemma.grammar_features_json = ["present", "singular"]
    lemma.forms_json = {"plural": "كِلَاب"}
    db_session.commit()
    resp = client.get(f"/api/words/{lemma.lemma_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["lemma_ar"] == "كَلْب"
    assert data["knowledge_state"] == "learning"
    assert data["forms_json"]["plural"] == "كِلَاب"
    assert any(g["feature_key"] == "present" for g in data["grammar_features"])


def test_get_word_sentence_stats(client, db_session):
    lemma = _seed_word(db_session, arabic="قَلَم", bare="قلم", gloss="pen")
    sentence = Sentence(
        arabic_text="هٰذَا قَلَمٌ جَدِيدٌ",
        arabic_diacritized="هٰذَا قَلَمٌ جَدِيدٌ",
        english_translation="This is a new pen",
        target_lemma_id=lemma.lemma_id,
    )
    db_session.add(sentence)
    db_session.flush()
    db_session.add(
        SentenceWord(
            sentence_id=sentence.id,
            position=1,
            surface_form="قَلَمٌ",
            lemma_id=lemma.lemma_id,
        )
    )
    db_session.add_all([
        ReviewLog(
            lemma_id=lemma.lemma_id,
            rating=1,
            reviewed_at=datetime.now(timezone.utc),
            session_id="s1",
            sentence_id=sentence.id,
            credit_type="primary",
            review_mode="reading",
        ),
        ReviewLog(
            lemma_id=lemma.lemma_id,
            rating=2,
            reviewed_at=datetime.now(timezone.utc),
            session_id="s1",
            sentence_id=sentence.id,
            credit_type="collateral",
            review_mode="reading",
        ),
        ReviewLog(
            lemma_id=lemma.lemma_id,
            rating=3,
            reviewed_at=datetime.now(timezone.utc),
            session_id="s1",
            sentence_id=sentence.id,
            credit_type="primary",
            review_mode="reading",
        ),
    ])
    db_session.commit()

    resp = client.get(f"/api/words/{lemma.lemma_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sentence_stats"]) == 1
    s = data["sentence_stats"][0]
    assert s["sentence_id"] == sentence.id
    assert s["seen_count"] == 3
    assert s["missed_count"] == 1
    assert s["confused_count"] == 1
    assert s["understood_count"] == 1
    assert s["primary_count"] == 2
    assert s["collateral_count"] == 1
    assert s["accuracy_pct"] == 33.3


def test_get_word_sentence_stats_includes_unreviewed_sentences(client, db_session):
    lemma = _seed_word(db_session, arabic="دَرْس", bare="درس", gloss="lesson")
    sentence1 = Sentence(
        arabic_text="هٰذَا دَرْسٌ",
        arabic_diacritized="هٰذَا دَرْسٌ",
        english_translation="This is a lesson",
        target_lemma_id=lemma.lemma_id,
    )
    sentence2 = Sentence(
        arabic_text="الدَّرْسُ سَهْلٌ",
        arabic_diacritized="الدَّرْسُ سَهْلٌ",
        english_translation="The lesson is easy",
        target_lemma_id=lemma.lemma_id,
    )
    db_session.add_all([sentence1, sentence2])
    db_session.flush()
    db_session.add_all([
        SentenceWord(
            sentence_id=sentence1.id,
            position=1,
            surface_form="دَرْسٌ",
            lemma_id=lemma.lemma_id,
        ),
        SentenceWord(
            sentence_id=sentence2.id,
            position=0,
            surface_form="الدَّرْسُ",
            lemma_id=lemma.lemma_id,
        ),
        ReviewLog(
            lemma_id=lemma.lemma_id,
            rating=3,
            reviewed_at=datetime.now(timezone.utc),
            session_id="s2",
            sentence_id=sentence1.id,
            credit_type="primary",
            review_mode="reading",
        ),
    ])
    db_session.commit()

    resp = client.get(f"/api/words/{lemma.lemma_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sentence_stats"]) == 2

    by_id = {s["sentence_id"]: s for s in data["sentence_stats"]}
    assert by_id[sentence1.id]["seen_count"] == 1
    assert by_id[sentence2.id]["seen_count"] == 0
    assert by_id[sentence2.id]["accuracy_pct"] is None


def test_get_word_not_found(client):
    resp = client.get("/api/words/9999")
    assert resp.status_code == 404


def test_analyze_word(client):
    resp = client.post("/api/analyze/word", json={"word": "كَلْب"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["word"] == "كَلْب"
    assert data["source"] in ("camel", "stub")


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


def test_next_sentences_prefetch_skips_logging(client, db_session, monkeypatch):
    lemma = _seed_word(db_session)
    sentence = Sentence(
        arabic_text="الولد",
        arabic_diacritized="الْوَلَدُ",
        english_translation="the boy",
        target_lemma_id=lemma.lemma_id,
    )
    db_session.add(sentence)
    db_session.flush()
    db_session.add(
        SentenceWord(
            sentence_id=sentence.id,
            position=0,
            surface_form="الْوَلَدُ",
            lemma_id=lemma.lemma_id,
        )
    )
    db_session.commit()

    router_events: list[str] = []
    selector_events: list[str] = []

    def _capture_router(event: str, **_kwargs):
        router_events.append(event)

    def _capture_selector(event: str, **_kwargs):
        selector_events.append(event)

    monkeypatch.setattr("app.routers.review.log_interaction", _capture_router)
    monkeypatch.setattr("app.services.sentence_selector.log_interaction", _capture_selector)

    resp = client.get("/api/review/next-sentences?limit=5&mode=reading&prefetch=true")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "session_start" not in router_events
    assert "sentence_selected" not in selector_events
