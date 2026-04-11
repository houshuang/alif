"""Sentence-centric session assembly.

Selects a review session of sentences that maximally cover due words,
ordered for good learning flow (easy -> hard -> easy).
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from app.services.fsrs_service import parse_json_column
from app.services.transliteration import transliterate_arabic, transliterate_forms

from app.models import (
    GrammarFeature,
    LearnerSettings,
    Lemma,
    ReviewLog,
    Root,
    Sentence,
    SentenceGrammarFeature,
    SentenceWord,
    UserGrammarExposure,
    UserLemmaKnowledge,
)
from app.services.interaction_logger import log_interaction
from app.services.sentence_validator import (
    FUNCTION_WORDS,
    FUNCTION_WORD_GLOSSES,
    _is_function_word,
    build_lemma_lookup,
    normalize_alef,
    strip_diacritics,
    strip_tatweel,
)

logger = logging.getLogger(__name__)

# Acquisition repetition: each acquiring word should appear this many times in a session
MIN_ACQUISITION_EXPOSURES = 4
MAX_ACQUISITION_EXTRA_SLOTS = 15  # max extra cards beyond session limit for repetitions
MAX_AUTO_INTRO_PER_SESSION = 5  # cap new words per single auto-intro call
AUTO_INTRO_ACCURACY_FLOOR = 0.70  # pause introduction if recent accuracy below this
INTRO_RESERVE_FRACTION = 0.2  # fraction of session slots reserved for new word introductions
PIPELINE_BACKLOG_THRESHOLD = 40  # suppress reserved intros when acquiring pipeline exceeds this
SESSION_SCAFFOLD_DECAY = 0.5  # per-appearance decay for scaffold words already in session
NEVER_REVIEWED_BOOST = 5.0  # score multiplier for sentences targeting acquiring words with 0 reviews
OVERDUE_ESCALATION_DAYS = 3.0  # start escalating after this many days overdue
OVERDUE_ESCALATION_MAX = 4.0  # max multiplier for severely overdue words
MAX_UNKNOWN_SCAFFOLD = 2  # max unknown non-target words per sentence (prevents overwhelming density)
MIN_SESSION_SENTENCES = 5  # minimum sentences before pulling in almost-due words
COMPREHENSIBILITY_THRESHOLD = 0.6  # min fraction of scaffold words that must be known


def _intro_slots_for_accuracy(accuracy: float) -> int:
    """Return how many words to auto-introduce based on recent session accuracy.

    Replaces the binary pause/continue logic with a graduated ramp:
    - <70%: 0 (struggling, don't add new words)
    - 70-85%: 3 (doing okay, slow introduction)
    - >=85%: MAX_AUTO_INTRO_PER_SESSION (doing well, full speed)
    """
    if accuracy < 0.70:
        return 0
    if accuracy < 0.85:
        return 3
    return MAX_AUTO_INTRO_PER_SESSION


def _get_accuracy_intro_slots(db: Session, now: datetime) -> int:
    """Compute how many intro slots the learner's accuracy allows."""
    recent_reviews = (
        db.query(ReviewLog)
        .filter(ReviewLog.reviewed_at >= (now - timedelta(days=2)).replace(tzinfo=None))
        .all()
    )
    if len(recent_reviews) >= 10:
        correct = sum(1 for r in recent_reviews if r.rating >= 3)
        accuracy = correct / len(recent_reviews)
        return _intro_slots_for_accuracy(accuracy)
    return 4  # conservative default with insufficient data


@dataclass
class WordMeta:
    lemma_id: Optional[int]
    surface_form: str
    gloss_en: Optional[str]
    stability: Optional[float]
    is_due: bool
    is_function_word: bool = False
    knowledge_state: str = "new"


@dataclass
class SentenceCandidate:
    sentence_id: int
    sentence: object
    words_meta: list[WordMeta] = field(default_factory=list)
    due_words_covered: set[int] = field(default_factory=set)
    score: float = 0.0
    score_components: dict = field(default_factory=dict)
    selection_reason: str = ""
    selection_order: int = 0


_GRAMMAR_ABBREV = {
    "singular": "sg.", "dual": "du.", "plural_sound": "pl.", "plural_broken": "pl.",
    "masculine": "m.", "feminine": "f.",
    "past": "past", "present": "pres.", "imperative": "impr.",
    "form_1": "I", "form_2": "II", "form_3": "III", "form_4": "IV",
    "form_5": "V", "form_6": "VI", "form_7": "VII", "form_8": "VIII",
    "form_9": "IX", "form_10": "X",
    "definite_article": "def.", "attached_pronouns": "+pron",
    "proclitic_prepositions": "+prep",
}


def _compact_grammar_tags(features_json) -> list[str]:
    """Convert grammar feature keys to compact display abbreviations."""
    feats = parse_json_column(features_json, default=[])
    if not isinstance(feats, list):
        return []
    return [_GRAMMAR_ABBREV[f] for f in feats if f in _GRAMMAR_ABBREV]


def _get_stability(knowledge: Optional[UserLemmaKnowledge]) -> float:
    if not knowledge or not knowledge.fsrs_card_json:
        return 0.0
    card_data = parse_json_column(knowledge.fsrs_card_json)
    return card_data.get("stability") or 0.0


def _get_due_dt(knowledge: UserLemmaKnowledge) -> Optional[datetime]:
    card_data = parse_json_column(knowledge.fsrs_card_json)
    if not card_data:
        return None
    due_str = card_data.get("due")
    if not due_str:
        return None
    dt = datetime.fromisoformat(due_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _overdue_escalation(due_word_ids: set[int], overdue_days_map: dict[int, float]) -> float:
    """Score multiplier for sentences covering overdue words.

    Words overdue by more than OVERDUE_ESCALATION_DAYS get a growing multiplier
    (up to OVERDUE_ESCALATION_MAX) so they can compete with multi-word sentences.
    Uses the max overdue value among covered words.
    """
    if not due_word_ids:
        return 1.0
    max_overdue = max(overdue_days_map.get(lid, 0.0) for lid in due_word_ids)
    if max_overdue <= OVERDUE_ESCALATION_DAYS:
        return 1.0
    # Linear ramp: 1.0 at threshold, OVERDUE_ESCALATION_MAX at threshold + 14 days
    escalation = 1.0 + (max_overdue - OVERDUE_ESCALATION_DAYS) / 14.0 * (OVERDUE_ESCALATION_MAX - 1.0)
    return min(escalation, OVERDUE_ESCALATION_MAX)


def _difficulty_match_quality(
    weakest_stability: float,
    scaffold_stabilities: list[float],
) -> float:
    """Score how well scaffold words match the difficulty needs of the weakest due word.

    Thresholds are low to work for early learners (days/weeks of study).
    Scaffold words just need to be somewhat more stable than the weakest due word.
    """
    if not scaffold_stabilities:
        return 1.0

    avg_scaffold = sum(scaffold_stabilities) / len(scaffold_stabilities)

    if weakest_stability < 0.5:
        # Very fragile word: prefer scaffolds with at least 1 day stability
        if any(s < 0.5 for s in scaffold_stabilities):
            return 0.3
        return 1.0
    elif weakest_stability < 3.0:
        # Still shaky: scaffolds should average above weakest
        if avg_scaffold < weakest_stability:
            return 0.5
        return 1.0
    else:
        return 1.0


def _grammar_fit(
    grammar_features: list[str],
    grammar_exposure: dict[str, dict],
) -> float:
    """Compute grammar fit multiplier for a sentence.

    Returns 0.8-1.1 based on how well the sentence's grammar features
    match the user's grammar comfort level.
    """
    if not grammar_features:
        return 1.0

    multipliers: list[float] = []
    for feat_key in grammar_features:
        exp = grammar_exposure.get(feat_key)
        if exp is None:
            # Never seen and not introduced — slight penalty
            multipliers.append(0.8)
        elif exp.get("introduced") and exp.get("comfort", 0) < 0.3:
            # Introduced but low comfort — neutral
            multipliers.append(1.0)
        elif exp.get("comfort", 0) >= 0.5:
            # Comfortable — slight bonus
            multipliers.append(1.1)
        elif exp.get("comfort", 0) >= 0.3:
            # Moderate comfort — neutral
            multipliers.append(1.0)
        else:
            # Seen but not introduced and low comfort
            if exp.get("introduced"):
                multipliers.append(1.0)
            else:
                multipliers.append(0.8)

    # Average the multipliers
    return sum(multipliers) / len(multipliers)


FRESHNESS_BASELINE = 5  # reviews at which penalty starts


def _scaffold_freshness(
    words_meta: list[WordMeta],
    knowledge_map: dict[int, "UserLemmaKnowledge"],
) -> float:
    """Penalize sentences whose scaffold words are over-reviewed.

    For each non-due, non-function scaffold word, compute
    penalty = min(1.0, FRESHNESS_BASELINE / max(times_seen, 1)).
    Aggregate via geometric mean, floored at 0.3.

    Effect: scaffold word seen 5× → 1.0 (no penalty),
    10× → 0.5, 50× → 0.1 (floored to 0.1 at sentence level).
    """
    scaffold = [
        w for w in words_meta
        if w.lemma_id and not w.is_due and not w.is_function_word
    ]
    if not scaffold:
        return 1.0

    product = 1.0
    for w in scaffold:
        k = knowledge_map.get(w.lemma_id)
        times_seen = (k.times_seen or 0) if k else 0
        penalty = min(1.0, FRESHNESS_BASELINE / max(times_seen, 1))
        product *= penalty

    geo_mean = product ** (1.0 / len(scaffold))
    return max(0.1, geo_mean)


def compute_sentence_diversity_score(
    words_meta: list[WordMeta],
    knowledge_map: dict[int, UserLemmaKnowledge],
    session_scaffold_counts: dict[int, int] | None = None,
) -> dict:
    """Compute per-sentence diversity metrics for logging."""
    scaffold = [
        w for w in words_meta
        if w.lemma_id and not w.is_due and not w.is_function_word
    ]
    unique_scaffold_count = len({w.lemma_id for w in scaffold})

    freshness = _scaffold_freshness(words_meta, knowledge_map)

    if session_scaffold_counts and scaffold:
        low_reuse = sum(
            1 for w in scaffold
            if session_scaffold_counts.get(w.lemma_id, 0) <= 1
        )
        scaffold_uniqueness = low_reuse / len(scaffold)
    else:
        scaffold_uniqueness = 1.0

    diversity_score = (freshness * scaffold_uniqueness) ** 0.5

    return {
        "diversity_score": round(diversity_score, 3),
        "scaffold_uniqueness": round(scaffold_uniqueness, 3),
        "scaffold_freshness": round(freshness, 3),
        "unique_scaffold_count": unique_scaffold_count,
    }


def _auto_introduce_words(
    db: Session,
    slots_needed: int,
    knowledge_by_id: dict[int, UserLemmaKnowledge],
    now: datetime,
    skip_material_gen: bool = False,
) -> list[int]:
    """Auto-introduce new words to fill an undersized session.

    The only throttle is: how many more words does the session need?
    No global cap on acquiring count — if words aren't due yet, they're
    not competing for session space. The session limit is the natural cap.

    Accuracy-based throttle still applies: if the learner is struggling,
    slow down introduction rate.
    """

    if slots_needed <= 0:
        return []

    # Adaptive introduction rate based on recent accuracy
    recent_reviews = (
        db.query(ReviewLog)
        .filter(ReviewLog.reviewed_at >= now - timedelta(days=2))
        .all()
    )
    if len(recent_reviews) >= 10:
        correct = sum(1 for r in recent_reviews if r.rating >= 3)
        accuracy = correct / len(recent_reviews)
        accuracy_slots = _intro_slots_for_accuracy(accuracy)
        if accuracy_slots == 0:
            logger.info(
                f"Auto-intro paused: recent accuracy {accuracy:.0%} < {AUTO_INTRO_ACCURACY_FLOOR:.0%}"
            )
            return []
    else:
        accuracy = None
        accuracy_slots = 4  # conservative default with insufficient data

    slots = min(accuracy_slots, slots_needed, MAX_AUTO_INTRO_PER_SESSION)
    if slots <= 0:
        return []

    from app.services.word_selector import select_next_words, introduce_word
    from app.services.material_generator import generate_material_for_word
    from app.services.topic_service import ensure_active_topic

    active_topic = ensure_active_topic(db)
    # Request extra candidates since we filter out names/sounds
    candidates = select_next_words(db, count=slots + 5, domain=active_topic)
    # Never auto-introduce names, onomatopoeia, or function words
    from app.services.sentence_validator import _is_function_word
    candidates = [c for c in candidates if c.get("word_category") not in ("proper_name", "onomatopoeia")]
    candidates = [c for c in candidates if not _is_function_word(c.get("lemma_ar_bare", ""))]
    if not candidates:
        return []

    introduced_ids: list[int] = []
    for cand in candidates[:slots]:
        lid = cand["lemma_id"]
        tier = cand.get("score_breakdown", {}).get("priority_tier", "")
        if tier.startswith("book_p"):
            intro_source = "book"
        elif tier == "active_story":
            intro_source = "story_import"
        elif tier in ("textbook_scan", "duolingo"):
            intro_source = tier
        else:
            intro_source = "auto_intro"
        story_id = cand.get("story_id")
        try:
            result = introduce_word(db, lid, source=intro_source, due_immediately=True)
            if result.get("already_known"):
                continue
            if story_id:
                lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
                if lemma and not lemma.source_story_id:
                    lemma.source_story_id = story_id
                    db.commit()
            introduced_ids.append(lid)
            logger.info(f"Auto-introduced word {lid}: {cand.get('lemma_ar', '?')} source={intro_source}")

            if not skip_material_gen:
                try:
                    generate_material_for_word(lid, needed=2)
                except Exception:
                    logger.warning(f"Material generation failed for auto-intro {lid}")
        except Exception:
            logger.warning(f"Failed to auto-introduce word {lid}")

    if introduced_ids:
        from app.services.interaction_logger import log_interaction
        log_interaction(
            event="auto_introduce",
            count=len(introduced_ids),
            lemma_ids=introduced_ids,
            accuracy=round(accuracy, 3) if accuracy is not None else None,
            accuracy_slots=accuracy_slots,
            slots_needed=slots_needed,
        )

    return introduced_ids


def build_session(
    db: Session,
    limit: int = 10,
    mode: str = "reading",
    log_events: bool = True,
    exclude_sentence_ids: set[int] | None = None,
) -> dict:
    """Assemble a sentence-based review session.

    Returns a dict matching SentenceSessionOut schema:
    {session_id, items, total_due_words, covered_due_words}
    """
    import logging
    logger = logging.getLogger(__name__)
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Load tashkeel settings
    tashkeel_settings = db.query(LearnerSettings).first()
    tashkeel_mode = (tashkeel_settings.tashkeel_mode if tashkeel_settings else None) or "always"
    tashkeel_threshold = (tashkeel_settings.tashkeel_stability_threshold if tashkeel_settings else None) or 30.0

    # Comprehension-aware recency cutoffs
    # Failed sentences can be re-shown quickly so learner gets a positive review,
    # then ideally sees the same word in a different sentence next time.
    # "understood" uses 1-day window (was 4d, but high-volume learners exhaust the
    # 610-sentence pool within 4 days, causing undersized sessions).
    cutoff_understood = now - timedelta(days=1)
    cutoff_partial = now - timedelta(hours=4)
    cutoff_no_idea = now - timedelta(minutes=30)

    # 1. Fetch all word knowledge (exclude only suspended)
    # Encountered words are included so the comprehensibility gate can
    # treat them as passive vocabulary (seen but not yet formally studied).
    all_knowledge = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state != "suspended",
        )
        .all()
    )

    due_lemma_ids: set[int] = set()
    stability_map: dict[int, float] = {}
    knowledge_by_id: dict[int, UserLemmaKnowledge] = {}

    # Build function word lemma ID set to exclude from scheduling
    function_word_lemma_ids: set[int] = set()
    lemma_bare_map = {
        row.lemma_id: row.lemma_ar_bare
        for row in db.query(Lemma.lemma_id, Lemma.lemma_ar_bare).all()
    }
    for lid, bare in lemma_bare_map.items():
        if bare and _is_function_word(bare):
            function_word_lemma_ids.add(lid)

    overdue_days_map: dict[int, float] = {}  # lemma_id → days past due

    for k in all_knowledge:
        knowledge_by_id[k.lemma_id] = k

        if k.knowledge_state == "encountered":
            continue  # passive vocab — not due, not scheduled
        if k.lemma_id in function_word_lemma_ids:
            continue  # function words excluded from scheduling
        elif k.knowledge_state == "acquiring":
            # Acquisition words use box-based pseudo-stability for difficulty matching
            box = k.acquisition_box or 1
            pseudo_stability = {1: 0.1, 2: 0.5, 3: 2.0}.get(box, 0.1)
            stability_map[k.lemma_id] = pseudo_stability
            # Check if acquisition review is due (naive→aware conversion for SQLite)
            if k.acquisition_next_due:
                acq_due = k.acquisition_next_due
                if acq_due.tzinfo is None:
                    acq_due = acq_due.replace(tzinfo=timezone.utc)
                if acq_due <= now:
                    due_lemma_ids.add(k.lemma_id)
                    overdue_days_map[k.lemma_id] = (now - acq_due).total_seconds() / 86400
        elif k.fsrs_card_json:
            stability_map[k.lemma_id] = _get_stability(k)
            due_dt = _get_due_dt(k)
            if due_dt and due_dt <= now:
                due_lemma_ids.add(k.lemma_id)
                overdue_days_map[k.lemma_id] = (now - due_dt).total_seconds() / 86400

    # Filter through focus cohort — only review words in the active cohort
    from app.services.cohort_service import get_focus_cohort
    cohort = get_focus_cohort(db)
    due_lemma_ids &= cohort

    # Auto-introduce new words: reserve slots even when due queue is full
    # This ensures vocabulary growth doesn't stall when reviews pile up.
    # But suppress reserved intro slots when the acquiring pipeline is overloaded —
    # still fill undersized sessions (when due < limit).
    accuracy_slots = _get_accuracy_intro_slots(db, now)
    acquiring_count = sum(
        1 for k in all_knowledge if k.knowledge_state == "acquiring"
    )
    # Dynamic backlog threshold: scale with accuracy so high-performing learners
    # aren't starved of new words by an inflow they can clearly handle.
    recent_reviews = (
        db.query(ReviewLog)
        .filter(ReviewLog.reviewed_at >= (now - timedelta(days=2)).replace(tzinfo=None))
        .all()
    )
    if len(recent_reviews) >= 10:
        recent_accuracy = sum(1 for r in recent_reviews if r.rating >= 3) / len(recent_reviews)
    else:
        recent_accuracy = 0.80  # conservative default
    if recent_accuracy >= 0.90:
        effective_threshold = 80
    elif recent_accuracy >= 0.80:
        effective_threshold = 60
    else:
        effective_threshold = PIPELINE_BACKLOG_THRESHOLD  # 40

    if accuracy_slots > 0 and acquiring_count <= effective_threshold:
        reserved_intro = max(1, int(limit * INTRO_RESERVE_FRACTION))
        intro_slots = min(accuracy_slots, reserved_intro)
    else:
        intro_slots = 0
        if acquiring_count > effective_threshold:
            logger.info(
                f"Auto-intro reserved slots suppressed: {acquiring_count} acquiring "
                f"> {effective_threshold} threshold (accuracy={recent_accuracy:.0%})"
            )
    undersized_slots = max(0, limit - len(due_lemma_ids))
    slots_for_intro = max(intro_slots, undersized_slots)
    auto_introduced_ids = _auto_introduce_words(
        db, slots_for_intro, knowledge_by_id, now,
        skip_material_gen=True,
    )
    if auto_introduced_ids:
        # Add newly introduced words to due set and tracking structures
        for lid in auto_introduced_ids:
            due_lemma_ids.add(lid)
            stability_map[lid] = 0.1  # pseudo-stability for box 1
            ulk = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == lid)
                .first()
            )
            if ulk:
                knowledge_by_id[lid] = ulk
                all_knowledge.append(ulk)  # ensure comprehensibility gate sees them
        # Refresh cohort to include newly acquiring words
        cohort = get_focus_cohort(db)
        due_lemma_ids &= cohort

    # Identify struggling words: seen 3+ times, never correct
    struggling_ids: set[int] = set()
    for lid in list(due_lemma_ids):
        k = knowledge_by_id.get(lid)
        if k and (k.times_seen or 0) >= 3 and (k.times_correct or 0) == 0:
            struggling_ids.add(lid)

    # Keep struggling words in due_lemma_ids so they get sentences through
    # normal greedy selection + acquisition repetition (MIN_ACQUISITION_EXPOSURES).
    # They also get reintro cards below for re-teaching.

    total_due = len(due_lemma_ids)

    # Fallback: if nothing is due, pull in "almost due" FSRS words
    # (closest to their due date) so the learner always has something to review.
    if not due_lemma_ids and not struggling_ids:
        import logging
        logger = logging.getLogger(__name__)
        almost_due: list[tuple[int, datetime]] = []
        for k in all_knowledge:
            if k.knowledge_state in ("known", "learning", "lapsed") and k.fsrs_card_json:
                due_dt = _get_due_dt(k)
                if due_dt:
                    almost_due.append((k.lemma_id, due_dt))
            elif k.knowledge_state == "acquiring" and k.acquisition_next_due:
                acq_due = k.acquisition_next_due
                if acq_due.tzinfo is None:
                    acq_due = acq_due.replace(tzinfo=timezone.utc)
                almost_due.append((k.lemma_id, acq_due))
        almost_due.sort(key=lambda x: x[1])
        # Take extra candidates before cohort filtering (cohort may exclude many)
        preview_ids = [lid for lid, _ in almost_due[:limit * 3]]
        if preview_ids:
            due_lemma_ids = set(preview_ids) & cohort
            for lid in due_lemma_ids:
                if lid not in stability_map:
                    k = knowledge_by_id.get(lid)
                    if k:
                        stability_map[lid] = _get_stability(k) if k.fsrs_card_json else 0.1
            logger.info(f"No due words — previewing {len(due_lemma_ids)} almost-due words")
        if not due_lemma_ids:
            return {
                "session_id": session_id,
                "items": [],
                "total_due_words": 0,
                "covered_due_words": 0,
                "reintro_cards": [],
            }

    # Build reintro cards for struggling words (limit 3 per session)
    reintro_cards = _build_reintro_cards(db, struggling_ids, limit=3) if struggling_ids else []

    if not due_lemma_ids:
        return {
            "session_id": session_id,
            "items": [],
            "total_due_words": total_due,
            "covered_due_words": 0,
            "reintro_cards": reintro_cards,
            "experiment_intro_cards": [],
        }

    # 2. Fetch candidate sentences containing at least one due word
    sentence_words = (
        db.query(SentenceWord)
        .filter(SentenceWord.lemma_id.in_(due_lemma_ids))
        .all()
    )

    sentence_ids_with_due = {sw.sentence_id for sw in sentence_words}
    if exclude_sentence_ids:
        sentence_ids_with_due -= exclude_sentence_ids
    if not sentence_ids_with_due:
        return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, [], limit, reintro_cards=reintro_cards, knowledge_by_id=knowledge_by_id, all_knowledge=all_knowledge, mode=mode)

    from sqlalchemy import or_

    # Use mode-specific comprehension columns for recency filter
    if mode == "listening":
        comp_col = Sentence.last_listening_comprehension
        shown_col = Sentence.last_listening_shown_at
    else:
        comp_col = Sentence.last_reading_comprehension
        shown_col = Sentence.last_reading_shown_at

    sentences = (
        db.query(Sentence)
        .filter(
            Sentence.id.in_(sentence_ids_with_due),
            Sentence.is_active == True,  # noqa: E712
            or_(
                shown_col.is_(None),
                (comp_col == "understood") & (shown_col < cutoff_understood),
                (comp_col == "partial") & (shown_col < cutoff_partial),
                (comp_col == "no_idea") & (shown_col < cutoff_no_idea),
                (comp_col.is_(None)) & (shown_col < cutoff_understood),
            ),
        )
        .all()
    )

    # Rescue pass: for due words whose sentences ALL failed recency (e.g. all
    # "understood" within 4 days), fetch those sentences anyway so the word isn't
    # dropped from the session entirely. They'll get a score penalty below.
    fresh_sent_ids = {s.id for s in sentences}
    words_with_fresh = {
        sw.lemma_id for sw in sentence_words
        if sw.sentence_id in fresh_sent_ids and sw.lemma_id in due_lemma_ids
    }
    words_needing_rescue = due_lemma_ids - words_with_fresh
    rescue_sentence_ids: set[int] = set()

    if words_needing_rescue:
        rescue_sw_rows = [
            sw for sw in sentence_words
            if sw.lemma_id in words_needing_rescue
            and sw.sentence_id not in fresh_sent_ids
        ]
        potential_rescue_ids = {sw.sentence_id for sw in rescue_sw_rows}
        if potential_rescue_ids:
            rescue_sents = (
                db.query(Sentence)
                .filter(
                    Sentence.id.in_(potential_rescue_ids),
                    Sentence.is_active == True,  # noqa: E712
                )
                .all()
            )
            if rescue_sents:
                rescue_sentence_ids = {s.id for s in rescue_sents}
                sentences.extend(rescue_sents)

    if not sentences:
        return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, [], limit, reintro_cards=reintro_cards, knowledge_by_id=knowledge_by_id, all_knowledge=all_knowledge, mode=mode)

    sentence_map: dict[int, Sentence] = {s.id: s for s in sentences}

    # Load all sentence words for these sentences
    all_sw = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id.in_(sentence_map.keys()))
        .order_by(SentenceWord.sentence_id, SentenceWord.position)
        .all()
    )

    # Backfill missing lemma IDs in older sentence rows, including
    # function words that now have lemma entries.
    # Uses fast dict lookup only (no CAMeL) to avoid 4s+ disambiguation cost.
    # Uses SAVEPOINT so a DB lock doesn't crash the whole session build.
    words_missing_lemma = [sw for sw in all_sw if sw.lemma_id is None]
    if words_missing_lemma:
        lookup_lemmas = db.query(Lemma).all()
        lemma_lookup = build_lemma_lookup(lookup_lemmas) if lookup_lemmas else {}
        backfilled = 0
        for sw in words_missing_lemma:
            bare = strip_diacritics(sw.surface_form)
            bare_norm = normalize_alef(strip_tatweel(bare))
            lemma_id = lemma_lookup.get(bare_norm)
            if lemma_id is not None:
                sw.lemma_id = lemma_id
                backfilled += 1
        if backfilled > 0:
            import logging
            _log = logging.getLogger(__name__)
            try:
                with db.begin_nested():
                    db.flush()
                _log.info(f"Backfilled {backfilled} lemma IDs")
            except OperationalError:
                _log.warning(f"DB lock during lemma backfill, deferring ({backfilled} words)")

    sw_by_sentence: dict[int, list[SentenceWord]] = {}
    for sw in all_sw:
        sw_by_sentence.setdefault(sw.sentence_id, []).append(sw)

    # Load lemma info
    all_lemma_ids = {sw.lemma_id for sw in all_sw if sw.lemma_id}
    all_lemma_ids |= due_lemma_ids
    lemmas = db.query(Lemma).options(joinedload(Lemma.root)).filter(Lemma.lemma_id.in_(all_lemma_ids)).all() if all_lemma_ids else []
    lemma_map = {l.lemma_id: l for l in lemmas}

    # Build variant→canonical map so sentences with variant forms cover canonical due words
    # Follow multi-hop chains (A→B→C → A maps to C)
    variant_to_canonical: dict[int, int] = {}
    for l in lemmas:
        if l.canonical_lemma_id:
            variant_to_canonical[l.lemma_id] = l.canonical_lemma_id
    changed = True
    while changed:
        changed = False
        missing_ids = set()
        for vid, cid in list(variant_to_canonical.items()):
            canon = lemma_map.get(cid)
            if canon and canon.canonical_lemma_id:
                variant_to_canonical[vid] = canon.canonical_lemma_id
                missing_ids.add(canon.canonical_lemma_id)
                changed = True
        for mid in missing_ids - set(lemma_map.keys()):
            lo = db.query(Lemma).filter(Lemma.lemma_id == mid).first()
            if lo:
                lemma_map[lo.lemma_id] = lo

    knowledge_map = {k.lemma_id: k for k in all_knowledge}

    # Load grammar exposure for grammar_fit scoring
    from app.services.grammar_service import compute_comfort
    grammar_exposure_map: dict[str, dict] = {}
    gram_exposures = (
        db.query(UserGrammarExposure)
        .join(GrammarFeature)
        .all()
    )
    for exp in gram_exposures:
        grammar_exposure_map[exp.feature.feature_key] = {
            "comfort": compute_comfort(exp.times_seen, exp.times_correct, exp.last_seen_at),
            "introduced": exp.introduced_at is not None,
        }

    # Pre-compute grammar features per sentence from SentenceGrammarFeature + lemma tags
    sentence_grammar_cache: dict[int, list[str]] = {}
    if sentence_map:
        sgf_rows = (
            db.query(SentenceGrammarFeature.sentence_id, GrammarFeature.feature_key)
            .join(GrammarFeature)
            .filter(SentenceGrammarFeature.sentence_id.in_(sentence_map.keys()))
            .all()
        )
        for sid, fk in sgf_rows:
            sentence_grammar_cache.setdefault(sid, []).append(fk)

    # For listening mode, pre-compute which words are "listening-ready":
    # at least one positive review AND (no negatives OR last review positive)
    listening_ready: set[int] = set()
    if mode == "listening":
        non_due_ids = all_lemma_ids - due_lemma_ids
        if non_due_ids:
            from sqlalchemy import func as sa_func
            max_ids = (
                db.query(ReviewLog.lemma_id, sa_func.max(ReviewLog.id).label("max_id"))
                .filter(ReviewLog.lemma_id.in_(non_due_ids))
                .group_by(ReviewLog.lemma_id)
                .subquery()
            )
            last_reviews = (
                db.query(ReviewLog.lemma_id, ReviewLog.rating)
                .join(max_ids, ReviewLog.id == max_ids.c.max_id)
                .all()
            )
            last_rating_map = {r.lemma_id: r.rating for r in last_reviews}
            for lid in non_due_ids:
                k = knowledge_map.get(lid)
                if not k:
                    continue
                if (k.times_correct or 0) < 1:
                    continue
                last_r = last_rating_map.get(lid)
                if last_r is not None and last_r >= 3:
                    listening_ready.add(lid)

    # Pre-compute acquiring words that need a score boost to compete with
    # multi-word FSRS sentences in the greedy selection.
    # Two categories: (1) never reviewed (times_seen == 0), and (2) zero-accuracy
    # words (seen but never correct) — these fall through the cracks when they
    # lose the times_seen==0 boost after their first failed review.
    boosted_acquiring_ids: set[int] = set()
    for lid in due_lemma_ids:
        k = knowledge_by_id.get(lid)
        if k and k.knowledge_state == "acquiring":
            if (k.times_seen or 0) == 0:
                boosted_acquiring_ids.add(lid)
            elif (k.times_correct or 0) == 0:
                boosted_acquiring_ids.add(lid)
    if boosted_acquiring_ids:
        logger.info(f"Boosted acquiring words due: {len(boosted_acquiring_ids)}")

    # Build candidates
    candidates: list[SentenceCandidate] = []
    for sent in sentences:
        sws = sw_by_sentence.get(sent.id, [])
        due_covered: set[int] = set()
        word_metas: list[WordMeta] = []
        scaffold_stabilities: list[float] = []

        for sw in sws:
            lemma = lemma_map.get(sw.lemma_id) if sw.lemma_id else None
            # Resolve variant→canonical for scheduling purposes
            effective_id = variant_to_canonical.get(sw.lemma_id, sw.lemma_id) if sw.lemma_id else None
            stab = stability_map.get(effective_id, 0.0) if effective_id else None
            # Check both canonical and original ID — a variant word that is due
            # must match its own sentences, not just its canonical's
            is_due = (effective_id in due_lemma_ids or (sw.lemma_id is not None and sw.lemma_id in due_lemma_ids)) if effective_id else False

            k_state = "new"
            if effective_id:
                k = knowledge_map.get(effective_id)
                if k:
                    k_state = k.knowledge_state or "new"

            bare = strip_diacritics(sw.surface_form)
            is_func = _is_function_word(bare)
            gloss = lemma.gloss_en if lemma else FUNCTION_WORD_GLOSSES.get(bare)
            if not gloss:
                bare_norm = normalize_alef(strip_tatweel(bare))
                gloss = FUNCTION_WORD_GLOSSES.get(bare_norm)
            if not gloss and sw.lemma_id:
                logger.warning(f"Word '{sw.surface_form}' (lemma_id={sw.lemma_id}) has no gloss in sentence {sw.sentence_id}")
            wm = WordMeta(
                lemma_id=sw.lemma_id,  # original lemma for display/lookup (effective_id used only for scheduling)
                surface_form=sw.surface_form,
                gloss_en=gloss,
                stability=stab,
                is_due=is_due,
                is_function_word=is_func,
                knowledge_state=k_state,
            )
            word_metas.append(wm)

            if effective_id and is_due:
                due_covered.add(effective_id)
                # Also track the original variant ID if different
                if sw.lemma_id and sw.lemma_id in due_lemma_ids:
                    due_covered.add(sw.lemma_id)
            elif effective_id and stab is not None:
                scaffold_stabilities.append(stab)

        if not due_covered:
            continue

        # Comprehensibility gate: skip sentences where <THRESHOLD of scaffold words are known.
        scaffold = [w for w in word_metas if not w.is_function_word and not w.is_due]
        total_scaffold = len(scaffold)
        known_scaffold = sum(
            1 for w in scaffold
            if w.knowledge_state in ("known", "learning", "lapsed", "acquiring")
        )
        if total_scaffold > 0 and known_scaffold / total_scaffold < COMPREHENSIBILITY_THRESHOLD:
            continue

        # Unknown density cap: reject sentences with too many unknown non-target words.
        unknown_scaffold = total_scaffold - known_scaffold
        if unknown_scaffold > MAX_UNKNOWN_SCAFFOLD:
            continue

        # Listening mode: skip if any non-function, non-due word isn't listening-ready
        if mode == "listening":
            scaffold_ids = [w.lemma_id for w in word_metas
                            if w.lemma_id and not w.is_due and not w.is_function_word]
            if any(lid not in listening_ready for lid in scaffold_ids):
                continue

        weakest = min(stability_map.get(lid, 0.0) for lid in due_covered)
        dmq = _difficulty_match_quality(weakest, scaffold_stabilities)

        # Grammar fit: derive features from cache or lemma tags
        sent_grammar = sentence_grammar_cache.get(sent.id)
        if sent_grammar is None:
            feat_keys: set[str] = set()
            for sw in sws:
                if sw.lemma_id:
                    lem = lemma_map.get(sw.lemma_id)
                    if lem and lem.grammar_features_json:
                        feats = parse_json_column(lem.grammar_features_json, default=[])
                        if isinstance(feats, list):
                            feat_keys.update(feats)
            sent_grammar = list(feat_keys)
            sentence_grammar_cache[sent.id] = sent_grammar

        gfit = _grammar_fit(sent_grammar, grammar_exposure_map)
        diversity = 1.0 / (1.0 + (sent.times_shown or 0))
        freshness = _scaffold_freshness(word_metas, knowledge_map)
        source_bonus = 1.3 if sent.source in ("book", "corpus") else 1.0
        # Rescue sentences (recently shown but only option for a due word) get a
        # penalty so fresh sentences are preferred, but they still participate.
        rescue_penalty = 0.3 if sent.id in rescue_sentence_ids else 1.0
        # Boost sentences targeting never-reviewed acquiring words so they can
        # compete with multi-word FSRS sentences in the greedy selection.
        nr_boost = NEVER_REVIEWED_BOOST if (due_covered & boosted_acquiring_ids) else 1.0
        overdue_boost = _overdue_escalation(due_covered, overdue_days_map)
        score = (len(due_covered) ** 1.5) * dmq * gfit * diversity * freshness * source_bonus * rescue_penalty * nr_boost * overdue_boost

        candidates.append(SentenceCandidate(
            sentence_id=sent.id,
            sentence=sent,
            words_meta=word_metas,
            due_words_covered=due_covered,
            score=score,
        ))

    # 3. Greedy set cover with within-session scaffold diversity
    selected: list[SentenceCandidate] = []
    remaining_due = set(due_lemma_ids)
    session_scaffold_counts: dict[int, int] = {}

    while remaining_due and len(selected) < limit and candidates:
        for c in candidates:
            overlap = c.due_words_covered & remaining_due
            if not overlap:
                c.score = 0.0
                continue
            weakest = min(stability_map.get(lid, 0.0) for lid in overlap)
            scaffold_stabs = [w.stability for w in c.words_meta
                              if w.lemma_id and not w.is_due and w.stability is not None]
            dmq = _difficulty_match_quality(weakest, scaffold_stabs)
            gfit = _grammar_fit(sentence_grammar_cache.get(c.sentence_id, []), grammar_exposure_map)
            diversity = 1.0 / (1.0 + (c.sentence.times_shown or 0))
            freshness = _scaffold_freshness(c.words_meta, knowledge_map)
            source_bonus = 1.3 if c.sentence.source in ("book", "corpus") else 1.0

            # Within-session scaffold diversity: penalize reuse of scaffold words
            scaffold_ids = [w.lemma_id for w in c.words_meta
                            if w.lemma_id and not w.is_due and not w.is_function_word]
            if scaffold_ids and session_scaffold_counts:
                max_session_count = max(session_scaffold_counts.get(lid, 0) for lid in scaffold_ids)
                session_diversity = SESSION_SCAFFOLD_DECAY ** max_session_count
            else:
                session_diversity = 1.0

            rescue_penalty = 0.3 if c.sentence_id in rescue_sentence_ids else 1.0
            nr_boost = NEVER_REVIEWED_BOOST if (overlap & boosted_acquiring_ids) else 1.0
            overdue_boost = _overdue_escalation(overlap, overdue_days_map)
            c.score = (len(overlap) ** 1.5) * dmq * gfit * diversity * freshness * source_bonus * session_diversity * rescue_penalty * nr_boost * overdue_boost
            c.score_components = {
                "due_coverage": len(overlap),
                "difficulty_match": round(dmq, 2),
                "grammar_fit": round(gfit, 2),
                "diversity": round(diversity, 2),
                "freshness": round(freshness, 2),
                "source_bonus": source_bonus,
                "session_diversity": round(session_diversity, 2),
                "rescue": rescue_penalty < 1.0,
                "never_reviewed_boost": nr_boost,
                "overdue_boost": round(overdue_boost, 2),
            }

        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        if best.score <= 0:
            break

        selected.append(best)
        best.selection_reason = "greedy_cover"
        best.selection_order = len(selected)
        remaining_due -= best.due_words_covered
        candidates.remove(best)

        # Track scaffold words used in this session for diversity
        for w in best.words_meta:
            if w.lemma_id and not w.is_due and not w.is_function_word:
                session_scaffold_counts[w.lemma_id] = session_scaffold_counts.get(w.lemma_id, 0) + 1

        if log_events:
            div_metrics = compute_sentence_diversity_score(
                best.words_meta, knowledge_map, session_scaffold_counts
            )
            log_interaction(
                event="sentence_selected",
                session_id=session_id,
                sentence_id=best.sentence_id,
                selection_order=len(selected),
                score=round(best.score, 3),
                due_words_covered=len(best.due_words_covered),
                remaining_due=len(remaining_due),
                **div_metrics,
            )

    # Track covered
    covered_ids: set[int] = set()
    for c in selected:
        covered_ids |= c.due_words_covered

    logger.info(
        f"Session build: {len(selected)}/{limit} sentences selected, "
        f"{len(remaining_due)}/{len(due_lemma_ids)} words uncovered"
    )

    # Track pre-repetition count so on-demand generation uses the right budget
    base_item_count = len(selected)

    # 3b. Within-session repetition for acquisition words
    # Target MIN_ACQUISITION_EXPOSURES (3-4) per acquiring word
    acquiring_word_counts: dict[int, int] = {}
    for c in selected:
        for w in c.words_meta:
            if w.lemma_id and w.lemma_id in due_lemma_ids:
                k = knowledge_by_id.get(w.lemma_id)
                if k and k.knowledge_state == "acquiring":
                    acquiring_word_counts[w.lemma_id] = acquiring_word_counts.get(w.lemma_id, 0) + 1

    # Allow session to grow beyond limit to fit acquisition repetitions
    acq_extra_slots = sum(
        max(0, MIN_ACQUISITION_EXPOSURES - count)
        for count in acquiring_word_counts.values()
    )
    effective_limit = limit + min(acq_extra_slots, MAX_ACQUISITION_EXTRA_SLOTS)

    selected_ids = {c.sentence_id for c in selected}
    for target_count in range(2, MIN_ACQUISITION_EXPOSURES + 1):
        for acq_lid, count in list(acquiring_word_counts.items()):
            if len(selected) >= effective_limit:
                break
            if count >= target_count:
                continue
            extra = None
            for c in candidates:
                if c.sentence_id not in selected_ids and acq_lid in {w.lemma_id for w in c.words_meta}:
                    extra = c
                    break
            if extra:
                extra.selection_reason = "acquisition_repeat"
                selected.append(extra)
                selected_ids.add(extra.sentence_id)
                candidates.remove(extra)
                acquiring_word_counts[acq_lid] = count + 1

    # 4. Order: easy bookends, hard in middle
    ordered = _order_session(selected, stability_map)

    selected_sentence_ids = {c.sentence_id for c in ordered}

    # Note: mapping verification happens in warm_sentence_cache (background),
    # not here. Sentences already pass generation-time verification.
    # Running LLM verification in the request path caused 15-30s timeouts.

    # Load grammar features for selected sentences
    grammar_by_sentence: dict[int, list[str]] = {}
    if selected_sentence_ids:
        sgf_rows = (
            db.query(SentenceGrammarFeature, GrammarFeature.feature_key)
            .join(GrammarFeature)
            .filter(SentenceGrammarFeature.sentence_id.in_(selected_sentence_ids))
            .all()
        )
        for sgf, feature_key in sgf_rows:
            grammar_by_sentence.setdefault(sgf.sentence_id, []).append(feature_key)

        # Also derive from lemma tags for sentences without existing grammar features
        for sid in selected_sentence_ids:
            if sid in grammar_by_sentence:
                continue
            cand = next((c for c in ordered if c.sentence_id == sid), None)
            if cand:
                feature_keys: set[str] = set()
                for w in cand.words_meta:
                    if w.lemma_id:
                        lemma = lemma_map.get(w.lemma_id)
                        if lemma and lemma.grammar_features_json:
                            feats = parse_json_column(lemma.grammar_features_json, default=[])
                            if isinstance(feats, list):
                                feature_keys.update(feats)
                if feature_keys:
                    grammar_by_sentence[sid] = list(feature_keys)

    # Build response items (shown_at is set on review submission, not here)
    items: list[dict] = []
    for cand in ordered:
        sent = sentence_map[cand.sentence_id]

        primary_lid = sent.target_lemma_id
        if primary_lid not in due_lemma_ids and cand.due_words_covered:
            primary_lid = next(iter(cand.due_words_covered))

        primary_lemma = lemma_map.get(primary_lid)

        word_dicts = []
        for w in cand.words_meta:
            lemma = lemma_map.get(w.lemma_id) if w.lemma_id else None
            root_obj = lemma.root if lemma else None

            # Compute per-word tashkeel visibility.
            # Scaffold words (not being tested) fade at 30d; target/due words fade
            # at the user's configured threshold (default 90d) — they still need the crutch.
            if tashkeel_mode == "never":
                word_show_tashkeel = False
            elif tashkeel_mode == "fade":
                word_show_tashkeel = True
                fade_threshold = tashkeel_threshold if w.is_due else min(tashkeel_threshold / 3, 30.0)
                if w.stability is not None and w.stability >= fade_threshold:
                    word_show_tashkeel = False
            else:  # "always"
                word_show_tashkeel = True

            word_dicts.append({
                "lemma_id": w.lemma_id,
                "surface_form": w.surface_form,
                "gloss_en": w.gloss_en,
                "stability": w.stability,
                "is_due": w.is_due,
                "is_function_word": w.is_function_word,
                "knowledge_state": w.knowledge_state,
                "root": root_obj.root if root_obj else None,
                "root_meaning": root_obj.core_meaning_en if root_obj else None,
                "root_id": root_obj.root_id if root_obj else None,
                "frequency_rank": lemma.frequency_rank if lemma else None,
                "cefr_level": lemma.cefr_level if lemma else None,
                "grammar_tags": _compact_grammar_tags(lemma.grammar_features_json) if lemma else [],
                "show_tashkeel": word_show_tashkeel,
                "transliteration": transliterate_arabic(w.surface_form) if w.surface_form else None,
            })

        # Build selection_info for this item
        k = knowledge_by_id.get(primary_lid)
        if k and k.knowledge_state == "acquiring":
            word_reason = f"Acquiring (box {k.acquisition_box})"
        elif k:
            stab = stability_map.get(primary_lid)
            if stab is not None:
                if stab < 1:
                    stab_label = f"{max(1, round(stab * 24))}h"
                elif stab < 30:
                    stab_label = f"{round(stab)}d"
                else:
                    stab_label = f"{stab / 30:.1f}mo"
                word_reason = f"{k.knowledge_state.title()} (stability {stab_label})"
            else:
                word_reason = k.knowledge_state.title()
        else:
            word_reason = "New"

        items.append({
            "sentence_id": cand.sentence_id,
            "arabic_text": sent.arabic_text,
            "arabic_diacritized": sent.arabic_diacritized,
            "english_translation": sent.english_translation or "",
            "transliteration": sent.transliteration,
            "audio_url": sent.audio_url,
            "primary_lemma_id": primary_lid,
            "primary_lemma_ar": primary_lemma.lemma_ar if primary_lemma else "",
            "primary_gloss_en": primary_lemma.gloss_en if primary_lemma else "",
            "words": word_dicts,
            "grammar_features": grammar_by_sentence.get(cand.sentence_id, []),
            "selection_info": {
                "reason": cand.selection_reason,
                "order": cand.selection_order,
                "score": round(cand.score, 2),
                "word_reason": word_reason,
                "components": cand.score_components,
                "due_lemma_ids": sorted(cand.due_words_covered),
            },
        })

    db.commit()

    return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, items, limit, covered_ids, reintro_cards=reintro_cards, knowledge_by_id=knowledge_by_id, all_knowledge=all_knowledge, base_item_count=base_item_count, mode=mode)


