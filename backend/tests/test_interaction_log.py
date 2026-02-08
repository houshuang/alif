import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.interaction_logger import log_interaction


@pytest.fixture(autouse=True)
def _allow_logging():
    """Temporarily clear TESTING so these tests can exercise the logger."""
    old = os.environ.pop("TESTING", None)
    yield
    if old is not None:
        os.environ["TESTING"] = old


def test_log_interaction(tmp_path):
    with patch("app.services.interaction_logger.settings") as mock_settings:
        mock_settings.log_dir = tmp_path
        log_interaction(
            event="review",
            lemma_id=42,
            rating=3,
            response_ms=2100,
            context="sentence_id:17",
            session_id="abc123",
        )

    log_files = list(tmp_path.glob("interactions_*.jsonl"))
    assert len(log_files) == 1

    with open(log_files[0]) as f:
        lines = f.readlines()
    assert len(lines) == 1

    entry = json.loads(lines[0])
    assert entry["event"] == "review"
    assert entry["lemma_id"] == 42
    assert entry["rating"] == 3
    assert entry["response_ms"] == 2100
    assert entry["session_id"] == "abc123"
    assert "ts" in entry


def test_log_multiple_interactions(tmp_path):
    with patch("app.services.interaction_logger.settings") as mock_settings:
        mock_settings.log_dir = tmp_path
        log_interaction(event="review", lemma_id=1, rating=3)
        log_interaction(event="review", lemma_id=2, rating=1)
        log_interaction(event="word_viewed", lemma_id=3)

    log_files = list(tmp_path.glob("interactions_*.jsonl"))
    assert len(log_files) == 1

    with open(log_files[0]) as f:
        lines = f.readlines()
    assert len(lines) == 3


def test_log_omits_none_fields(tmp_path):
    with patch("app.services.interaction_logger.settings") as mock_settings:
        mock_settings.log_dir = tmp_path
        log_interaction(event="session_start")

    log_files = list(tmp_path.glob("interactions_*.jsonl"))
    with open(log_files[0]) as f:
        entry = json.loads(f.readline())

    assert entry["event"] == "session_start"
    assert "lemma_id" not in entry
    assert "rating" not in entry
