"""Versioned, supported reading. Deliberately independent of word scheduling."""

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.models import ReadingPilotEvent


@lru_cache(maxsize=1)
def get_reading_pilot() -> dict:
    return json.loads(
        (Path(__file__).resolve().parents[1] / "data/reading_pilot_momo_v1.json").read_text()
    )


class ReadingPilotEventIn(BaseModel):
    client_event_id: str = Field(min_length=1, max_length=100)
    attempt_id: str = Field(min_length=1, max_length=80)
    pilot_id: Literal["momo-wings"]
    version: Literal[1]
    session_id: str
    occurred_at: datetime
    kind: Literal["open", "translation", "phrase_help", "help", "simpler", "reread", "feedback", "complete", "pause"]
    phase: Literal["read", "reread"]
    reading_ms: int = Field(ge=0, le=86_400_000)
    rereading_ms: int = Field(ge=0, le=86_400_000)
    english_visible: bool = False
    simpler_visible: bool = False
    phrase_help_visible: bool = False
    open_help_ids: list[str] = Field(default_factory=list, max_length=30)
    help_id: str | None = None
    visible: bool | None = None
    followed: Literal["yes", "partly", "no"] | None = None
    wanted_more: Literal["yes", "maybe", "no"] | None = None

    @model_validator(mode="after")
    def validate_content(self):
        session = next((s for s in get_reading_pilot()["sessions"] if s["id"] == self.session_id), None)
        if session is None:
            raise ValueError("Unknown reading session")
        help_ids = {h["id"] for b in session["blocks"] for h in b["help"]}
        if not set(self.open_help_ids).issubset(help_ids):
            raise ValueError("Unknown open phrase help")
        if self.kind == "help":
            if self.help_id not in help_ids:
                raise ValueError("Unknown phrase help")
        elif self.help_id is not None:
            raise ValueError("Phrase identity is only valid for help events")
        if self.occurred_at.tzinfo is None:
            raise ValueError("Client time must include a timezone")
        return self


def record_reading_event(db: Session, event: ReadingPilotEventIn) -> dict:
    """Retry-safe event journal. No lemma, acquisition, review, or exposure credit writes.

    Source ranges and support are recoverable through the immutable content version.
    Cumulative foreground times are estimates, never reading-speed/recognition scores.
    """
    payload = event.model_dump(mode="json")
    result = db.execute(
        insert(ReadingPilotEvent)
        .values(client_event_id=event.client_event_id, payload_json=payload)
        .on_conflict_do_nothing(index_elements=["client_event_id"])
    )
    db.commit()
    return {"status": "recorded" if result.rowcount else "duplicate"}