MAX_REINTRO_PER_SESSION = 3
STRUGGLING_MIN_SEEN = 3


_GENERIC_ULK_SOURCES = {None, "study", "encountered", "auto_intro", "collateral", "leech_reintro"}

def _display_source(ulk, lemma) -> str | None:
    """Prefer ulk.source (how the word entered learning) over lemma.source (dictionary provenance)."""
    ulk_source = ulk.source if ulk else None
    return ulk_source if ulk_source not in _GENERIC_ULK_SOURCES else lemma.source


def _build_reintro_cards(
    db: Session,
    struggling_ids: set[int],
    limit: int = MAX_REINTRO_PER_SESSION,
) -> list[dict]:
    """Build rich introduction-style cards for struggling words."""
    if not struggling_ids:
        return []

    from app.services.word_selector import get_root_family

    lemmas = (
        db.query(Lemma)
        .options(joinedload(Lemma.root))
        .filter(Lemma.lemma_id.in_(struggling_ids), Lemma.gates_completed_at.isnot(None))
        .all()
    )

    # Sort by times_seen descending (most seen but still failing = highest priority)
    ulk_map: dict[int, UserLemmaKnowledge] = {}
    for k in db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id.in_(struggling_ids)
    ).all():
        ulk_map[k.lemma_id] = k

    lemmas.sort(
        key=lambda l: (ulk_map.get(l.lemma_id) and ulk_map[l.lemma_id].times_seen) or 0,
        reverse=True,
    )

    # Ensure enrichment exists for intro card words (forms, etymology, memory hooks).
    # Trigger background enrichment for any words missing data so cards are info-dense.
    needs_enrichment = [
        l for l in lemmas[:limit]
        if not l.forms_json or not l.etymology_json or not l.memory_hooks_json
    ]
    if needs_enrichment:
        import threading
        from app.services.lemma_enrichment import enrich_lemmas_batch
        from app.services.memory_hooks import generate_memory_hooks

        enrich_ids = [l.lemma_id for l in needs_enrichment if not l.forms_json or not l.etymology_json]
        hooks_ids = [l.lemma_id for l in needs_enrichment if not l.memory_hooks_json]

        if enrich_ids:
            threading.Thread(
                target=enrich_lemmas_batch, args=(enrich_ids,), daemon=True
            ).start()
        for lid in hooks_ids:
            threading.Thread(
                target=generate_memory_hooks, args=(lid,), daemon=True
            ).start()

        logger.info(
            f"Triggered background enrichment for intro cards: "
            f"{len(enrich_ids)} forms/etymology, {len(hooks_ids)} memory hooks"
        )


    cards = []
    for lemma in lemmas[:limit]:
        k = ulk_map.get(lemma.lemma_id)
        root_obj = lemma.root

        # Get root family with knowledge states
        family = []
        if root_obj:
            family = get_root_family(db, root_obj.root_id)

        card = {
            "lemma_id": lemma.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "gloss_en": lemma.gloss_en,
            "pos": lemma.pos,
            "transliteration": lemma.transliteration_ala_lc,
            "root": root_obj.root if root_obj else None,
            "root_meaning": root_obj.core_meaning_en if root_obj else None,
            "root_id": root_obj.root_id if root_obj else None,
            "forms_json": lemma.forms_json,
            "forms_translit": transliterate_forms(lemma.forms_json) if lemma.forms_json else None,
            "example_ar": lemma.example_ar,
            "example_en": lemma.example_en,
            "audio_url": lemma.audio_url,
            "grammar_features": lemma.grammar_features_json or [],
            "grammar_details": [],
            "times_seen": k.times_seen if k else 0,
            "root_family": family,
            "memory_hooks": lemma.memory_hooks_json,
            "etymology": lemma.etymology_json,
            "wazn": lemma.wazn,
            "wazn_meaning": lemma.wazn_meaning,
            "source": _display_source(k, lemma),
        }
        cards.append(card)

    return cards


