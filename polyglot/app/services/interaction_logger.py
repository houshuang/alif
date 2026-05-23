"""Append-only JSONL event log.

Each event becomes one line in `polyglot/data/logs/interactions_YYYY-MM-DD.jsonl`
(or whatever `settings.log_dir` resolves to). Mirrors Alif's schema so a
future shared analyzer can ingest both feeds.

Disabled when `TESTING=1` is set so unit tests don't pollute the dev log.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)
_TESTING_TRUE_VALUES = {"1", "true", "yes", "on"}


def _testing_enabled() -> bool:
    return os.environ.get("TESTING", "").strip().lower() in _TESTING_TRUE_VALUES


def _get_log_path() -> Path:
    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return log_dir / f"interactions_{today}.jsonl"


def log_interaction(
    event: str,
    lemma_id: int | None = None,
    rating: int | None = None,
    response_ms: int | None = None,
    context: str | None = None,
    session_id: str | None = None,
    **extra,
) -> None:
    if _testing_enabled():
        return
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "lemma_id": lemma_id,
        "rating": rating,
        "response_ms": response_ms,
        "context": context,
        "session_id": session_id,
        **extra,
    }
    entry = {k: v for k, v in entry.items() if v is not None}

    try:
        log_path = _get_log_path()
        with open(log_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("Failed to append interaction log event=%s", event)
