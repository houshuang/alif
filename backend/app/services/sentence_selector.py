"""Sentence-centric session assembly.

Selects a review session of sentences that maximally cover due words,
ordered for good learning flow (easy -> hard -> easy).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from app.services.fsrs_service import parse_json_column

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
    lookup_lemma_id,
    strip_diacritics,
)

# Acquisition repetition: each acquiring word should appear this many times in a session
MIN_ACQUISITION_EXPOSURES = 4
MAX_ACQUISITION_EXTRA_SLOTS = 15  # max extra cards beyond session limit for repetitions
MAX_AUTO_INTRO_PER_SESSION = 10  # cap new words per single auto-intro call
AUTO_INTRO_ACCURACY_FLOOR = 0.70  # pause introduction if recent accuracy below this
INTRO_RESERVE_FRACTION = 0.2  # fraction of session slots reserved for new word introductions
SESSION_SCAFFOLD_DECAY = 0.5  # per-appearance decay for scaffold words already in session


def _intro_slots_for_accuracy(accuracy: float) -> int:
    """Return how many words to auto-introduce based on recent session accuracy.

    Replaces the binary pause/continue logic with a graduated ramp:
    - <70%: 0 (struggling, don't add new words)
    - 70-85%: 4 (doing okay, slow introduction)
    - 85-92%: 7 (doing well, moderate introduction)
    - >=92%: MAX_AUTO_INTRO_PER_SESSION (cruising, full speed)
    """
    if accuracy < 0.70:
        return 0
    if accuracy < 0.85:
        return 4
    if accuracy < 0.92:
        return 7
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
    import logging
    logger = logging.getLogger(__name__)

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
    skip_on_demand: bool = False,
) -> dict:
    """Assemble a sentence-based review session.

    Returns a dict matching SentenceSessionOut schema:
    {session_id, items, total_due_words, covered_due_words}
    """
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Load tashkeel settings
    tashkeel_settings = db.query(LearnerSettings).first()
    tashkeel_mode = (tashkeel_settings.tashkeel_mode if tashkeel_settings else None) or "always"
    tashkeel_threshold = (tashkeel_settings.tashkeel_stability_threshold if tashkeel_settings else None) or 30.0

    # Comprehension-aware recency cutoffs
    # Failed sentences can be re-shown quickly so learner gets a positive review,
    # then ideally sees the same word in a different sentence next time.
    cutoff_understood = now - timedelta(days=4)
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

    for k in all_knowledge:
        knowledge_by_id[k.lemma_id] = k

        if k.knowledge_state == "encountered":
            continue  # passive vocab — not due, not scheduled
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
        elif k.fsrs_card_json:
            stability_map[k.lemma_id] = _get_stability(k)
            due_dt = _get_due_dt(k)
            if due_dt and due_dt <= now:
                due_lemma_ids.add(k.lemma_id)

    # Filter through focus cohort — only review words in the active cohort
    from app.services.cohort_service import get_focus_cohort
    cohort = get_focus_cohort(db)
    due_lemma_ids &= cohort

    # Auto-introduce new words: reserve slots even when due queue is full
    # This ensures vocabulary growth doesn't stall when reviews pile up.
    accuracy_slots = _get_accuracy_intro_slots(db, now)
    if accuracy_slots > 0:
        reserved_intro = max(1, int(limit * INTRO_RESERVE_FRACTION))
        intro_slots = min(accuracy_slots, reserved_intro)
    else:
        intro_slots = 0
    undersized_slots = max(0, limit - len(due_lemma_ids))
    slots_for_intro = max(intro_slots, undersized_slots)
    auto_introduced_ids = _auto_introduce_words(
        db, slots_for_intro, knowledge_by_id, now,
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

    # Remove struggling words from sentence selection pool
    due_lemma_ids -= struggling_ids

    total_due = len(due_lemma_ids) + len(struggling_ids)

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
        }

    # 2. Fetch candidate sentences containing at least one due word
    sentence_words = (
        db.query(SentenceWord)
        .filter(SentenceWord.lemma_id.in_(due_lemma_ids))
        .all()
    )

    sentence_ids_with_due = {sw.sentence_id for sw in sentence_words}
    if not sentence_ids_with_due:
        return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, [], limit, reintro_cards=reintro_cards, knowledge_by_id=knowledge_by_id, all_knowledge=all_knowledge, skip_on_demand=skip_on_demand)

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
        return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, [], limit, reintro_cards=reintro_cards, knowledge_by_id=knowledge_by_id, all_knowledge=all_knowledge, skip_on_demand=skip_on_demand)

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
    # Uses SAVEPOINT so a DB lock doesn't crash the whole session build.
    words_missing_lemma = [sw for sw in all_sw if sw.lemma_id is None]
    if words_missing_lemma:
        lookup_lemmas = db.query(Lemma).all()
        lemma_lookup = build_lemma_lookup(lookup_lemmas) if lookup_lemmas else {}
        backfilled = 0
        for sw in words_missing_lemma:
            lemma_id = lookup_lemma_id(sw.surface_form, lemma_lookup)
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
    variant_to_canonical: dict[int, int] = {}
    for l in lemmas:
        if l.canonical_lemma_id:
            variant_to_canonical[l.lemma_id] = l.canonical_lemma_id

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
            is_due = effective_id in due_lemma_ids if effective_id else False

            k_state = "new"
            if effective_id:
                k = knowledge_map.get(effective_id)
                if k:
                    k_state = k.knowledge_state or "new"

            bare = strip_diacritics(sw.surface_form)
            is_func = _is_function_word(bare)
            gloss = lemma.gloss_en if lemma else FUNCTION_WORD_GLOSSES.get(bare)
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
            elif effective_id and stab is not None:
                scaffold_stabilities.append(stab)

        if not due_covered:
            continue

        # Comprehensibility gate: skip sentences where <60% of scaffold words are known.
        # Scaffold = non-function, non-due words (including unmapped words with lemma_id=None).
        # "encountered" does NOT count — the learner has only seen the word, never studied it.
        # "acquiring" only counts if past box 1 (stability >= 0.5 = reviewed at least once).
        scaffold = [w for w in word_metas if not w.is_function_word and not w.is_due]
        total_scaffold = len(scaffold)
        known_scaffold = sum(
            1 for w in scaffold
            if (
                w.knowledge_state in ("known", "learning", "lapsed")
                or (w.knowledge_state == "acquiring" and (w.stability or 0) >= 0.5)
            )
        )
        if total_scaffold > 0 and known_scaffold / total_scaffold < 0.6:
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
        source_bonus = 1.3 if sent.source == "book" else 1.0
        # Rescue sentences (recently shown but only option for a due word) get a
        # penalty so fresh sentences are preferred, but they still participate.
        rescue_penalty = 0.3 if sent.id in rescue_sentence_ids else 1.0
        score = (len(due_covered) ** 1.5) * dmq * gfit * diversity * freshness * source_bonus * rescue_penalty

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
            source_bonus = 1.3 if c.sentence.source == "book" else 1.0

            # Within-session scaffold diversity: penalize reuse of scaffold words
            scaffold_ids = [w.lemma_id for w in c.words_meta
                            if w.lemma_id and not w.is_due and not w.is_function_word]
            if scaffold_ids and session_scaffold_counts:
                max_session_count = max(session_scaffold_counts.get(lid, 0) for lid in scaffold_ids)
                session_diversity = SESSION_SCAFFOLD_DECAY ** max_session_count
            else:
                session_diversity = 1.0

            rescue_penalty = 0.3 if c.sentence_id in rescue_sentence_ids else 1.0
            c.score = (len(overlap) ** 1.5) * dmq * gfit * diversity * freshness * source_bonus * session_diversity * rescue_penalty
            c.score_components = {
                "due_coverage": len(overlap),
                "difficulty_match": round(dmq, 2),
                "grammar_fit": round(gfit, 2),
                "diversity": round(diversity, 2),
                "freshness": round(freshness, 2),
                "source_bonus": source_bonus,
                "session_diversity": round(session_diversity, 2),
                "rescue": rescue_penalty < 1.0,
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

    # Load grammar features for selected sentences
    selected_sentence_ids = {c.sentence_id for c in ordered}
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

            # Compute per-word tashkeel visibility
            if tashkeel_mode == "never":
                word_show_tashkeel = False
            elif tashkeel_mode == "fade":
                word_show_tashkeel = True
                if w.stability is not None and w.stability >= tashkeel_threshold:
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

    return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, items, limit, covered_ids, reintro_cards=reintro_cards, knowledge_by_id=knowledge_by_id, all_knowledge=all_knowledge, base_item_count=base_item_count, skip_on_demand=skip_on_demand)


MAX_REINTRO_PER_SESSION = 3
STRUGGLING_MIN_SEEN = 3


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
        .filter(Lemma.lemma_id.in_(struggling_ids))
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
            "example_ar": lemma.example_ar,
            "example_en": lemma.example_en,
            "audio_url": lemma.audio_url,
            "grammar_features": lemma.grammar_features_json or [],
            "grammar_details": [],
            "times_seen": k.times_seen if k else 0,
            "root_family": family,
            "memory_hooks": lemma.memory_hooks_json,
            "etymology": lemma.etymology_json,
        }
        cards.append(card)

    return cards


MAX_ON_DEMAND_PER_SESSION = 10


def _generate_on_demand(
    db: Session,
    uncovered_ids: set[int],
    stability_map: dict[int, float],
    max_items: int,
) -> list[dict]:
    """Generate sentences on-demand for due words with no comprehensible sentences.

    When 2+ words are uncovered, tries multi-target generation first (grouping
    up to 4 words per sentence). Falls back to single-target for remaining.
    """
    import logging
    from app.services.sentence_generator import (
        generate_validated_sentence,
        generate_validated_sentences_multi_target,
        group_words_for_multi_target,
        GenerationError,
    )
    from app.services.material_generator import store_multi_target_sentence
    from app.services.sentence_validator import (
        build_comprehensive_lemma_lookup,
        build_lemma_lookup,
        map_tokens_to_lemmas,
        strip_diacritics,
        tokenize_display,
    )

    logger = logging.getLogger(__name__)
    cap = max_items  # caller controls the cap
    items: list[dict] = []

    # Build known words list from current ULK
    # Active words (prompt for GPT): known/learning/lapsed/acquiring
    # Encountered words: included in validator so GPT isn't rejected for using them,
    # but NOT in the GPT prompt (avoids overwhelming the learner).
    # The comprehensibility gate (≥60% known content) already limits difficulty.
    all_ulks = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state.in_(
            ["known", "learning", "lapsed", "acquiring", "encountered"]
        ))
        .all()
    )
    known_ulks = [u for u in all_ulks if u.knowledge_state != "encountered"]
    all_lemma_ids = {k.lemma_id for k in all_ulks}
    known_lemma_ids = {k.lemma_id for k in known_ulks}
    all_lemmas = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.in_(all_lemma_ids))
        .all()
    ) if all_lemma_ids else []

    # GPT prompt gets only active words; validator gets all (including encountered)
    known_words = [
        {"arabic": lem.lemma_ar, "english": lem.gloss_en or "", "lemma_id": lem.lemma_id, "pos": lem.pos or ""}
        for lem in all_lemmas if lem.lemma_id in known_lemma_ids
    ]
    all_words_for_validation = [
        {"arabic": lem.lemma_ar, "english": lem.gloss_en or "", "lemma_id": lem.lemma_id, "pos": lem.pos or ""}
        for lem in all_lemmas
    ]
    lemma_lookup = build_lemma_lookup(all_lemmas) if all_lemmas else {}
    mapping_lookup = build_comprehensive_lemma_lookup(db)
    lemma_map = {lem.lemma_id: lem for lem in all_lemmas}

    # Also load target lemmas that may not be in known set
    target_lemmas = (
        db.query(Lemma).options(joinedload(Lemma.root))
        .filter(Lemma.lemma_id.in_(uncovered_ids))
        .all()
    )
    target_map = {lem.lemma_id: lem for lem in target_lemmas}

    # Build ULK lookup for knowledge_state
    ulk_by_lemma: dict[int, UserLemmaKnowledge] = {k.lemma_id: k for k in known_ulks}

    still_uncovered = set(uncovered_ids)
    generated_count = 0

    # --- Phase 1: Multi-target generation (parallel across groups) ---
    multi_groups = []
    if len(still_uncovered) >= 2:
        word_dicts_for_grouping = []
        for lid in still_uncovered:
            lem = target_map.get(lid)
            if lem:
                word_dicts_for_grouping.append({
                    "lemma_id": lid,
                    "lemma_ar": lem.lemma_ar,
                    "gloss_en": lem.gloss_en or "",
                    "root_id": lem.root_id,
                })
        multi_groups = group_words_for_multi_target(word_dicts_for_grouping)

    # Run all multi-target groups in parallel
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _gen_multi(group):
        try:
            return group, generate_validated_sentences_multi_target(
                target_words=group,
                known_words=known_words,
                count=len(group),
                difficulty_hint="beginner",
                max_words=12,
                validation_words=all_words_for_validation,
                lemma_lookup=lemma_lookup,
            )
        except Exception as e:
            logger.warning(f"Multi-target generation failed: {e}")
            return group, []

    def _gen_single(lid, lem):
        try:
            return lid, generate_validated_sentence(
                target_arabic=lem.lemma_ar,
                target_translation=lem.gloss_en or "",
                known_words=known_words,
                difficulty_hint="beginner",
                max_words=10,
                validation_words=all_words_for_validation,
                lemma_lookup=lemma_lookup,
            )
        except GenerationError:
            logger.warning(f"On-demand generation failed for lemma {lid}")
            return lid, None
        except Exception:
            logger.exception(f"Unexpected error in on-demand generation for lemma {lid}")
            return lid, None

    # Submit all LLM calls in parallel (multi-target groups + single-target words)
    with ThreadPoolExecutor(max_workers=8) as executor:
        multi_futures = {}
        for group in multi_groups[:cap]:
            fut = executor.submit(_gen_multi, group)
            multi_futures[fut] = group

        # Collect multi-target results first to know which words are still uncovered
        for fut in as_completed(multi_futures):
            group, multi_results = fut.result()
            target_bares = {strip_diacritics(tw["lemma_ar"]): tw["lemma_id"] for tw in group}
            for mres in multi_results:
                if generated_count >= cap:
                    break

                sent = store_multi_target_sentence(db, mres, mapping_lookup, target_bares)
                if not sent:
                    continue

                primary_lemma = target_map.get(mres.primary_target_lemma_id)
                sws = db.query(SentenceWord).filter(SentenceWord.sentence_id == sent.id).order_by(SentenceWord.position).all()
                word_dicts = []
                for sw in sws:
                    mapped_lemma = lemma_map.get(sw.lemma_id) or target_map.get(sw.lemma_id)
                    root_obj = mapped_lemma.root if mapped_lemma and hasattr(mapped_lemma, 'root') else None
                    bare = strip_diacritics(sw.surface_form)
                    k_state = "new"
                    ulk = ulk_by_lemma.get(sw.lemma_id)
                    if ulk:
                        k_state = ulk.knowledge_state or "new"

                    word_dicts.append({
                        "lemma_id": sw.lemma_id,
                        "surface_form": sw.surface_form,
                        "gloss_en": mapped_lemma.gloss_en if mapped_lemma else FUNCTION_WORD_GLOSSES.get(bare),
                        "stability": stability_map.get(sw.lemma_id, 0.0) if sw.lemma_id else None,
                        "is_due": sw.lemma_id in uncovered_ids if sw.lemma_id else False,
                        "is_function_word": _is_function_word(bare),
                        "knowledge_state": k_state,
                        "root": root_obj.root if root_obj else None,
                        "root_meaning": root_obj.core_meaning_en if root_obj else None,
                        "root_id": root_obj.root_id if root_obj else None,
                        "frequency_rank": mapped_lemma.frequency_rank if mapped_lemma else None,
                        "cefr_level": mapped_lemma.cefr_level if mapped_lemma else None,
                        "grammar_tags": [],
                    })

                items.append({
                    "sentence_id": sent.id,
                    "arabic_text": sent.arabic_text,
                    "arabic_diacritized": sent.arabic_diacritized,
                    "english_translation": sent.english_translation or "",
                    "transliteration": sent.transliteration,
                    "audio_url": None,
                    "primary_lemma_id": mres.primary_target_lemma_id,
                    "primary_lemma_ar": primary_lemma.lemma_ar if primary_lemma else "",
                    "primary_gloss_en": primary_lemma.gloss_en if primary_lemma else "",
                    "words": word_dicts,
                    "grammar_features": [],
                    "selection_info": {
                        "reason": "on_demand",
                        "word_reason": "Generated on-demand (no existing sentence)",
                    },
                })
                generated_count += 1
                for found_lid in mres.target_lemma_ids:
                    still_uncovered.discard(found_lid)

        # Phase 2: Single-target for remaining uncovered words (all in parallel)
        single_futures = {}
        for lid in list(still_uncovered):
            if generated_count + len(single_futures) >= cap:
                break
            lemma = target_map.get(lid)
            if not lemma:
                continue
            fut = executor.submit(_gen_single, lid, lemma)
            single_futures[fut] = lid

        for fut in as_completed(single_futures):
            lid, result = fut.result()
            if result is None or generated_count >= cap:
                continue

            lemma = target_map.get(lid)
            if not lemma:
                continue

            sent = Sentence(
                arabic_text=result.arabic,
                arabic_diacritized=result.arabic,
                english_translation=result.english,
                transliteration=result.transliteration,
                source="llm",
                target_lemma_id=lid,
                created_at=datetime.now(timezone.utc),
            )
            db.add(sent)
            db.flush()

            tokens = tokenize_display(result.arabic)
            mappings = map_tokens_to_lemmas(
                tokens=tokens,
                lemma_lookup=mapping_lookup,
                target_lemma_id=lid,
                target_bare=strip_diacritics(lemma.lemma_ar),
            )
            unmapped = [m.surface_form for m in mappings if m.lemma_id is None]
            if unmapped:
                logger.warning(f"Skipping on-demand sentence with unmapped words: {unmapped}")
                db.delete(sent)
                continue

            word_dicts = []
            for m in mappings:
                sw = SentenceWord(
                    sentence_id=sent.id,
                    position=m.position,
                    surface_form=m.surface_form,
                    lemma_id=m.lemma_id,
                    is_target_word=m.is_target,
                )
                db.add(sw)

                mapped_lemma = lemma_map.get(m.lemma_id) or target_map.get(m.lemma_id)
                root_obj = mapped_lemma.root if mapped_lemma and hasattr(mapped_lemma, 'root') else None
                bare = strip_diacritics(m.surface_form)

                k_state = "new"
                if m.lemma_id:
                    ulk = ulk_by_lemma.get(m.lemma_id)
                    if ulk:
                        k_state = ulk.knowledge_state or "new"

                word_dicts.append({
                    "lemma_id": m.lemma_id,
                    "surface_form": m.surface_form,
                    "gloss_en": mapped_lemma.gloss_en if mapped_lemma else FUNCTION_WORD_GLOSSES.get(bare),
                    "stability": stability_map.get(m.lemma_id, 0.0) if m.lemma_id else None,
                    "is_due": m.lemma_id in uncovered_ids if m.lemma_id else False,
                    "is_function_word": _is_function_word(bare),
                    "knowledge_state": k_state,
                    "root": root_obj.root if root_obj else None,
                    "root_meaning": root_obj.core_meaning_en if root_obj else None,
                    "root_id": root_obj.root_id if root_obj else None,
                    "frequency_rank": mapped_lemma.frequency_rank if mapped_lemma else None,
                    "cefr_level": mapped_lemma.cefr_level if mapped_lemma else None,
                    "grammar_tags": [],
                })

            items.append({
                "sentence_id": sent.id,
                "arabic_text": sent.arabic_text,
                "arabic_diacritized": sent.arabic_diacritized,
                "english_translation": sent.english_translation or "",
                "transliteration": sent.transliteration,
                "audio_url": None,
                "primary_lemma_id": lid,
                "primary_lemma_ar": lemma.lemma_ar,
                "primary_gloss_en": lemma.gloss_en or "",
                "words": word_dicts,
                "grammar_features": [],
                "selection_info": {
                    "reason": "on_demand",
                    "word_reason": "Generated on-demand (no existing sentence)",
                },
            })
            generated_count += 1

    if generated_count > 0:
        db.flush()

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
    skip_on_demand: bool = False,
) -> dict:
    """Generate on-demand sentences for uncovered due words, then fill if undersized.

    When skip_on_demand=True, skips LLM generation (used for fast synchronous
    session loads — generation runs in background instead).
    """
    import logging
    logger = logging.getLogger(__name__)

    if covered_ids is None:
        covered_ids = set()
    if knowledge_by_id is None:
        knowledge_by_id = {}

    if not skip_on_demand:
        # Phase 1: On-demand generation for uncovered due words
        # Use base_item_count (pre-acquisition-repetition) for budget so that
        # acquisition repetition doesn't block on-demand generation for uncovered words
        uncovered = due_lemma_ids - covered_ids
        budget_basis = base_item_count if base_item_count is not None else len(items)
        on_demand_budget = max(0, limit - budget_basis)
        if uncovered and on_demand_budget > 0:
            try:
                generated_items = _generate_on_demand(db, uncovered, stability_map, on_demand_budget)
                items.extend(generated_items)
                for item in generated_items:
                    covered_ids.add(item["primary_lemma_id"])
            except Exception:
                logger.exception("On-demand generation failed, continuing with existing sentences")
                db.rollback()

        # Phase 2: Fill phase — if session is still undersized, introduce more words
        if len(items) < limit:
            try:
                now = datetime.now(timezone.utc)
                fill_ids = _auto_introduce_words(
                    db, limit - len(items), knowledge_by_id, now,
                    skip_material_gen=True,
                )
                if fill_ids:
                    logger.info(f"Fill phase: introduced {len(fill_ids)} new words to fill session")
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
                        fill_items = _generate_on_demand(
                            db, fill_due, stability_map, remaining_cap
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
    else:
        uncovered = due_lemma_ids - covered_ids
        if uncovered:
            logger.info(f"Skipping on-demand generation for {len(uncovered)} uncovered words (fast mode)")

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

    return {
        "session_id": session_id,
        "items": items,
        "total_due_words": total_due,
        "covered_due_words": len(covered_ids),
        "intro_candidates": [],  # deprecated: auto-introduction via sentences now
        "reintro_cards": reintro_cards or [],
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


