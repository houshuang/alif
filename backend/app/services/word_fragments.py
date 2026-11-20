"""Short phrases as the same-day retry after a missed word (`fragment_retest_v1`).

A/B against the bare word card (experiment log 2026-11-20). Each miss of a word
is assigned one arm by hash. In the fragment arm the retry shows the word in a
two-to-three-word phrase instead of alone. The answer is submitted exactly like
a word card (``review_mode="quiz"``), so scheduling is identical in both arms
and only the presentation differs.

Fragments are not sentences. They never enter a session, carry no word-level
mappings and earn no sentence credit, so they do not go through
``generate_material_for_word``. They pass their own checks instead: every
token must resolve through the running-text resolver without a CAMeL guess,
the target must be present, every other content word must be one the learner
knows, and the language judge must accept the phrase and its translation.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    Lemma,
    ReviewLog,
    UserLemmaKnowledge,
    WordFragment,
    WordRetestAssignment,
)

logger = logging.getLogger(__name__)

FRAGMENT_RETEST_PROTOCOL = "fragment_retest_v1"
FRAGMENT_FOLLOWUP_PROTOCOL = "fragment_followup_v1"
PHRASE_ARM = "phrase"
SENTENCE_ARM = "sentence"
FRAGMENT_WANTED_LOOKBACK = timedelta(days=30)
FRAGMENT_FOLLOWUP_REPEAT_GUARD = timedelta(hours=6)
FRAGMENT_ARM = "fragment"
WORD_CARD_ARM = "word_card"
FRAGMENT_MIN_STOCK = 2            # unshown fragments to keep for a word in the fragment arm
FRAGMENT_CANDIDATES_PER_WORD = 4
FRAGMENT_MAX_WORDS_PER_CALL = 8
FRAGMENT_MIN_TOKENS = 2
FRAGMENT_MAX_TOKENS = 4
FRAGMENT_COMPANION_SAMPLE = 200
FRAGMENT_MISS_LOOKBACK = timedelta(hours=48)

_SCHEMA = {
    "type": "object",
    "properties": {
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_id": {"type": "integer"},
                    "fragments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "arabic": {"type": "string"},
                                "english": {"type": "string"},
                            },
                            "required": ["arabic", "english"],
                        },
                    },
                },
                "required": ["lemma_id", "fragments"],
            },
        },
    },
    "required": ["words"],
}

_SYSTEM_PROMPT = (
    "You write very short Modern Standard Arabic phrases for a learner who is "
    "re-checking one word they just missed. Each phrase is natural Arabic a "
    "native speaker would say or write, fully vocalized."
)


def fragment_retest_enabled() -> bool:
    return os.getenv("ALIF_FRAGMENT_RETEST", "1") != "0"


# The retry-card comparison (phrase against bare word card) was switched off on
# 2026-11-20, hours after launch, so that the follow-up trial is the only
# randomized factor and reads out sooner. Every same-day retry is a word card.
RETEST_AB_ACTIVE = False


def retest_arm(lemma_id: int, missed_at: datetime) -> str:
    """Stable arm for one miss of one word (word card while the trial is off)."""
    if not RETEST_AB_ACTIVE:
        return WORD_CARD_ARM
    key = f"{FRAGMENT_RETEST_PROTOCOL}:{lemma_id}:{missed_at.replace(tzinfo=None).isoformat()}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return FRAGMENT_ARM if digest[0] % 2 == 0 else WORD_CARD_ARM


def followup_arm(lemma_id: int, missed_at: datetime) -> str:
    """Stable follow-up arm for one miss of one word, independent of the retry arm."""
    key = f"{FRAGMENT_FOLLOWUP_PROTOCOL}:{lemma_id}:{missed_at.replace(tzinfo=None).isoformat()}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return PHRASE_ARM if digest[0] % 2 == 0 else SENTENCE_ARM


def fragments_wanted(db: Session, lemma_id: int, now: datetime) -> bool:
    """Whether this word's latest miss puts it in an arm that shows phrases."""
    recent = latest_sentence_miss(db, lemma_id, now)
    if recent is not None and retest_arm(lemma_id, recent.reviewed_at) == FRAGMENT_ARM:
        return True
    miss = latest_sentence_miss(db, lemma_id, now, lookback=FRAGMENT_WANTED_LOOKBACK)
    return miss is not None and followup_arm(lemma_id, miss.reviewed_at) == PHRASE_ARM


