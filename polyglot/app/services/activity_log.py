"""Thin helper that writes service-level events to the `ActivityLog` table.

Distinct from `interaction_logger` (per-event JSONL files): activity_log is
where batch jobs, cron scripts, and bulk-action endpoints record what they
did. The UI Activity panel reads from this table.
"""
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import ActivityLog

log = logging.getLogger(__name__)


def log_activity(
    db: Session,
    *,
    event_type: str,
    summary: str,
    detail: dict[str, Any] | None = None,
    language_code: str | None = None,
) -> ActivityLog:
    """Write an ActivityLog row. Commits its own transaction.

    Safe to call from background tasks or routes; failure to log is
    swallowed because activity logging should never break a request.
    """
    try:
        entry = ActivityLog(
            event_type=event_type,
            language_code=language_code,
            summary=summary,
            detail_json=detail or {},
        )
        db.add(entry)
        db.commit()
        return entry
    except Exception:
        log.exception("Failed to write ActivityLog entry (event_type=%s)", event_type)
        db.rollback()
        raise
