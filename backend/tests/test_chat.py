"""Tests for AI chat endpoints."""

from unittest.mock import patch

from app.models import ChatMessage


class TestAskQuestion:
    def test_ask_creates_messages_and_returns_answer(self, client, db_session):
        with patch("app.routers.chat.generate_completion") as mock_llm:
            mock_llm.return_value = {"content": "The word kitāb means book."}

            resp = client.post("/api/chat/ask", json={
                "question": "What does kitab mean?",
                "context": "Reviewing word: كتاب (book)",
                "screen": "review",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "The word kitāb means book."
        assert "conversation_id" in data

        # Check DB has both messages
        msgs = db_session.query(ChatMessage).filter(
            ChatMessage.conversation_id == data["conversation_id"]
        ).order_by(ChatMessage.created_at.asc()).all()
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "What does kitab mean?"
        assert msgs[0].context_summary == "Reviewing word: كتاب (book)"
        assert msgs[1].role == "assistant"
        assert msgs[1].content == "The word kitāb means book."

    def test_conversation_id_is_returned_and_reusable(self, client, db_session):
        with patch("app.routers.chat.generate_completion") as mock_llm:
            mock_llm.return_value = {"content": "Answer 1"}

            resp1 = client.post("/api/chat/ask", json={
                "question": "First question",
                "screen": "learn",
            })

        conv_id = resp1.json()["conversation_id"]

        with patch("app.routers.chat.generate_completion") as mock_llm:
            mock_llm.return_value = {"content": "Answer 2"}

            resp2 = client.post("/api/chat/ask", json={
                "question": "Follow-up question",
                "conversation_id": conv_id,
                "screen": "learn",
            })

        assert resp2.json()["conversation_id"] == conv_id

        msgs = db_session.query(ChatMessage).filter(
            ChatMessage.conversation_id == conv_id
        ).all()
        assert len(msgs) == 4  # 2 user + 2 assistant

    def test_follow_up_does_not_store_context_summary(self, client, db_session):
        with patch("app.routers.chat.generate_completion") as mock_llm:
            mock_llm.return_value = {"content": "Answer 1"}
            resp1 = client.post("/api/chat/ask", json={
                "question": "Q1",
                "context": "Some context",
                "screen": "review",
            })

        conv_id = resp1.json()["conversation_id"]

        with patch("app.routers.chat.generate_completion") as mock_llm:
            mock_llm.return_value = {"content": "Answer 2"}
            client.post("/api/chat/ask", json={
                "question": "Q2",
                "context": "New context",
                "conversation_id": conv_id,
                "screen": "review",
            })

        user_msgs = db_session.query(ChatMessage).filter(
            ChatMessage.conversation_id == conv_id,
            ChatMessage.role == "user",
        ).order_by(ChatMessage.created_at.asc()).all()

        assert user_msgs[0].context_summary == "Some context"
        assert user_msgs[1].context_summary is None

    def test_conversation_history_included_in_prompt(self, client, db_session):
        with patch("app.routers.chat.generate_completion") as mock_llm:
            mock_llm.return_value = {"content": "Answer 1"}
            resp1 = client.post("/api/chat/ask", json={
                "question": "What is a root?",
                "screen": "learn",
            })

        conv_id = resp1.json()["conversation_id"]

        with patch("app.routers.chat.generate_completion") as mock_llm:
            mock_llm.return_value = {"content": "Answer 2"}
            client.post("/api/chat/ask", json={
                "question": "Give me an example",
                "conversation_id": conv_id,
                "screen": "learn",
            })

            # Check that the prompt includes previous messages
            call_args = mock_llm.call_args
            prompt = call_args.kwargs.get("prompt", call_args[0][0] if call_args[0] else "")
            assert "What is a root?" in prompt
            assert "Answer 1" in prompt
            assert "Give me an example" in prompt


class TestListConversations:
    def test_returns_conversations(self, client, db_session):
        with patch("app.routers.chat.generate_completion") as mock_llm:
            mock_llm.return_value = {"content": "Reply 1"}
            client.post("/api/chat/ask", json={
                "question": "Question about grammar",
                "screen": "review",
            })

        with patch("app.routers.chat.generate_completion") as mock_llm:
            mock_llm.return_value = {"content": "Reply 2"}
            client.post("/api/chat/ask", json={
                "question": "Question about vocabulary",
                "screen": "words",
            })

        resp = client.get("/api/chat/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # Most recent first
        assert data[0]["preview"] == "Question about vocabulary"
        assert data[0]["message_count"] == 2
        assert data[1]["preview"] == "Question about grammar"

    def test_returns_empty_list_when_no_conversations(self, client):
        resp = client.get("/api/chat/conversations")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetConversation:
    def test_returns_messages_in_order(self, client, db_session):
        with patch("app.routers.chat.generate_completion") as mock_llm:
            mock_llm.return_value = {"content": "First answer"}
            resp = client.post("/api/chat/ask", json={
                "question": "First question",
                "context": "On review screen",
                "screen": "review",
            })

        conv_id = resp.json()["conversation_id"]

        with patch("app.routers.chat.generate_completion") as mock_llm:
            mock_llm.return_value = {"content": "Second answer"}
            client.post("/api/chat/ask", json={
                "question": "Second question",
                "conversation_id": conv_id,
                "screen": "review",
            })

        resp = client.get(f"/api/chat/conversations/{conv_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversation_id"] == conv_id
        assert data["screen"] == "review"
        assert data["context_summary"] == "On review screen"
        assert len(data["messages"]) == 4
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "First question"
        assert data["messages"][1]["role"] == "assistant"
        assert data["messages"][2]["role"] == "user"
        assert data["messages"][3]["role"] == "assistant"

    def test_404_for_unknown_conversation(self, client):
        resp = client.get("/api/chat/conversations/nonexistent-id")
        assert resp.status_code == 404
