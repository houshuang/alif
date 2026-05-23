from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import ActivityLog, Lemma, Sentence


def _client(tmp_db):
    def _override():
        db = tmp_db()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app), tmp_db


def test_report_sentence_creates_flag_and_dedupes(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            lemma = Lemma(
                language_code="el",
                lemma_form="κόσμος",
                lemma_bare="κοσμος",
                source="test",
            )
            db.add(lemma)
            db.flush()
            sentence = Sentence(
                language_code="el",
                text="Ο κόσμος είναι μεγάλος.",
                translation_en="The world is big.",
                source="test",
                target_lemma_id=lemma.lemma_id,
                mappings_verified_at=None,
            )
            db.add(sentence)
            db.commit()
            sentence_id = sentence.id

        first = client.post("/api/flags", json={
            "content_type": "sentence",
            "sentence_id": sentence_id,
        })
        assert first.status_code == 200
        assert first.json()["status"] == "pending"

        second = client.post("/api/flags", json={
            "content_type": "sentence",
            "sentence_id": sentence_id,
        })
        assert second.status_code == 200
        assert second.json()["status"] == "already_flagged"
        assert second.json()["flag_id"] == first.json()["flag_id"]

        listed = client.get("/api/flags?status=pending")
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        with factory() as db:
            activity = db.query(ActivityLog).filter(
                ActivityLog.event_type == "content_reported",
            ).one()
            assert activity.language_code == "el"
            assert activity.detail_json["sentence_id"] == sentence_id
    finally:
        app.dependency_overrides.clear()
