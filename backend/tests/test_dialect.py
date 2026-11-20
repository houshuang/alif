import json

import pytest

from app.models import DialectLineState, Lemma, UserLemmaKnowledge
from app.routers import dialect as dialect_router

LINE_ID = "layl30-0001"


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    (tmp_path / "audio").mkdir()
    (tmp_path / "lines.json").write_text(
        json.dumps({
            "sources": [
                {"id": "layl30", "title": "Layl 30", "audio": "layl30.mp3"},
                {"id": "nofile", "title": "Missing", "audio": "nofile.mp3"},
                {"id": "badtype", "title": "Bad", "audio": "badtype.wav"},
            ],
            "lines": [{"id": LINE_ID, "src": "layl30", "ar": "شو؟", "en": "What?", "s": 1.0, "e": 2.0}],
            "expressions": [{"id": "lb-shu", "ar": "شو", "en": "what"}],
        }),
        encoding="utf-8",
    )
    (tmp_path / "audio" / "layl30.mp3").write_bytes(b"0123456789")
    (tmp_path / "audio" / "badtype.wav").write_bytes(b"0123")
    monkeypatch.setattr(dialect_router, "DIALECT_DIR", tmp_path)
    return tmp_path


def test_empty_when_catalogue_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(dialect_router, "DIALECT_DIR", tmp_path / "absent")
    assert client.get("/api/dialect").json() == {"sources": [], "lines": [], "expressions": []}


def test_catalogue_marks_lines_not_hard_by_default(client, catalogue):
    body = client.get("/api/dialect").json()
    assert body["lines"][0]["hard"] is False
    assert body["expressions"][0]["id"] == "lb-shu"


def test_audio_supports_range_requests(client, catalogue):
    res = client.get("/api/dialect/audio/layl30", headers={"Range": "bytes=0-3"})
    assert res.status_code == 206
    assert res.content == b"0123"
    assert res.headers["content-type"] == "audio/mpeg"


@pytest.mark.parametrize("source_id", ["unknown", "..%2Flines", "Layl30", "nofile", "badtype"])
def test_unknown_invalid_or_missing_audio_is_404(client, catalogue, source_id):
    assert client.get(f"/api/dialect/audio/{source_id}").status_code == 404


def test_hard_flag_round_trips(client, catalogue, db_session):
    url = f"/api/dialect/lines/{LINE_ID}/event"
    assert client.post(url, json={"event": "hard", "hard": True}).json() == {"line_id": LINE_ID, "hard": True}
    assert client.get("/api/dialect").json()["lines"][0]["hard"] is True
    assert client.post(url, json={"event": "hard", "hard": False}).json()["hard"] is False
    assert client.get("/api/dialect").json()["lines"][0]["hard"] is False


def test_listening_events_count_without_changing_hard(client, catalogue, db_session):
    url = f"/api/dialect/lines/{LINE_ID}/event"
    client.post(url, json={"event": "hard", "hard": True})
    for event in ["play", "play", "arabic", "english"]:
        assert client.post(url, json={"event": event}).json()["hard"] is True
    db_session.expire_all()
    row = db_session.get(DialectLineState, LINE_ID)
    assert (row.plays, row.arabic_reveals, row.english_reveals) == (2, 1, 1)


def test_hard_requires_value_and_unknown_line_is_404(client, catalogue):
    assert client.post(f"/api/dialect/lines/{LINE_ID}/event", json={"event": "hard"}).status_code == 422
    assert client.post("/api/dialect/lines/layl30-9999/event", json={"event": "play"}).status_code == 404
    assert client.post("/api/dialect/lines/BAD_ID/event", json={"event": "play"}).status_code == 404


def test_events_never_touch_word_learning(client, catalogue, db_session):
    url = f"/api/dialect/lines/{LINE_ID}/event"
    for body in [{"event": "play"}, {"event": "arabic"}, {"event": "hard", "hard": True}]:
        client.post(url, json=body)
    assert db_session.query(UserLemmaKnowledge).count() == 0
    assert db_session.query(Lemma).count() == 0
