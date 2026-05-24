"""AI chat (Ask-AI tutor) endpoint for Polyglot review context.

All LLM work routes through ``llm_cli`` so the tutor honours the active provider
(Claude or Codex) and fails over on quota exhaustion, exactly like the
structured pipelines.
"""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ChatMessage
from app.services import llm_cli

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

CHAT_MODEL = os.environ.get("POLYGLOT_CHAT_MODEL", "haiku")
CHAT_TIMEOUT_S = int(os.environ.get("POLYGLOT_CHAT_TIMEOUT", "60"))

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


def _call_tutor(prompt: str) -> str:
    answer = llm_cli.call_text(
        prompt=prompt,
        model=CHAT_MODEL,
        timeout_s=CHAT_TIMEOUT_S,
        log_context="polyglot.chat",
        system_prompt=CHAT_SYSTEM_PROMPT,
    )
    if answer is None:
        raise HTTPException(502, "AI request failed")
    return answer


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

    answer = _call_tutor(prompt)

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
