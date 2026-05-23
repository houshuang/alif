import json

from app.services import interaction_logger


def test_testing_zero_does_not_disable_interaction_logging(tmp_path, monkeypatch):
    monkeypatch.setattr(interaction_logger.settings, "log_dir", tmp_path)
    monkeypatch.setenv("TESTING", "0")

    interaction_logger.log_interaction(
        event="sentence_review",
        app="polyglot",
        lemma_id=123,
        sentence_id=456,
    )

    files = list(tmp_path.glob("interactions_*.jsonl"))
    assert len(files) == 1
    row = json.loads(files[0].read_text().strip())
    assert row["event"] == "sentence_review"
    assert row["app"] == "polyglot"
    assert row["lemma_id"] == 123
    assert row["sentence_id"] == 456


def test_testing_one_disables_interaction_logging(tmp_path, monkeypatch):
    monkeypatch.setattr(interaction_logger.settings, "log_dir", tmp_path)
    monkeypatch.setenv("TESTING", "1")

    interaction_logger.log_interaction(event="sentence_review", app="polyglot")

    assert list(tmp_path.glob("interactions_*.jsonl")) == []
