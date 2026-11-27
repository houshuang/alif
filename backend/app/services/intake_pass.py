"""Automatic word introduction driven by real reviews.

Speculative (``prefetch=true``) session builds must not change learning state,
and the phone reads its sessions from the prefetched queue, so a build that is
allowed to introduce words almost never runs. This pass does that stateful work
once per reviewed session instead: refresh automatic attention, introduce words
within the session build's own gates and budgets, then generate their sentences.
"""
from __future__ import annotations

import logging
import threading

from app.database import SessionLocal

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_session_id: str | None = None


def _claim(session_id: str) -> bool:
    global _last_session_id
    with _lock:
        if session_id == _last_session_id:
            return False
        _last_session_id = session_id
        return True


def run_intake_pass(session_id: str | None) -> list[int]:
    """Background task. Runs once per session id; returns introduced lemma ids."""
    if not session_id or not _claim(session_id):
        return []
    from app.services.automatic_attention import refresh_attention
    from app.services.sentence_selector import introduce_reserved_words

    db = SessionLocal()
    try:
        refresh_attention(db)
        db.commit()
        introduced = introduce_reserved_words(db)
        db.commit()
    except Exception:
        logger.exception("intake pass failed for session %s", session_id)
        db.rollback()
        return []
    finally:
        db.close()
    if introduced:
        logger.info("intake pass introduced %s after session %s", introduced, session_id)
        from app.services.material_generator import warm_sentence_cache
        warm_sentence_cache()
        _write_word_cards(introduced)
    return introduced


def _write_word_cards(lemma_ids: list[int]) -> None:
    """The intro card shows the word card: write it before the next session."""
    from app.services.word_card import enrich_word_cards_for

    db = SessionLocal()
    try:
        enrich_word_cards_for(db, lemma_ids)
    except Exception:
        logger.exception("word cards failed for introduced words %s", lemma_ids)
        db.rollback()
    finally:
        db.close()
