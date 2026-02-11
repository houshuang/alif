"""Activity logging utility for batch jobs, background tasks, and manual operations."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import ActivityLog


def log_activity(
    db: Session,
    event_type: str,
    summary: str,
    detail: dict | None = None,
    commit: bool = True,
) -> ActivityLog:
    entry = ActivityLog(
        event_type=event_type,
        summary=summary,
        detail_json=detail,
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
