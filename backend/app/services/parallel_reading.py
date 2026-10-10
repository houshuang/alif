"""Experiment journal only; never writes vocabulary/review state."""
import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session
from app.services.reading_chapters import record_reading_journal_event
from app.services.interaction_logger import log_interaction

@lru_cache(maxsize=2)
def get_parallel_library(version: int = 2) -> dict:
    return json.loads((Path(__file__).parent.parent / f"data/parallel_reading_v{version}.json").read_text())

class ParallelEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_event_id: str = Field(min_length=1, max_length=160)
    reader_id: Literal["parallel-reading"]
    version: Literal[1, 2]
    text_id: str
    paragraph_id: str
    occurred_at: datetime
    kind: Literal["open", "leave", "library", "select", "passage", "support", "reveal", "all", "vowels", "size", "clue", "word", "display", "about", "reread", "complete", "reflection"]
    support: Literal["grc", "la", "ru", "en"]
    revealed: bool
    all: bool
    vowels: bool
    size: float = Field(ge=.85, le=1.4)
    reread: bool
    completed: bool
    panel: Literal["appearance", "phrases"] | None = None
    token_id: str | None = None
    clue_id: str | None = None
    visible: bool | None = None
    effort: Literal["smooth", "some-work", "tiring"] | None = None

    def model_dump(self, *args, **kwargs):
        payload = super().model_dump(*args, **kwargs)
        if self.version == 1:
            # A delayed v1 retry must compare equal to the payload stored by
            # the original server, before token/panel fields existed.
            payload.pop("token_id", None)
            payload.pop("panel", None)
        return payload

    @model_validator(mode="after")
    def validate_content(self):
        text = next((t for t in get_parallel_library(self.version)["texts"] if t["id"] == self.text_id), None)
        paragraph = next((p for p in text["paragraphs"] if p["id"] == self.paragraph_id), None) if text else None
        if paragraph is None: raise ValueError("Unknown parallel text or paragraph")
        if self.version == 1 and (self.support == "en" or self.kind in {"word", "display"}):
            raise ValueError("This action needs edition 2")
        if self.kind == "display":
            if self.panel is None: raise ValueError("Display action needs a panel")
        elif self.panel is not None: raise ValueError("Panel identity belongs to display controls")
        if self.kind == "word":
            token = next((t for t in paragraph.get("tokens", []) if t["id"] == self.token_id), None)
            if token is None or not token["gloss"]: raise ValueError("Unknown Arabic word in paragraph")
        elif self.token_id is not None: raise ValueError("Token identity belongs to word help")
        if self.kind == "clue":
            if self.clue_id not in {c["id"] for c in paragraph["clues"]}: raise ValueError("Unknown clue")
        elif self.clue_id is not None: raise ValueError("Clue identity belongs to clue actions")
        if self.kind == "reflection":
            if self.effort is None: raise ValueError("Reflection needs effort")
        elif self.effort is not None: raise ValueError("Effort belongs to reflection")
        if self.occurred_at.tzinfo is None: raise ValueError("Client time needs a timezone")
        return self

def record_parallel_event(db: Session, event: ParallelEventIn) -> dict:
    # Existing idempotent journal writes ReadingPilotEvent only.
    result = record_reading_journal_event(db, event)
    if result["status"] == "recorded":
        log_interaction(event="parallel_reading", **event.model_dump(mode="json"))
    return result
