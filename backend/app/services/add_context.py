"""The sentence a word was added from, and a later check of the entry against it.

Snap and pre-read add words the learner met in a real sentence. Recording that
sentence lets the entry be checked against how the word was actually used: the
running-text resolver can match a same-skeleton neighbour, and a stored gloss
can lack the sense on the page. The check runs after the add (background task
or ``scripts/verify_add_contexts.py``), never inside the request.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Lemma, WordAddContext

logger = logging.getLogger(__name__)

_MAX_SENTENCE_CHARS = 1500


def record_add_context(
    db: Session,
    lemma_id: int,
    source: str,
    sentence: str | None,
    surface: str | None = None,
    snap_id: int | None = None,
    preread_set_id: int | None = None,
) -> WordAddContext | None:
    """Stage a context row for an added word. Caller commits. No row without a
    sentence."""
    sentence = (sentence or "").strip()
    if not sentence:
        return None
    lemma = db.get(Lemma, lemma_id)
    row = WordAddContext(
        lemma_id=lemma_id,
        source=source,
        surface=(surface or "").strip() or None,
        sentence_ar=sentence[:_MAX_SENTENCE_CHARS],
        snap_id=snap_id,
        preread_set_id=preread_set_id,
        gloss_at_add=lemma.gloss_en if lemma is not None else None,
    )
    db.add(row)
    return row


def verify_add_contexts(context_ids: list[int]) -> dict[str, int]:
    """Check each unchecked context row's entry against its sentence.

    ``broader`` widens the gloss (compare-and-set, as pre-read does);
    ``different`` records the word actually used so it can be reviewed; the
    enrolled word is not changed automatically. Own sessions; no transaction is
    held across the LLM call. Returns verdict counts."""
    from app.database import SessionLocal
    from app.services.interaction_logger import log_interaction
    from app.services.lookup_sense import check_lookup_sense
    from app.services.preread_service import regloss

    counts: dict[str, int] = {}
    for cid in context_ids:
        db = SessionLocal()
        try:
            row = db.get(WordAddContext, cid)
            if row is None or row.checked_at is not None:
                continue
            lemma = db.get(Lemma, row.lemma_id)
            if lemma is None:
                continue
            if lemma.canonical_lemma_id:
                lemma = db.get(Lemma, lemma.canonical_lemma_id) or lemma
            lemma_id = lemma.lemma_id
            entry = {"lemma_ar": lemma.lemma_ar, "gloss_en": lemma.gloss_en, "pos": lemma.pos}
            sentence, surface = row.sentence_ar, row.surface or lemma.lemma_ar
            db.rollback()

            v = check_lookup_sense(sentence, surface, entry)
            if v is None:
                counts["failed"] = counts.get("failed", 0) + 1
                continue

            written = False
            if v["verdict"] == "broader":
                w = {"lemma_id": lemma_id, "regloss": {"from": entry["gloss_en"], "to": v["gloss_en"]}}
                regloss(db, [w])
                written = bool(w.get("reglossed"))
            row = db.get(WordAddContext, cid)
            row.check_verdict = v["verdict"]
            row.check_json = {**v, "checked_lemma_id": lemma_id, "gloss_from": entry["gloss_en"],
                              "written": written}
            row.checked_at = datetime.now(timezone.utc)
            db.commit()
            counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
            if v["verdict"] in ("broader", "different"):
                log_interaction(
                    event="word_add_context_check", context_id=cid, lemma_id=lemma_id,
                    lemma_ar=entry["lemma_ar"], gloss_from=entry["gloss_en"],
                    verdict=v["verdict"], gloss_to=v.get("gloss_en"),
                    proposed_lemma_ar=v.get("lemma_ar"), written=written,
                )
        except Exception:
            logger.exception("add-context check failed for %s", cid)
            db.rollback()
            counts["failed"] = counts.get("failed", 0) + 1
        finally:
            db.close()
    return counts