RESCUE_MIN_SEEN = 4
RESCUE_MAX_ACCURACY = 0.50
RESCUE_COOLDOWN_DAYS = 7


INTRO_CARDS_BASE = 5
INTRO_CARDS_MAX = 10


def _dynamic_intro_cap(db: Session) -> int:
    """Scale intro card cap based on un-introed acquiring word backlog.

    Base 5, +1 per 10 un-introed words, capped at 10.
    After a large import, temporarily shows more intros to clear the backlog.
    """
    unintro_count = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            (UserLemmaKnowledge.times_seen == 0) | (UserLemmaKnowledge.times_seen.is_(None)),
            UserLemmaKnowledge.experiment_intro_shown_at.is_(None),
        )
        .count()
    )
    return min(INTRO_CARDS_MAX, INTRO_CARDS_BASE + unintro_count // 10)


def _build_intro_cards(
    db: Session,
    knowledge_by_id: dict[int, UserLemmaKnowledge],
    covered_ids: set[int],
) -> list[dict]:
    """Build intro cards for new and struggling acquiring words in this session.

    Two categories:
    1. New words (times_seen == 0) — first-encounter teaching card
    2. Rescue words (acquiring, ≥4 reviews, <50% accuracy) — re-teaching
       for stuck words, with a 7-day cooldown between rescue cards.

    Both limited to words covered by sentences in this session.
    Cap scales dynamically with un-introed backlog (5 base, up to 10).
    """
    now = datetime.now(timezone.utc)
    cooldown_cutoff = now - timedelta(days=RESCUE_COOLDOWN_DAYS)

    # Build canonical resolution map for variant checking
    canonical_knowledge: dict[int, str] = {}  # canonical_id → knowledge_state
    for lid, ulk in knowledge_by_id.items():
        canonical_knowledge[lid] = ulk.knowledge_state or "new"

    card_ids = set()
    for lid, ulk in knowledge_by_id.items():
        if ulk.knowledge_state != "acquiring":
            continue

        # Skip variants whose canonical is already known/learning
        lemma = db.get(Lemma, lid)
        if lemma and lemma.canonical_lemma_id and lemma.canonical_lemma_id != lid:
            canon_ulk = knowledge_by_id.get(lemma.canonical_lemma_id)
            if canon_ulk and canon_ulk.knowledge_state in ("known", "learning"):
                continue

        # Category 1: New words (never reviewed)
        if (
            (ulk.times_seen or 0) == 0
            and ulk.experiment_intro_shown_at is None
            # Skip intro cards for words already familiar from encounters
            and (ulk.total_encounters or 0) < 5
        ):
            # Skip if user already demonstrated recognition (e.g. Quran promotion
            # means they recognized the word in 3+ understood verses)
            if (ulk.times_correct or 0) > 0 or ulk.source == "quran":
                continue
            card_ids.add(lid)
            continue

        # Category 2: Rescue cards for stuck words
        times_seen = ulk.times_seen or 0
        times_correct = ulk.times_correct or 0
        if times_seen >= RESCUE_MIN_SEEN:
            accuracy = times_correct / times_seen
            if accuracy < RESCUE_MAX_ACCURACY:
                # Only show if not recently shown (cooldown)
                if ulk.experiment_intro_shown_at is None:
                    card_ids.add(lid)
                else:
                    shown_at = ulk.experiment_intro_shown_at
                    if shown_at.tzinfo is None:
                        shown_at = shown_at.replace(tzinfo=timezone.utc)
                    if shown_at < cooldown_cutoff:
                        card_ids.add(lid)

    card_ids &= covered_ids

    if not card_ids:
        return []

    cap = _dynamic_intro_cap(db)
    return _build_reintro_cards(db, card_ids, limit=min(len(card_ids), cap))


MAX_ON_DEMAND_PER_SESSION = 10


def _find_pregenerated_sentences_for_words(
    db: Session,
    target_lemma_ids: set[int],
    stability_map: dict[int, float],
    knowledge_by_id: dict[int, UserLemmaKnowledge],
    all_knowledge: list,
    limit: int,
    mode: str = "reading",
) -> list[dict]:
    """Find pre-generated sentences for newly introduced words (no LLM calls).

    Used during fill phase to populate sessions from the existing sentence pool.
    All sentence generation happens in background (warm_sentence_cache / cron).
    """
    import logging
    from sqlalchemy import or_

    logger = logging.getLogger(__name__)

    if not target_lemma_ids:
        return []

    # Query sentences containing target words
    sentence_words = (
        db.query(SentenceWord)
        .filter(SentenceWord.lemma_id.in_(target_lemma_ids))
        .all()
    )
    sentence_ids_with_target = {sw.sentence_id for sw in sentence_words}
    if not sentence_ids_with_target:
        logger.info(f"Pre-gen fill: {len(target_lemma_ids)} words, 0 sentences found")
        return []

    # Recency filter (same cutoffs as build_session)
    now = datetime.now(timezone.utc)
    cutoff_understood = now - timedelta(days=1)
    cutoff_partial = now - timedelta(hours=4)
    cutoff_no_idea = now - timedelta(minutes=30)

    if mode == "listening":
        comp_col = Sentence.last_listening_comprehension
        shown_col = Sentence.last_listening_shown_at
    else:
        comp_col = Sentence.last_reading_comprehension
        shown_col = Sentence.last_reading_shown_at

    sentences = (
        db.query(Sentence)
        .filter(
            Sentence.id.in_(sentence_ids_with_target),
            Sentence.is_active == True,  # noqa: E712
            or_(
                shown_col.is_(None),
                (comp_col == "understood") & (shown_col < cutoff_understood),
                (comp_col == "partial") & (shown_col < cutoff_partial),
                (comp_col == "no_idea") & (shown_col < cutoff_no_idea),
                (comp_col.is_(None)) & (shown_col < cutoff_understood),
            ),
        )
        .all()
    )

    # Rescue pass for words with no fresh sentences
    fresh_sent_ids = {s.id for s in sentences}
    words_with_fresh = {
        sw.lemma_id for sw in sentence_words
        if sw.sentence_id in fresh_sent_ids and sw.lemma_id in target_lemma_ids
    }
    rescue_sentence_ids: set[int] = set()
    words_needing_rescue = target_lemma_ids - words_with_fresh
    if words_needing_rescue:
        rescue_sw_rows = [
            sw for sw in sentence_words
            if sw.lemma_id in words_needing_rescue and sw.sentence_id not in fresh_sent_ids
        ]
        potential_rescue_ids = {sw.sentence_id for sw in rescue_sw_rows}
        if potential_rescue_ids:
            rescue_sents = (
                db.query(Sentence)
                .filter(Sentence.id.in_(potential_rescue_ids), Sentence.is_active == True)  # noqa: E712
                .all()
            )
            if rescue_sents:
                rescue_sentence_ids = {s.id for s in rescue_sents}
                sentences.extend(rescue_sents)

    if not sentences:
        logger.info(f"Pre-gen fill: {len(target_lemma_ids)} words, 0 sentences after filters")
        return []

    sentence_map = {s.id: s for s in sentences}

    # Load all sentence words for these sentences
    all_sw = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id.in_(sentence_map.keys()))
        .order_by(SentenceWord.sentence_id, SentenceWord.position)
        .all()
    )
    sw_by_sentence: dict[int, list[SentenceWord]] = {}
    for sw in all_sw:
        sw_by_sentence.setdefault(sw.sentence_id, []).append(sw)

    # Load lemma info
    all_lemma_ids = {sw.lemma_id for sw in all_sw if sw.lemma_id}
    all_lemma_ids |= target_lemma_ids
    lemmas = (
        db.query(Lemma).options(joinedload(Lemma.root))
        .filter(Lemma.lemma_id.in_(all_lemma_ids))
        .all()
    ) if all_lemma_ids else []
    lemma_map = {l.lemma_id: l for l in lemmas}
    knowledge_map = {k.lemma_id: k for k in all_knowledge} if all_knowledge else {}

    # Build candidates with comprehensibility gate
    candidates: list[SentenceCandidate] = []
    for sent in sentences:
        sws = sw_by_sentence.get(sent.id, [])
        due_covered: set[int] = set()
        word_metas: list[WordMeta] = []
        scaffold_stabilities: list[float] = []

        for sw in sws:
            lemma = lemma_map.get(sw.lemma_id) if sw.lemma_id else None
            stab = stability_map.get(sw.lemma_id, 0.0) if sw.lemma_id else None
            is_due = sw.lemma_id in target_lemma_ids if sw.lemma_id else False

            k_state = "new"
            if sw.lemma_id:
                k = knowledge_map.get(sw.lemma_id) or knowledge_by_id.get(sw.lemma_id)
                if k:
                    k_state = k.knowledge_state or "new"

            bare = strip_diacritics(sw.surface_form)
            is_func = _is_function_word(bare)
            gloss = lemma.gloss_en if lemma else FUNCTION_WORD_GLOSSES.get(bare)
            if not gloss:
                bare_norm = normalize_alef(strip_tatweel(bare))
                gloss = FUNCTION_WORD_GLOSSES.get(bare_norm)
            wm = WordMeta(
                lemma_id=sw.lemma_id,
                surface_form=sw.surface_form,
                gloss_en=gloss,
                stability=stab,
                is_due=is_due,
                is_function_word=is_func,
                knowledge_state=k_state,
            )
            word_metas.append(wm)

            if sw.lemma_id and is_due:
                due_covered.add(sw.lemma_id)
            elif sw.lemma_id and stab is not None:
                scaffold_stabilities.append(stab)

        if not due_covered:
            continue

        # Comprehensibility gate (same logic as main gate).
        scaffold = [w for w in word_metas if not w.is_function_word and not w.is_due]
        total_scaffold = len(scaffold)
        known_scaffold = sum(
            1 for w in scaffold
            if w.knowledge_state in ("known", "learning", "lapsed", "acquiring")
        )
        if total_scaffold > 0 and known_scaffold / total_scaffold < COMPREHENSIBILITY_THRESHOLD:
            continue

        weakest = min(stability_map.get(lid, 0.0) for lid in due_covered)
        dmq = _difficulty_match_quality(weakest, scaffold_stabilities)
        diversity = 1.0 / (1.0 + (sent.times_shown or 0))
        freshness = _scaffold_freshness(word_metas, knowledge_map)
        rescue_penalty = 0.3 if sent.id in rescue_sentence_ids else 1.0
        score = (len(due_covered) ** 1.5) * dmq * diversity * freshness * rescue_penalty

        candidates.append(SentenceCandidate(
            sentence_id=sent.id,
            sentence=sent,
            words_meta=word_metas,
            due_words_covered=due_covered,
            score=score,
        ))

    # Greedy set cover
    selected: list[SentenceCandidate] = []
    remaining = set(target_lemma_ids)
    while remaining and len(selected) < limit and candidates:
        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        if best.score <= 0:
            break
        selected.append(best)
        remaining -= best.due_words_covered
        candidates.remove(best)

    logger.info(
        f"Pre-gen fill: {len(target_lemma_ids)} words, "
        f"{len(sentence_map)} sentences found, {len(selected)} selected"
    )

    # Build item dicts
    items: list[dict] = []
    for cand in selected:
        sent = sentence_map[cand.sentence_id]
        primary_lid = sent.target_lemma_id
        if primary_lid not in target_lemma_ids and cand.due_words_covered:
            primary_lid = next(iter(cand.due_words_covered))
        primary_lemma = lemma_map.get(primary_lid)

        word_dicts = []
        for w in cand.words_meta:
            lemma = lemma_map.get(w.lemma_id) if w.lemma_id else None
            root_obj = lemma.root if lemma else None
            word_dicts.append({
                "lemma_id": w.lemma_id,
                "surface_form": w.surface_form,
                "gloss_en": w.gloss_en,
                "stability": w.stability,
                "is_due": w.is_due,
                "is_function_word": w.is_function_word,
                "knowledge_state": w.knowledge_state,
                "root": root_obj.root if root_obj else None,
                "root_meaning": root_obj.core_meaning_en if root_obj else None,
                "root_id": root_obj.root_id if root_obj else None,
                "frequency_rank": lemma.frequency_rank if lemma else None,
                "cefr_level": lemma.cefr_level if lemma else None,
                "grammar_tags": _compact_grammar_tags(lemma.grammar_features_json) if lemma else [],
                "show_tashkeel": True,
                "transliteration": transliterate_arabic(w.surface_form) if w.surface_form else None,
            })

        items.append({
            "sentence_id": cand.sentence_id,
            "arabic_text": sent.arabic_text,
            "arabic_diacritized": sent.arabic_diacritized,
            "english_translation": sent.english_translation or "",
            "transliteration": sent.transliteration,
            "audio_url": sent.audio_url,
            "primary_lemma_id": primary_lid,
            "primary_lemma_ar": primary_lemma.lemma_ar if primary_lemma else "",
            "primary_gloss_en": primary_lemma.gloss_en if primary_lemma else "",
            "words": word_dicts,
            "grammar_features": [],
            "selection_info": {
                "reason": "fill_pregen",
                "order": len(items) + 1,
                "score": round(cand.score, 2),
                "word_reason": "Auto-introduced (pre-generated)",
                "components": {},
                "due_lemma_ids": sorted(cand.due_words_covered),
            },
        })

    return items