def pick_fragment(db: Session, lemma_id: int) -> WordFragment | None:
    return (
        db.query(WordFragment)
        .filter(WordFragment.lemma_id == lemma_id)
        .order_by(WordFragment.times_shown, WordFragment.id)
        .first()
    )


def fragment_payload(fragment: WordFragment) -> dict:
    return {
        "id": fragment.id,
        "arabic": fragment.arabic_text,
        "english": fragment.english_translation,
        "transliteration": fragment.transliteration,
        "target_surface": fragment.target_surface,
    }


def recent_followup_exists(db: Session, lemma_id: int, now: datetime) -> bool:
    """A phrase follow-up for this word was answered recently (resumed sessions)."""
    cutoff = (now - FRAGMENT_FOLLOWUP_REPEAT_GUARD).replace(tzinfo=None)
    return (
        db.query(ReviewLog.id)
        .filter(
            ReviewLog.lemma_id == lemma_id,
            ReviewLog.review_mode == "fragment",
            ReviewLog.reviewed_at >= cutoff,
        )
        .first()
        is not None
    )


def mark_fragment_shown(db: Session, fragment_id: int, now: datetime) -> None:
    fragment = db.query(WordFragment).filter(WordFragment.id == fragment_id).first()
    if fragment is not None:
        fragment.times_shown = (fragment.times_shown or 0) + 1
        fragment.last_shown_at = now.replace(tzinfo=None)


def latest_sentence_miss(
    db: Session,
    lemma_id: int,
    now: datetime,
    lookback: timedelta = FRAGMENT_MISS_LOOKBACK,
) -> ReviewLog | None:
    cutoff = (now - lookback).replace(tzinfo=None)
    return (
        db.query(ReviewLog)
        .filter(
            ReviewLog.lemma_id == lemma_id,
            ReviewLog.rating <= 2,
            ReviewLog.review_mode != "quiz",
            or_(
                ReviewLog.sentence_id.isnot(None),
                ReviewLog.review_mode == "fragment",
            ),
            ReviewLog.reviewed_at >= cutoff,
        )
        .order_by(ReviewLog.reviewed_at.desc(), ReviewLog.id.desc())
        .first()
    )


def _unshown_count(db: Session, lemma_id: int) -> int:
    return (
        db.query(WordFragment)
        .filter(WordFragment.lemma_id == lemma_id, WordFragment.times_shown == 0)
        .count()
    )


def assign_retest(db: Session, lemma_id: int, now: datetime | None = None) -> tuple[str, WordFragment | None]:
    """Record the arm for this word's latest miss and pick what to show.

    Returns ``(arm, fragment)``. ``fragment`` is None in the word-card arm and
    when the fragment arm has nothing ready; the row then records
    ``delivered="word_card"`` so the analysis can stay intention-to-treat.
    Writes are small and committed here.
    """
    from app.services.canonical_resolution import resolve_canonical_lemma_id

    now = now or datetime.now(timezone.utc)
    lemma_id = resolve_canonical_lemma_id(db, lemma_id)
    if not fragment_retest_enabled() or not RETEST_AB_ACTIVE:
        return WORD_CARD_ARM, None
    miss = latest_sentence_miss(db, lemma_id, now)
    if miss is None:
        return WORD_CARD_ARM, None
    arm = retest_arm(lemma_id, miss.reviewed_at)
    fragment = None
    if arm == FRAGMENT_ARM:
        fragment = pick_fragment(db, lemma_id)
    try:
        row = (
            db.query(WordRetestAssignment)
            .filter(
                WordRetestAssignment.lemma_id == lemma_id,
                WordRetestAssignment.missed_at == miss.reviewed_at,
            )
            .first()
        )
        if row is None:
            row = WordRetestAssignment(
                lemma_id=lemma_id,
                missed_at=miss.reviewed_at,
                miss_rating=miss.rating,
                arm=arm,
                delivered=WORD_CARD_ARM,
                protocol=FRAGMENT_RETEST_PROTOCOL,
            )
            db.add(row)
        # A fragment delivery is never downgraded by a later fallback.
        if fragment is not None:
            row.delivered = FRAGMENT_ARM
            row.fragment_id = fragment.id
            fragment.times_shown = (fragment.times_shown or 0) + 1
            fragment.last_shown_at = now.replace(tzinfo=None)
        db.commit()
    except Exception:
        logger.exception("Retest assignment write failed for lemma %s", lemma_id)
        db.rollback()
        return WORD_CARD_ARM, None
    return arm, fragment


