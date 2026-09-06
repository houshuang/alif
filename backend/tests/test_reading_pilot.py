import hashlib
import json
from pathlib import Path
from app.models import ReadingPilotEvent, ReviewLog, SentenceReviewLog, UserLemmaKnowledge


def event(**changes):
    return dict(client_event_id="pilot:1", attempt_id="pilot", pilot_id="momo-wings",
                version=1, session_id="wings-1", occurred_at="2026-09-05T12:00:00Z",
                kind="complete", phase="reread", reading_ms=180000, rereading_ms=45000,
                followed="partly", wanted_more="yes") | changes


def test_preserves_source_and_pairs_every_block(client):
    data = client.get("/api/books/reading-pilot").json()
    path = Path(__file__).resolve().parents[2] / data["source"]["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == data["source"]["sha256"]
    source = json.loads(path.read_text())
    running = " ".join(" ".join(p["arabic"] for p in source["pages"]).split())
    assert len(data["sessions"]) == 3
    ids = []
    for session in data["sessions"]:
        assert session["orientation"] and session["simpler_arabic"] and session["simpler_english"]
        for block in session["blocks"]:
            assert " ".join(block["arabic"].split()) in running
            assert block["english"]
            for note in block["help"]:
                assert note["anchor"] in block["arabic"]
                assert note["form"] and note["meaning"] and note["explanation"]
                ids.append(note["id"])
    assert len(ids) == len(set(ids))


def test_completion_retry_never_creates_word_credit(client, db_session):
    for _ in range(2):
        response = client.post("/api/books/reading-pilot/events", json=event())
        assert response.status_code == 200
    assert response.json()["status"] == "duplicate"
    assert db_session.query(ReadingPilotEvent).count() == 1
    for model in (ReviewLog, SentenceReviewLog, UserLemmaKnowledge):
        assert db_session.query(model).count() == 0
    stored = db_session.query(ReadingPilotEvent).one().payload_json
    assert stored["followed"] == "partly" and stored["wanted_more"] == "yes"
    assert stored["reading_ms"] == 180000


def test_rejects_invalid_evidence(client, db_session):
    for changes in [dict(session_id="wings-99"), dict(version=2), dict(reading_ms=-1),
                    dict(kind="help", help_id="3-1-1"), dict(help_id="1-1-1"),
                    dict(occurred_at="2026-09-05T12:00:00"), dict(followed="fluent")]:
        assert client.post("/api/books/reading-pilot/events", json=event(**changes)).status_code == 422
    assert db_session.query(ReadingPilotEvent).count() == 0


def test_help_and_reopen_retain_attempt_identity(client, db_session):
    for payload in [event(kind="help", phase="read", help_id="1-1-1", visible=True),
                    event(client_event_id="pilot:2", kind="open")]:
        assert client.post("/api/books/reading-pilot/events", json=payload).status_code == 200
    records = db_session.query(ReadingPilotEvent).all()
    assert len(records) == 2
    assert all(row.payload_json["attempt_id"] == "pilot" for row in records)
