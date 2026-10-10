from pathlib import Path
import pytest
from app.models import ReadingPilotEvent, ReviewLog, SentenceReviewLog, UserLemmaKnowledge, Lemma
from app.services.parallel_reading import get_parallel_library

def event(**changes):
    return dict(client_event_id="parallel:test:1", reader_id="parallel-reading", version=2,
        text_id="aesop-jar", paragraph_id="1", occurred_at="2026-10-10T12:00:00Z", kind="open",
        support="grc", revealed=False, all=False, vowels=False, size=1, reread=False, completed=False) | changes

def test_bundled_content_is_identical_and_has_source_provenance():
    root = Path(__file__).resolve().parents[2]
    assert (root / "backend/app/data/parallel_reading_v2.json").read_bytes() == (root / "frontend/lib/data/parallel-reading-v2.json").read_bytes()
    for text in get_parallel_library()["texts"]:
        assert text["source_url"].startswith("https://") and text["source_note"]
        assert set(text["versions"]) == {"ar", "grc", "la", "ru", "en"}
        for paragraph in text["paragraphs"]:
            assert all(paragraph[lang] for lang in ("ar", "grc", "la", "ru", "en"))

def test_parallel_interactions_are_idempotent_and_never_schedule(client, db_session, monkeypatch):
    logged = []
    monkeypatch.setattr("app.services.parallel_reading.log_interaction", lambda **kw: logged.append(kw))
    actions = [dict(kind=k) for k in ("open", "leave", "library", "select", "passage", "support", "reveal", "all", "vowels", "size", "about", "reread", "complete")]
    actions += [dict(kind="clue", clue_id="1", visible=True), dict(kind="reflection", effort="some-work"), dict(kind="word", token_id="1-1", visible=True), dict(kind="display", panel="appearance", visible=True)]
    for n, action in enumerate(actions):
        payload = event(client_event_id=f"parallel:test:{n}", **action)
        assert client.post("/api/books/parallel/events", json=payload).json() == {"status": "recorded"}
        assert client.post("/api/books/parallel/events", json=payload).json() == {"status": "duplicate"}
    assert db_session.query(ReadingPilotEvent).count() == len(actions)
    assert len(logged) == len(actions)
    assert all(e["event"] == "parallel_reading" for e in logged)
    for model in (ReviewLog, SentenceReviewLog, UserLemmaKnowledge, Lemma):
        assert db_session.query(model).count() == 0

@pytest.mark.parametrize("changes", [dict(text_id="missing"), dict(paragraph_id="999"), dict(version=3),
    dict(kind="rating"), dict(support="de"), dict(size=2), dict(kind="clue", clue_id="99"),
    dict(clue_id="1"), dict(kind="reflection"), dict(effort="smooth"),
    dict(occurred_at="2026-10-10T12:00:00"), dict(rating=4)])
def test_invalid_evidence_is_rejected_without_writes(client, db_session, changes):
    assert client.post("/api/books/parallel/events", json=event(**changes)).status_code == 422
    assert db_session.query(ReadingPilotEvent).count() == 0

def test_id_collision_preserves_first_event(client, db_session):
    assert client.post("/api/books/parallel/events", json=event()).status_code == 200
    assert client.post("/api/books/parallel/events", json=event(support="ru")).status_code == 422
    assert db_session.query(ReadingPilotEvent).one().payload_json["support"] == "grc"


@pytest.mark.parametrize("changes", [dict(kind="word"), dict(kind="word", token_id="2-1"),
    dict(token_id="1-1"), dict(kind="display"), dict(panel="phrases"),
    dict(version=1, kind="word", token_id="1-1"), dict(version=1, support="en")])
def test_word_and_control_identity_is_edition_scoped(client, db_session, changes):
    assert client.post("/api/books/parallel/events", json=event(**changes)).status_code == 422
    assert db_session.query(ReadingPilotEvent).count() == 0


def test_old_queued_events_are_still_valid_and_source_wording_is_unchanged(client):
    old = get_parallel_library(1)
    new = get_parallel_library(2)
    assert client.post("/api/books/parallel/events", json=event(version=1)).json() == {"status": "recorded"}
    for before, after in zip(old["texts"], new["texts"]):
        assert before["id"] == after["id"]
        for p, q in zip(before["paragraphs"], after["paragraphs"]):
            for key, value in p.items(): assert q[key] == value
            assert " ".join(t["surface"] for t in q["tokens"]) == q["ar"]
            assert all(t["gloss"] for t in q["tokens"] if any("\u0621" <= c <= "\u064a" for c in t["surface"]))


def test_reread_lookup_retains_exact_paragraph_and_is_scheduling_inert(client, db_session):
    assert client.post("/api/books/parallel/events", json=event(paragraph_id="3", kind="word", token_id="3-1", reread=True)).status_code == 200
    assert db_session.query(ReadingPilotEvent).one().payload_json["paragraph_id"] == "3"
    for model in (ReviewLog, SentenceReviewLog, UserLemmaKnowledge, Lemma): assert db_session.query(model).count() == 0


def test_preupgrade_stored_event_is_still_an_idempotent_retry(client, db_session):
    from app.services.parallel_reading import ParallelEventIn
    payload = event(version=1) | {"clue_id": None, "visible": None, "effort": None}
    assert ParallelEventIn(**event(version=1)).model_dump(mode="json") == payload
    # Exact serialized shape from the old deployed server.
    db_session.add(ReadingPilotEvent(client_event_id=payload["client_event_id"], payload_json=payload))
    db_session.commit()
    assert client.post("/api/books/parallel/events", json=event(version=1)).json() == {"status": "duplicate"}
    assert db_session.query(ReadingPilotEvent).count() == 1