def _build_prompt(targets: list[dict], companions: list[dict]) -> str:
    from app.services.llm import format_known_words_by_pos

    target_lines = "\n".join(
        f'- lemma_id {t["lemma_id"]}: {t["arabic"]} ({t["pos"] or "word"}) = {t["english"]}'
        for t in targets
    )
    return f"""For each TARGET WORD write {FRAGMENT_CANDIDATES_PER_WORD} different Arabic phrases of 2 or 3 words that contain it.

TARGET WORDS:
{target_lines}

WORDS THE LEARNER KNOWS WELL (take the other word or words of each phrase from here; function words such as prepositions, pronouns and the article are also fine):
{format_known_words_by_pos(companions)}

RULES:
- 2 or 3 Arabic words per phrase; 4 only when one of them is a preposition or other particle.
- Each phrase is a complete small unit: a noun with its adjective, a verb with its subject or object, an iḍāfa pair, or a minimal sentence.
- Use the target in the meaning and part of speech given. Verbs should appear conjugated, nouns may take the article, a possessive suffix or a plural when that is natural.
- The other words must not give the target's meaning away by definition, and must not be rarer than the target.
- Typical, everyday collocations; nothing poetic or odd.
- Full diacritics on every Arabic word.
- The English is a natural translation of the whole phrase."""


