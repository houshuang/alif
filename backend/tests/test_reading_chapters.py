import base64

import pytest

from app.models import ReadingPilotEvent, ReviewLog, SentenceReviewLog, UserLemmaKnowledge, Lemma
from app.services import reading_chapters


def event(**changes):
    return dict(client_event_id="chapter:test:1", attempt_id="chapter:test", reader_id="library-bridge",
                version=1, chapter_id="drawing", occurred_at="2026-09-16T10:00:00Z",
                kind="complete", stage="finished", vowels=True) | changes


def test_chapters_have_exact_token_glosses_and_bounded_preview(client):
    data = client.get("/api/books/chapters").json()
    assert data["id"] == "library-bridge" and data["version"] == 1
    token_ids = []
    for chapter in data["chapters"]:
        assert 1 <= len(chapter["preview"]) <= 6
        assert 100 <= chapter["word_count"] <= 200
        assert chapter["word_count"] == sum(len(p["tokens"]) for p in chapter["paragraphs"])
        for p in chapter["paragraphs"]:
            assert p["english"]
            assert " ".join(t["surface"] for t in p["tokens"]) == p["arabic"]
            for token in p["tokens"]:
                assert token["gloss"] and token["surface"]
                assert "lemma_id" not in token  # editorial glosses, not guessed scheduler identities
                token_ids.append(token["id"])
    assert len(token_ids) == len(set(token_ids))


def test_all_chapter_actions_are_inert_for_scheduling_and_idempotent(client, db_session):
    examples = [dict(kind=k) for k in ("open", "start", "preview", "pause", "complete", "reread", "vowels")]
    examples += [dict(kind="word", paragraph_id="drawing-3", token_id="drawing-3-41"),
                 dict(kind="translation", paragraph_id="drawing-1", visible=True),
                 dict(kind="feedback", effort="some-work", text="I had to parse ورقة again.")]
    for n, changes in enumerate(examples):
        payload = event(client_event_id=f"chapter:test:{n}", **changes)
        assert client.post("/api/books/chapters/events", json=payload).json()["status"] == "recorded"
        assert client.post("/api/books/chapters/events", json=payload).json()["status"] == "duplicate"
    assert db_session.query(ReadingPilotEvent).count() == len(examples)
    for model in (ReviewLog, SentenceReviewLog, UserLemmaKnowledge, Lemma):
        assert db_session.query(model).count() == 0


@pytest.mark.parametrize("changes", [dict(chapter_id="missing"), dict(version=2), dict(kind="rating"),
    dict(paragraph_id="bank-1"), dict(kind="translation"), dict(kind="word", token_id="drawing-1-1"),
    dict(kind="word", paragraph_id="drawing-1", token_id="drawing-3-41"), dict(token_id="drawing-1-1"),
    dict(occurred_at="2026-09-16T10:00:00"), dict(text="misplaced feedback"),
    dict(kind="feedback", text="x" * 4001), dict(client_event_id="../../bad"), dict(rating=4)])
def test_rejects_unattributable_events(client, db_session, changes):
    assert client.post("/api/books/chapters/events", json=event(**changes)).status_code == 422
    assert db_session.query(ReadingPilotEvent).count() == 0


def voice(**changes):
    return dict(event=event(kind="feedback", text="The ending was clear."), mime_type="audio/webm",
                audio_base64=base64.b64encode(b"\x1a\x45\xdf\xa3test-audio").decode(), duration_ms=1200) | changes


def test_voice_retry_retains_one_blob_and_one_event_and_can_be_retrieved(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(reading_chapters, "VOICE_DIR", tmp_path)
    for _ in range(2):
        assert client.post("/api/books/chapters/voice", json=voice()).status_code == 200
    assert len(list(tmp_path.iterdir())) == 1
    stored = db_session.query(ReadingPilotEvent).one().payload_json
    assert "audio_base64" not in stored
    assert stored["voice_duration_ms"] == 1200 and stored["text"] == "The ending was clear."
    response = client.get("/api/books/chapters/voice/chapter:test:1")
    assert response.status_code == 200 and response.content == base64.b64decode(voice()["audio_base64"])
    for model in (ReviewLog, SentenceReviewLog, UserLemmaKnowledge, Lemma):
        assert db_session.query(model).count() == 0


@pytest.mark.parametrize("changes", [dict(audio_base64="not base64!"), dict(audio_base64="YQ=="),
    dict(audio_base64=""), dict(audio_base64="a" * 2_000_001), dict(mime_type="text/html"),
    dict(duration_ms=66000), dict(event=event()), dict(mime_type="audio/mp4")])
def test_bad_voice_does_not_write_files_or_feedback(client, db_session, tmp_path, monkeypatch, changes):
    monkeypatch.setattr(reading_chapters, "VOICE_DIR", tmp_path)
    assert client.post("/api/books/chapters/voice", json=voice(**changes)).status_code == 422
    assert list(tmp_path.iterdir()) == []
    assert db_session.query(ReadingPilotEvent).count() == 0


def test_event_collision_cannot_overwrite_reflection(client, db_session):
    assert client.post("/api/books/chapters/events", json=event(kind="feedback", text="original")).status_code == 200
    assert client.post("/api/books/chapters/events", json=event(kind="feedback", text="replacement")).status_code == 422
    assert db_session.query(ReadingPilotEvent).one().payload_json["text"] == "original"


def test_missing_audio_returns_404(client):
    assert client.get("/api/books/chapters/voice/chapter:missing").status_code == 404
