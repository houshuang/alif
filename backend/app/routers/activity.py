from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActivityLog

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("")
def list_activity(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    entries = (
        db.query(ActivityLog)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "summary": e.summary,
            "detail_json": e.detail_json,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]