def generate_fragments_for_lemmas(lemma_ids: list[int]) -> dict:
    """Background task: stock fragments for missed words in an arm that shows them.

    Read, then LLM with no session open, then a short write.
    """
    stats = {"requested": len(lemma_ids), "targets": 0, "returned": 0, "valid": 0, "stored": 0}
    if not lemma_ids or not fragment_retest_enabled():
        return stats

    from app.services.canonical_resolution import resolve_canonical_lemma_id
    from app.services.sentence_validator import (
        ARABIC_DIACRITICS,
        build_comprehensive_lemma_lookup,
        is_function_word_lemma,
        map_tokens_to_lemmas,
        strip_diacritics,
        tokenize,
    )

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        canonical_ids: list[int] = []
        for lemma_id in lemma_ids:
            canonical = resolve_canonical_lemma_id(db, lemma_id)
            if canonical not in canonical_ids:
                canonical_ids.append(canonical)
        targets: list[dict] = []
        target_bare: dict[int, str] = {}
        for lemma_id in canonical_ids:
            if not fragments_wanted(db, lemma_id, now):
                continue
            if _unshown_count(db, lemma_id) >= FRAGMENT_MIN_STOCK:
                continue
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
            if (
                lemma is None
                or not lemma.gloss_en
                or lemma.gates_completed_at is None
                or lemma.word_category in ("proper_name", "onomatopoeia", "practiced_name")
                or is_function_word_lemma(lemma.lemma_ar_bare, lemma.function_word_override)
            ):
                continue
            target_bare[lemma_id] = lemma.lemma_ar_bare or strip_diacritics(lemma.lemma_ar)
            targets.append({
                "lemma_id": lemma_id, "arabic": lemma.lemma_ar,
                "english": lemma.gloss_en, "pos": lemma.pos or "",
            })
            if len(targets) >= FRAGMENT_MAX_WORDS_PER_CALL:
                break
        stats["targets"] = len(targets)
        if not targets:
            return stats

        known_rows = (
            db.query(Lemma, UserLemmaKnowledge)
            .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
            .filter(UserLemmaKnowledge.knowledge_state == "known")
            .all()
        )
        known_ids = {lemma.lemma_id for lemma, _ in known_rows}
        from app.services.fsrs_service import parse_json_column

        def stability(ulk) -> float:
            card = parse_json_column(ulk.fsrs_card_json) or {}
            return float(card.get("stability") or 0.0) if isinstance(card, dict) else 0.0

        companions = [
            {"arabic": lemma.lemma_ar, "english": lemma.gloss_en or "",
             "lemma_id": lemma.lemma_id, "pos": lemma.pos or ""}
            for lemma, ulk in sorted(known_rows, key=lambda row: -stability(row[1]))
            if lemma.gloss_en
            and lemma.canonical_lemma_id is None
            and lemma.word_category not in ("proper_name", "onomatopoeia", "practiced_name")
            and not is_function_word_lemma(lemma.lemma_ar_bare, lemma.function_word_override)
        ][:FRAGMENT_COMPANION_SAMPLE]
        lookup = build_comprehensive_lemma_lookup(db, require_gated=True)
    finally:
        db.close()

    from app.services.llm import generate_completion, review_sentences_quality

    try:
        result = generate_completion(
            prompt=_build_prompt(targets, companions),
            system_prompt=_SYSTEM_PROMPT,
            json_schema=_SCHEMA,
            temperature=0.6,
            timeout=180,
            model_override="claude_sonnet",
            task_type="word_fragment_gen",
        )
    except Exception:
        logger.warning("Fragment generation call failed", exc_info=True)
        return stats

    wanted = {t["lemma_id"] for t in targets}
    candidates: list[dict] = []
    for word in (result.get("words") or []) if isinstance(result, dict) else []:
        if not isinstance(word, dict) or word.get("lemma_id") not in wanted:
            continue
        lemma_id = word["lemma_id"]
        for item in word.get("fragments") or []:
            if not isinstance(item, dict):
                continue
            arabic = (item.get("arabic") or "").strip()
            english = (item.get("english") or "").strip()
            if not arabic or not english:
                continue
            stats["returned"] += 1
            tokens = tokenize(arabic)
            if not (FRAGMENT_MIN_TOKENS <= len(tokens) <= FRAGMENT_MAX_TOKENS):
                continue
            if not ARABIC_DIACRITICS.search(arabic):
                continue
            mappings = map_tokens_to_lemmas(tokens, lookup, lemma_id, target_bare[lemma_id])
            target_tokens = [m for m in mappings if m.lemma_id == lemma_id]
            if len(target_tokens) != 1:
                continue
            ok = True
            for mapping in mappings:
                if mapping.lemma_id == lemma_id:
                    continue
                if mapping.is_proper_name or mapping.lemma_id is None or mapping.via_camel:
                    ok = False
                    break
                if not mapping.is_function_word and mapping.lemma_id not in known_ids:
                    ok = False
                    break
            if not ok:
                continue
            candidates.append({
                "lemma_id": lemma_id, "arabic": arabic, "english": english,
                "target_surface": target_tokens[0].surface_form,
            })
    stats["valid"] = len(candidates)
    if not candidates:
        return stats

    try:
        reviews = review_sentences_quality(
            [{"arabic": c["arabic"], "english": c["english"]} for c in candidates]
        )
    except Exception:
        logger.warning("Fragment quality review failed", exc_info=True)
        return stats
    passed = [
        c for c, review in zip(candidates, reviews)
        if review.review_completed and review.natural and review.translation_correct
    ]
    if not passed:
        return stats

    from app.services.transliteration import transliterate_arabic

    db = SessionLocal()
    try:
        existing = {
            (row.lemma_id, strip_diacritics(row.arabic_text))
            for row in db.query(WordFragment)
            .filter(WordFragment.lemma_id.in_({c["lemma_id"] for c in passed}))
            .all()
        }
        for c in passed:
            key = (c["lemma_id"], strip_diacritics(c["arabic"]))
            if key in existing:
                continue
            existing.add(key)
            db.add(WordFragment(
                lemma_id=c["lemma_id"],
                arabic_text=c["arabic"],
                english_translation=c["english"],
                transliteration=transliterate_arabic(c["arabic"]),
                target_surface=c["target_surface"],
            ))
            stats["stored"] += 1
        db.commit()
    except Exception:
        logger.exception("Fragment write failed")
        db.rollback()
    finally:
        db.close()
    logger.info("Fragments: %s", stats)
    return stats


def stock_followup_fragments(max_words: int = FRAGMENT_MAX_WORDS_PER_CALL) -> dict:
    """Warm-cache step: phrases for open misses in the phrase follow-up arm that
    have none left unshown. The follow-up comes days after the miss, so the
    stock written at miss time may be used up by the same-day retry."""
    if not fragment_retest_enabled():
        return {"requested": 0}
    from app.services.missed_word_recovery import open_misses, recovery_order

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        misses = open_misses(db, now)
        wanted = [
            lemma_id
            for lemma_id in recovery_order(misses)
            if followup_arm(lemma_id, misses[lemma_id].missed_at) == PHRASE_ARM
            and _unshown_count(db, lemma_id) == 0
        ][:max_words]
    finally:
        db.close()
    return generate_fragments_for_lemmas(wanted)
