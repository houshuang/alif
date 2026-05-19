"""Append-only JSONL event log.

Each event becomes one line in `polyglot/data/logs/interactions_YYYY-MM-DD.jsonl`
(or whatever `settings.log_dir` resolves to). Mirrors Alif's schema so a
future shared analyzer can ingest both feeds.

Disabled when `TESTING=1` is set so unit tests don't pollute the dev log.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings


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
    if os.environ.get("TESTING"):
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

    log_path = _get_log_path()
    with open(log_path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
