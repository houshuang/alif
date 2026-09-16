"""Supported chapters and optional reflections; no vocabulary/scheduler mutations."""

import base64
import binascii
import hashlib
import json
import os
import tempfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.models import ReadingPilotEvent

VOICE_DIR = BASE_DIR / "data" / "reading-voice"
MAX_AUDIO_BYTES = 1_500_000


@lru_cache(maxsize=1)
def get_reading_chapters() -> dict:
    return json.loads((Path(__file__).resolve().parents[1] / "data/reading_chapters_v1.json").read_text())


class ChapterEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_event_id: str = Field(pattern=r"^chapter:[a-zA-Z0-9:-]{1,90}$")
    attempt_id: str = Field(min_length=1, max_length=80)
    reader_id: Literal["library-bridge"]
    version: Literal[1]
    chapter_id: str
    occurred_at: datetime
    kind: Literal["open", "start", "word", "translation", "vowels", "preview", "pause", "complete", "feedback", "reread"]
    stage: Literal["preview", "reading", "finished"]
    vowels: bool
    paragraph_id: str | None = None
    token_id: str | None = None
    visible: bool | None = None
    effort: Literal["smooth", "some-work", "tiring"] | None = None
    text: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_content(self):
        chapter = next((c for c in get_reading_chapters()["chapters"] if c["id"] == self.chapter_id), None)
        if chapter is None:
            raise ValueError("Unknown chapter")
        paragraphs = {p["id"]: p for p in chapter["paragraphs"]}
        if self.paragraph_id is not None and self.paragraph_id not in paragraphs:
            raise ValueError("Unknown paragraph")
        if self.kind == "word":
            p = paragraphs.get(self.paragraph_id, {})
            if self.token_id not in {t["id"] for t in p.get("tokens", [])}:
                raise ValueError("Unknown token in paragraph")
        elif self.token_id is not None:
            raise ValueError("Token identity is only valid for a word lookup")
        if self.kind == "translation" and self.paragraph_id is None:
            raise ValueError("Translation needs a paragraph")
        if self.kind != "feedback" and (self.text is not None or self.effort is not None):
            raise ValueError("Reflection belongs to feedback")
        if self.occurred_at.tzinfo is None:
            raise ValueError("Client time must include a timezone")
        return self


def record_chapter_event(db: Session, event: ChapterEventIn, **extra) -> dict:
    payload = event.model_dump(mode="json") | extra
    existing = db.get(ReadingPilotEvent, event.client_event_id)
    if existing is not None:
        if existing.payload_json != payload:
            raise ValueError("Event ID already used for different feedback")
        return {"status": "duplicate"}
    result = db.execute(insert(ReadingPilotEvent).values(
        client_event_id=event.client_event_id, payload_json=payload,
    ).on_conflict_do_nothing(index_elements=["client_event_id"]))
    db.commit()
    return {"status": "recorded" if result.rowcount else "duplicate"}


class ChapterVoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: ChapterEventIn
    mime_type: Literal["audio/webm", "audio/mp4", "audio/ogg"]
    audio_base64: str = Field(min_length=1, max_length=2_000_000)
    duration_ms: int = Field(ge=1, le=65_000)

    @model_validator(mode="after")
    def feedback_only(self):
        if self.event.kind != "feedback":
            raise ValueError("Audio belongs to feedback")
        return self


def record_chapter_voice(db: Session, body: ChapterVoiceIn) -> dict:
    try:
        audio = base64.b64decode(body.audio_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid audio encoding") from exc
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise ValueError("Voice note is empty or too large")
    # These are browser/native containers, not arbitrary uploaded file paths.
    signatures = {"audio/webm": audio.startswith(b"\x1a\x45\xdf\xa3"),
                  "audio/mp4": len(audio) >= 12 and audio[4:8] == b"ftyp",
                  "audio/ogg": audio.startswith(b"OggS")}
    if not signatures[body.mime_type]:
        raise ValueError("Audio container does not match its type")
    digest = hashlib.sha256(audio).hexdigest()
    extension = {"audio/webm": "webm", "audio/mp4": "m4a", "audio/ogg": "ogg"}[body.mime_type]
    name = f"{digest}.{extension}"
    # Store the complete blob before its journal reference. Retrying after a
    # crash reuses the same content-addressed file; no partial audio is exposed.
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    target = VOICE_DIR / name
    if not target.exists():
        fd, temp = tempfile.mkstemp(dir=VOICE_DIR)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(audio)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
        finally:
            Path(temp).unlink(missing_ok=True)
    return record_chapter_event(db, body.event, voice_file=name,
                                voice_mime_type=body.mime_type, voice_duration_ms=body.duration_ms)
