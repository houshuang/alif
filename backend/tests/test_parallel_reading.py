from pathlib import Path
import pytest
from app.models import ReadingPilotEvent, ReviewLog, SentenceReviewLog, UserLemmaKnowledge, Lemma
from app.services.parallel_reading import get_parallel_library

def event(**changes):
    return dict(client_event_id="parallel:test:1", reader_id="parallel-reading", version=1,
        text_id="aesop-jar", paragraph_id="1", occurred_at="2026-10-10T12:00:00Z", kind="open",
        support="grc", revealed=False, all=False, vowels=False, size=1, reread=False, completed=False) | changes

def test_bundled_content_is_identical_and_has_source_provenance():
    root = Path(__file__).resolve().parents[2]
    assert (root / "backend/app/data/parallel_reading_v1.json").read_bytes() == (root / "frontend/lib/data/parallel-reading-v1.json").read_bytes()
    for text in get_parallel_library()["texts"]:
        assert text["source_url"].startswith("https://") and text["source_note"]
        assert set(text["versions"]) == {"ar", "grc", "la", "ru"}
        for paragraph in text["paragraphs"]:
            assert all(paragraph[lang] for lang in ("ar", "grc", "la", "ru"))

def test_parallel_interactions_are_idempotent_and_never_schedule(client, db_session, monkeypatch):
    logged = []
    monkeypatch.setattr("app.services.parallel_reading.log_interaction", lambda **kw: logged.append(kw))
    actions = [dict(kind=k) for k in ("open", "leave", "library", "select", "passage", "support", "reveal", "all", "vowels", "size", "about", "reread", "complete")]
    actions += [dict(kind="clue", clue_id="1", visible=True), dict(kind="reflection", effort="some-work")]
    for n, action in enumerate(actions):
        payload = event(client_event_id=f"parallel:test:{n}", **action)
        assert client.post("/api/books/parallel/events", json=payload).json() == {"status": "recorded"}
        assert client.post("/api/books/parallel/events", json=payload).json() == {"status": "duplicate"}
    assert db_session.query(ReadingPilotEvent).count() == len(actions)
    assert len(logged) == len(actions)
    assert all(e["event"] == "parallel_reading" for e in logged)
    for model in (ReviewLog, SentenceReviewLog, UserLemmaKnowledge, Lemma):
        assert db_session.query(model).count() == 0

@pytest.mark.parametrize("changes", [dict(text_id="missing"), dict(paragraph_id="999"), dict(version=2),
    dict(kind="rating"), dict(support="en"), dict(size=2), dict(kind="clue", clue_id="99"),
    dict(clue_id="1"), dict(kind="reflection"), dict(effort="smooth"),
    dict(occurred_at="2026-10-10T12:00:00"), dict(rating=4)])
def test_invalid_evidence_is_rejected_without_writes(client, db_session, changes):
    assert client.post("/api/books/parallel/events", json=event(**changes)).status_code == 422
    assert db_session.query(ReadingPilotEvent).count() == 0

def test_id_collision_preserves_first_event(client, db_session):
    assert client.post("/api/books/parallel/events", json=event()).status_code == 200
    assert client.post("/api/books/parallel/events", json=event(support="ru")).status_code == 422
    assert db_session.query(ReadingPilotEvent).one().payload_json["support"] == "grc"
