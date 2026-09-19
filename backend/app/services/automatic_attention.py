"""Automatic reading priorities; never reset memory or fabricate review credit.

Frequency is a coarse prior, not a fiction-readiness score. Recent authentic
reading can override it. Generated practice supplies cost evidence only.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from app.models import (FrequencyCoreEntry, Lemma, ReadingPilotEvent, ReviewLog,
                        Sentence, SentenceReviewLog, SentenceWord, Story, StoryWord,
                        UserLemmaKnowledge)
from app.services.canonical_resolution import resolve_canonical_via_map
from app.services.sentence_validator import is_function_word_lemma
from app.services.sentence_eligibility import MAPPING_VERIFICATION_MIN_AT

POLICY_VERSION = "automatic_attention_v2"
BROAD_RANK = 5000
READING_DAYS = 30
COST_REVIEWS = 8
MIN_COST_REVIEWS = 6
MIN_FAILURES = 3
READING_REASON = f"{POLICY_VERSION}:reading_recurrence"


def modern_frequency_ranks(db: Session) -> dict[int, int]:
    """Positive evidence only; Quran/fused rank cannot confer modern priority."""
    ranks = {lid: rank for lid, rank in db.query(Lemma.lemma_id, Lemma.frequency_rank)
             if rank is not None and rank > 0}
    for row in db.query(FrequencyCoreEntry).filter(FrequencyCoreEntry.excluded_reason.is_(None)):
        if row.lemma_id is None:
            continue
        values = [v for v in (ranks.get(row.lemma_id), row.camel_rank,
                  row.news_rank, row.buckwalter_rank, row.artenten_rank, row.kelly_rank)
                  if v is not None and v > 0]
        if values:
            ranks[row.lemma_id] = min(values)
    return ranks


def _recent(value, cutoff, now):
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        return cutoff <= dt <= now
    except (ValueError, TypeError):
        return False


def reading_contexts(db: Session, lemmas: dict[int, Lemma], now: datetime) -> dict[int, set]:
    """Independent text units, never imported inventory or generated repetition.

    Supported-chapter lookups use unique exact vocalized citation identity only;
    uncertain/inflected tokens stay unresolved. Book receipts retain validated
    token positions. Review sentences require the existing linguistic QA gates.
    """
    cutoff = now - timedelta(days=READING_DAYS)
    parents = {lid: lemma.canonical_lemma_id for lid, lemma in lemmas.items()}
    contexts = defaultdict(set)

    def add(lid, key):
        if lid in lemmas:
            canonical = resolve_canonical_via_map(lid, parents)
            if canonical in lemmas:
                contexts[canonical].add(key)

    rows = db.query(SentenceWord.lemma_id, Sentence.id).join(
        Sentence, Sentence.id == SentenceWord.sentence_id
    ).join(SentenceReviewLog, SentenceReviewLog.sentence_id == Sentence.id).filter(
        Sentence.source.in_(["book", "corpus"]),
        SentenceReviewLog.review_mode == "reading",
        SentenceReviewLog.reviewed_at >= cutoff, SentenceReviewLog.reviewed_at <= now,
        Sentence.mappings_verified_at >= MAPPING_VERIFICATION_MIN_AT,
        Sentence.quality_natural.is_(True), Sentence.quality_translation_correct.is_(True),
    ).distinct()
    for lid, sid in rows:
        add(lid, ("sentence", sid))

    # Completed exact book-reader ranges count by sentence, so replaying or
    # splitting the same range cannot manufacture independent contexts.
    for story in db.query(Story).options(load_only(Story.id, Story.metadata_json)).filter(
        Story.source.in_(["imported", "book_ocr"])
    ):
        metadata = story.metadata_json if isinstance(story.metadata_json, dict) else {}
        receipts = (metadata.get("book_reader") or {}).get("passages") or {}
        positions = set()
        for receipt in receipts.values():
            if isinstance(receipt, dict) and _recent(receipt.get("completed_at"), cutoff, now):
                positions.update(receipt.get("passage_token_positions") or [])
        if not positions:
            continue
        for word in db.query(StoryWord).filter(StoryWord.story_id == story.id,
                                              StoryWord.position.in_(positions)):
            if not word.is_function_word and not word.name_type:
                add(word.lemma_id, ("book", story.id, word.sentence_index))

    from app.services.reading_chapters import get_reading_chapters
    tokens = {(c["id"], p["id"], t["id"]): t["surface"]
              for c in get_reading_chapters()["chapters"]
              for p in c["paragraphs"] for t in p["tokens"]}
    citations = defaultdict(set)
    for lid, lemma in lemmas.items():
        # Inventory-wide uniqueness: an ungated collision also blocks identity.
        citations[unicodedata.normalize("NFC", lemma.lemma_ar)].add(lid)
    for (payload,) in db.query(ReadingPilotEvent.payload_json).filter(
        ReadingPilotEvent.received_at >= cutoff, ReadingPilotEvent.received_at <= now
    ):
        if not isinstance(payload, dict) or payload.get("reader_id") != "library-bridge" or payload.get("kind") != "word":
            continue
        if not _recent(payload.get("occurred_at"), cutoff, now):
            continue
        key = (payload.get("chapter_id"), payload.get("paragraph_id"), payload.get("token_id"))
        surface = tokens.get(key)
        matches = citations.get(unicodedata.normalize("NFC", surface)) if surface else None
        if matches and len(matches) == 1:
            add(next(iter(matches)), ("chapter", key[0], key[1]))
    return contexts


def attention_plan(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    lemmas = {l.lemma_id: l for l in db.query(Lemma).options(load_only(
        Lemma.lemma_id, Lemma.lemma_ar, Lemma.lemma_ar_bare, Lemma.canonical_lemma_id,
        Lemma.word_category, Lemma.function_word_override, Lemma.gates_completed_at))}
    knowledge = {k.lemma_id: k for k in db.query(UserLemmaKnowledge).options(load_only(
        UserLemmaKnowledge.id, UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state,
        UserLemmaKnowledge.attention_disposition, UserLemmaKnowledge.attention_reason))}
    ranks = modern_frequency_ranks(db)
    contexts = reading_contexts(db, lemmas, now)
    # Hindawi is selected inventory, not independent coverage. Use it only as
    # positive protection for existing vocabulary, never to bulk-enroll imports.
    fiction_protected = {lid for lid, rank in db.query(
        FrequencyCoreEntry.lemma_id, FrequencyCoreEntry.hindawi_rank
    ).filter(FrequencyCoreEntry.excluded_reason.is_(None)) if rank and 0 < rank <= BROAD_RANK}
    recent = select(ReviewLog.lemma_id, ReviewLog.rating, func.row_number().over(
        partition_by=ReviewLog.lemma_id,
        order_by=(ReviewLog.reviewed_at.desc(), ReviewLog.id.desc()),
    ).label("n")).where(ReviewLog.reviewed_at <= now).subquery()
    cost = defaultdict(list)
    for lid, rating in db.execute(select(recent.c.lemma_id, recent.c.rating).where(recent.c.n <= COST_REVIEWS)):
        cost[lid].append(rating)

    changes = []
    reasons = Counter()
    for lid, lemma in lemmas.items():
        k = knowledge.get(lid)
        if lemma.canonical_lemma_id is not None or lemma.word_category in {"proper_name", "onomatopoeia"} or is_function_word_lemma(lemma.lemma_ar_bare, lemma.function_word_override):
            continue
        if k is not None and k.attention_disposition == "parked":
            reasons["identity_qa_hold"] += 1
            continue
        if lemma.gates_completed_at is None:
            continue
        repeated = len(contexts.get(lid, ())) >= 2
        broad = ranks.get(lid, BROAD_RANK + 1) <= BROAD_RANK
        introduced = k is not None and k.knowledge_state not in {"new", "encountered"}
        expensive = len(cost[lid]) >= MIN_COST_REVIEWS and sum(r <= 2 for r in cost[lid]) >= MIN_FAILURES
        if repeated:
            disposition, reason = "maintain", "reading_recurrence"
        elif broad:
            disposition, reason = "maintain", "broad_frequency"
        elif not introduced:
            disposition, reason = "reading_support", "await_reading_evidence"
        elif lid in fiction_protected:
            disposition, reason = "maintain", "fiction_evidence"
        elif lid not in ranks:
            disposition, reason = "maintain", "unknown_frequency"
        elif expensive:
            disposition, reason = "reading_support", "costly_lower_priority"
        else:
            disposition, reason = "maintain", "established_low_cost"
        reasons[reason] += 1
        # Do not turn an untouched dictionary into a learner inventory. A
        # repeated reading need may stage one existing, validated lexical row.
        if k is None and not repeated:
            continue
        full_reason = f"{POLICY_VERSION}:{reason}"
        if k is None or k.attention_disposition != disposition or k.attention_reason != full_reason:
            changes.append({"lemma_id":lid, "before":k.attention_disposition if k else None,
                            "before_reason":k.attention_reason if k else None,
                            "after":disposition, "reason":full_reason,
                            "modern_rank":ranks.get(lid), "reading_contexts":len(contexts.get(lid, ())),
                            "recent_reviews":len(cost[lid]), "recent_failures":sum(r <= 2 for r in cost[lid])})
    return {"policy_version":POLICY_VERSION, "evaluated_at":now.isoformat(),
            "reasons":dict(reasons), "changes":changes}


def refresh_attention(db: Session, *, apply: bool = True, now: datetime | None = None) -> dict:
    """Single short DB-only pass. No acquisition, LLMs or memory-field writes."""
    plan = attention_plan(db, now)
    if apply and plan["changes"]:
        rows = {k.lemma_id:k for k in db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id.in_([c["lemma_id"] for c in plan["changes"]]))}
        updated_at = now or datetime.now(timezone.utc)
        for change in plan["changes"]:
            k = rows.get(change["lemma_id"])
            if k is None:
                k = UserLemmaKnowledge(lemma_id=change["lemma_id"], knowledge_state="encountered", source="book")
                db.add(k)
            k.attention_disposition = change["after"]
            k.attention_reason = change["reason"]
            k.attention_updated_at = updated_at
        from app.services.activity_log import log_activity
        log_activity(db, "automatic_attention", "Recomputed automatic reading priorities", plan)
    return plan