def _with_fallbacks(
    db: Session,
    session_id: str,
    due_lemma_ids: set[int],
    stability_map: dict[int, float],
    total_due: int,
    items: list[dict],
    limit: int,
    covered_ids: set[int] | None = None,
    reintro_cards: list[dict] | None = None,
    knowledge_by_id: dict[int, UserLemmaKnowledge] | None = None,
    all_knowledge: list | None = None,
    base_item_count: int | None = None,
    mode: str = "reading",
) -> dict:
    """Fill undersized sessions using pre-generated sentences (DB queries only).

    No LLM calls — all sentence generation happens in background via
    warm_sentence_cache() and the cron. This keeps session build <1s.
    """
    import logging
    logger = logging.getLogger(__name__)

    if covered_ids is None:
        covered_ids = set()
    if knowledge_by_id is None:
        knowledge_by_id = {}

    uncovered = due_lemma_ids - covered_ids
    if uncovered:
        logger.info(f"{len(uncovered)} uncovered words — warm_sentence_cache will generate for next session")

    # Fill phase — ALWAYS runs when session is undersized.
    # Uses pre-generated sentences only (fast DB queries, no LLM).
    if len(items) < limit:
        logger.info(
            f"Fill phase: session has {len(items)}/{limit} items"
        )
        try:
            now = datetime.now(timezone.utc)
            fill_ids = _auto_introduce_words(
                db, limit - len(items), knowledge_by_id, now,
                skip_material_gen=True,
            )
            if fill_ids:
                logger.info(f"Fill phase: introduced {len(fill_ids)} new words")
                for lid in fill_ids:
                    stability_map[lid] = 0.1
                    ulk = (
                        db.query(UserLemmaKnowledge)
                        .filter(UserLemmaKnowledge.lemma_id == lid)
                        .first()
                    )
                    if ulk:
                        knowledge_by_id[lid] = ulk

                fill_due = set(fill_ids)
                remaining_cap = limit - len(items)
                if remaining_cap > 0:
                    fill_items = _find_pregenerated_sentences_for_words(
                        db, fill_due, stability_map, knowledge_by_id,
                        all_knowledge or [], remaining_cap, mode=mode,
                    )
                    for fi in fill_items:
                        if fi.get("selection_info"):
                            fi["selection_info"]["reason"] = "fill_intro"
                            fi["selection_info"]["word_reason"] = "Auto-introduced to fill session"
                    items.extend(fill_items)
                    for fi in fill_items:
                        covered_ids.add(fi["primary_lemma_id"])
        except Exception:
            logger.exception("Fill phase failed, continuing with existing items")
            db.rollback()

    # Almost-due backfill: if session is still severely undersized after fill,
    # pull in words closest to their due date so the learner isn't stuck with
    # a near-empty session.
    if len(items) < MIN_SESSION_SENTENCES:
        try:
            from app.services.cohort_service import get_focus_cohort
            cohort = get_focus_cohort(db)
            now = datetime.now(timezone.utc)
            already_covered = {item.get("primary_lemma_id") for item in items if item.get("primary_lemma_id")}
            almost_due: list[tuple[int, datetime]] = []
            for k in (all_knowledge or []):
                if k.lemma_id in already_covered or k.lemma_id in due_lemma_ids:
                    continue
                if k.knowledge_state in ("known", "learning", "lapsed") and k.fsrs_card_json:
                    due_dt = _get_due_dt(k)
                    if due_dt:
                        almost_due.append((k.lemma_id, due_dt))
                elif k.knowledge_state == "acquiring" and k.acquisition_next_due:
                    acq_due = k.acquisition_next_due
                    if acq_due.tzinfo is None:
                        acq_due = acq_due.replace(tzinfo=timezone.utc)
                    almost_due.append((k.lemma_id, acq_due))
            almost_due.sort(key=lambda x: x[1])
            preview_ids = {lid for lid, _ in almost_due[:limit * 3]} & cohort
            if preview_ids:
                remaining_cap = limit - len(items)
                for lid in preview_ids:
                    if lid not in stability_map:
                        k = knowledge_by_id.get(lid)
                        if k:
                            stability_map[lid] = _get_stability(k) if k.fsrs_card_json else 0.1
                fill_items = _find_pregenerated_sentences_for_words(
                    db, preview_ids, stability_map, knowledge_by_id,
                    all_knowledge or [], remaining_cap, mode=mode,
                )
                for fi in fill_items:
                    if fi.get("selection_info"):
                        fi["selection_info"]["reason"] = "almost_due_fill"
                        fi["selection_info"]["word_reason"] = "Almost due — filling undersized session"
                items.extend(fill_items)
                for fi in fill_items:
                    covered_ids.add(fi["primary_lemma_id"])
                if fill_items:
                    logger.info(f"Almost-due backfill: added {len(fill_items)} sentences (session was {len(items) - len(fill_items)}/{limit})")
        except Exception:
            logger.exception("Almost-due backfill failed")
            db.rollback()

    # Check for un-introduced grammar features in session sentences
    sentence_ids_in_session = [item["sentence_id"] for item in items if item.get("sentence_id")]
    grammar_intro_needed: list[str] = []
    if sentence_ids_in_session:
        from app.services.grammar_lesson_service import get_unintroduced_features_for_session
        grammar_intro_needed = get_unintroduced_features_for_session(db, sentence_ids_in_session)

    # Also check for confused features that need resurfacing
    grammar_refresher_needed: list[str] = []
    from app.services.grammar_lesson_service import get_confused_features
    confused = get_confused_features(db)
    grammar_refresher_needed = [f["feature_key"] for f in confused]

    # Build intro cards for new words and rescue cards for stuck words,
    # limited to words covered by sentences in this session.
    experiment_intro_cards = _build_intro_cards(
        db, knowledge_by_id, covered_ids
    )

    return {
        "session_id": session_id,
        "items": items,
        "total_due_words": total_due,
        "covered_due_words": len(covered_ids),
        "intro_candidates": [],  # deprecated: auto-introduction via sentences now
        "reintro_cards": reintro_cards or [],
        "experiment_intro_cards": experiment_intro_cards,
        "grammar_intro_needed": grammar_intro_needed,
        "grammar_refresher_needed": grammar_refresher_needed,
    }


def _order_session(
    selected: list[SentenceCandidate],
    stability_map: dict[int, float],
) -> list[SentenceCandidate]:
    """Order sentences: easy bookends, hard in the middle."""
    if len(selected) <= 2:
        return selected

    def min_due_stability(c: SentenceCandidate) -> float:
        due_stabs = [stability_map.get(lid, 0.0) for lid in c.due_words_covered]
        return min(due_stabs) if due_stabs else 0.0

    sorted_by_difficulty = sorted(selected, key=min_due_stability, reverse=True)

    start = [sorted_by_difficulty[0]]
    end = [sorted_by_difficulty[1]] if len(sorted_by_difficulty) > 1 else []
    middle = sorted_by_difficulty[2:]

    return start + middle + end


