"""AI chat endpoints for asking questions about Arabic learning."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ChatMessage
from app.schemas import (
    AskQuestionIn,
    AskQuestionOut,
    ChatMessageOut,
    ConversationDetail,
    ConversationSummary,
)
from app.services.interaction_logger import log_interaction
from app.services.llm import generate_completion

router = APIRouter(prefix="/api/chat", tags=["chat"])

CHAT_SYSTEM_PROMPT = (
    "You are a helpful Arabic language tutor. The learner is using an Arabic reading "
    "and listening training app. Answer questions concisely. When using Arabic examples, "
    "always include full diacritics (tashkeel). Use ALA-LC transliteration when helpful "
    "(e.g. kitāb, madrasa). The learner's current screen context is provided — use it "
    "to give relevant answers."
)


@router.post("/ask", response_model=AskQuestionOut)
def ask_question(body: AskQuestionIn, db: Session = Depends(get_db)):
    conversation_id = body.conversation_id or uuid.uuid4().hex

    # Load previous messages for this conversation
    previous = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    # Build prompt with conversation history
    parts: list[str] = []

    if body.context:
        parts.append(f"[Screen context: {body.screen}]\n{body.context}")

    for msg in previous:
        prefix = "User" if msg.role == "user" else "Assistant"
        parts.append(f"{prefix}: {msg.content}")

    parts.append(f"User: {body.question}")
    prompt = "\n\n".join(parts)

    result = generate_completion(
        prompt=prompt,
        system_prompt=CHAT_SYSTEM_PROMPT,
        json_mode=False,
        temperature=0.7,
        task_type="chat",
    )
    answer = result["content"]

    # Store user message
    user_msg = ChatMessage(
        conversation_id=conversation_id,
        screen=body.screen,
        role="user",
        content=body.question,
        context_summary=body.context if body.context else None,
    )
    db.add(user_msg)

    # Store assistant response
    assistant_msg = ChatMessage(
        conversation_id=conversation_id,
        screen=body.screen,
        role="assistant",
        content=answer,
    )
    db.add(assistant_msg)
    db.commit()

    log_interaction(
        event="ai_ask",
        screen=body.screen,
        conversation_id=conversation_id,
        question=body.question[:200],
        context=body.context[:200] if body.context else None,
    )

    return AskQuestionOut(answer=answer, conversation_id=conversation_id)


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(limit: int = 50, db: Session = Depends(get_db)):
    # Subquery: latest created_at per conversation
    sub = (
        db.query(
            ChatMessage.conversation_id,
            func.max(ChatMessage.created_at).label("last_at"),
            func.count(ChatMessage.id).label("msg_count"),
        )
        .group_by(ChatMessage.conversation_id)
        .subquery()
    )

    # Get the first user message per conversation for preview + screen
    first_msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    # Build lookup: conversation_id -> first user message
    first_by_conv: dict[str, ChatMessage] = {}
    for msg in first_msgs:
        if msg.conversation_id not in first_by_conv:
            first_by_conv[msg.conversation_id] = msg

    # Get conversations ordered by most recent
    rows = (
        db.query(sub.c.conversation_id, sub.c.last_at, sub.c.msg_count)
        .order_by(sub.c.last_at.desc())
        .limit(limit)
        .all()
    )

    results = []
    for row in rows:
        conv_id = row.conversation_id
        first = first_by_conv.get(conv_id)
        results.append(
            ConversationSummary(
                conversation_id=conv_id,
                screen=first.screen or "" if first else "",
                preview=first.content[:100] if first else "",
                created_at=first.created_at if first else row.last_at,
                message_count=row.msg_count,
            )
        )

    return results


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)):
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    if not messages:
        raise HTTPException(status_code=404, detail="Conversation not found")

    first_user = next((m for m in messages if m.role == "user"), None)

    return ConversationDetail(
        conversation_id=conversation_id,
        screen=first_user.screen or "" if first_user else "",
        context_summary=first_user.context_summary if first_user else None,
        messages=[
            ChatMessageOut(
                role=m.role,
                content=m.content,
                created_at=m.created_at,
            )
            for m in messages
        ],
    )
