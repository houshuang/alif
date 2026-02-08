import json
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
