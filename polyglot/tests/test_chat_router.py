from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import ChatMessage
from app.routers import chat


def _client(tmp_db):
    def _override():
        db = tmp_db()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app), tmp_db


def test_chat_ask_persists_messages(tmp_db, monkeypatch):
    monkeypatch.setattr(chat, "_call_claude", lambda prompt: "Use kosmos as 'world'.")
    client, factory = _client(tmp_db)
    try:
        r = client.post("/api/chat/ask", json={
            "question": "Explain this sentence",
            "context": "Sentence: kosmos estin megas.",
            "screen": "polyglot-review",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["answer"] == "Use kosmos as 'world'."
        assert body["conversation_id"]

        with factory() as db:
            messages = db.query(ChatMessage).order_by(ChatMessage.id.asc()).all()
            assert [m.role for m in messages] == ["user", "assistant"]
            assert messages[0].screen == "polyglot-review"
            assert messages[0].context_summary == "Sentence: kosmos estin megas."
    finally:
        app.dependency_overrides.clear()
