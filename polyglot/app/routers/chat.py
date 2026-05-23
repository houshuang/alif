"""AI chat endpoint for Polyglot review context."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ChatMessage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

_MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku": "claude-haiku-4-5-20251001",
}


def _resolve_model(raw: str) -> str:
    return _MODEL_ALIASES.get(raw.strip().lower(), raw)


CHAT_MODEL = _resolve_model(os.environ.get("POLYGLOT_CHAT_MODEL", "haiku"))
CHAT_TIMEOUT_S = int(os.environ.get("POLYGLOT_CHAT_TIMEOUT", "30"))

CHAT_SYSTEM_PROMPT = (
    "You are a concise language tutor for Polyglot, a reading-comprehension app "
    "for Modern Greek, Ancient Greek, and Latin. Use the learner's current "
    "screen context. Explain how the sentence works, prioritize meaning and "
    "recognition, and avoid mechanical word-by-word gloss lists unless the user "
    "asks for them. Use Greek or Latin script accurately, and include English "
    "translations for examples."
)


class AskQuestionIn(BaseModel):
    question: str
    context: str = ""
    screen: str = ""
    conversation_id: str | None = None


class AskQuestionOut(BaseModel):
    answer: str
    conversation_id: str


def _call_claude(prompt: str) -> str:
    if not shutil.which("claude"):
        raise HTTPException(503, "claude CLI not found")

    cmd = [
        "claude", "-p",
        "--tools", "",
        "--output-format", "json",
        "--model", CHAT_MODEL,
        "--no-session-persistence",
        "--system-prompt", CHAT_SYSTEM_PROMPT,
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CHAT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "AI request timed out") from exc

    if proc.returncode != 0:
        logger.warning("polyglot chat failed (%s): %s", proc.returncode, proc.stderr[:500])
        raise HTTPException(502, "AI request failed")

    text = proc.stdout.strip()
    try:
        wrapper = json.loads(text)
        if isinstance(wrapper, dict) and isinstance(wrapper.get("result"), str):
            return wrapper["result"].strip()
    except json.JSONDecodeError:
        pass
    return text


@router.post("/ask", response_model=AskQuestionOut)
def ask_question(body: AskQuestionIn, db: Session = Depends(get_db)):
    conversation_id = body.conversation_id or uuid.uuid4().hex

    previous = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    parts: list[str] = []
    if body.context:
        parts.append(f"[Screen context: {body.screen}]\n{body.context}")
    for msg in previous:
        prefix = "User" if msg.role == "user" else "Assistant"
        parts.append(f"{prefix}: {msg.content}")
    parts.append(f"User: {body.question}")
    prompt = "\n\n".join(parts)

    answer = _call_claude(prompt)

    try:
        db.add(ChatMessage(
            conversation_id=conversation_id,
            screen=body.screen,
            role="user",
            content=body.question,
            context_summary=body.context if body.context else None,
        ))
        db.add(ChatMessage(
            conversation_id=conversation_id,
            screen=body.screen,
            role="assistant",
            content=answer,
        ))
        db.commit()
    except Exception:
        logger.warning("polyglot chat: failed to persist messages", exc_info=True)
        db.rollback()

    return AskQuestionOut(answer=answer, conversation_id=conversation_id)
