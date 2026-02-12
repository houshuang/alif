"""Sentence-centric session assembly.

Selects a review session of sentences that maximally cover due words,
ordered for good learning flow (easy -> hard -> easy).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from sqlalchemy.orm import joinedload

from app.services.fsrs_service import parse_json_column

from app.models import (
    GrammarFeature,
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
    build_lemma_lookup,
    lookup_lemma_id,
    strip_diacritics,
)

# Acquisition repetition: each acquiring word should appear this many times in a session
MIN_ACQUISITION_EXPOSURES = 4
MAX_ACQUISITION_EXTRA_SLOTS = 8  # max extra cards beyond session limit for repetitions
MAX_AUTO_INTRO_PER_SESSION = 3  # new words auto-introduced per session
AUTO_INTRO_ACCURACY_FLOOR = 0.70  # pause introduction if recent accuracy below this
MAX_ACQUIRING_WORDS = 8  # don't auto-introduce if already this many acquiring


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


FRESHNESS_BASELINE = 8  # reviews at which penalty starts


def _scaffold_freshness(
    words_meta: list[WordMeta],
    knowledge_map: dict[int, "UserLemmaKnowledge"],
) -> float:
    """Penalize sentences whose scaffold words are over-reviewed.

    For each non-due, non-function scaffold word, compute
    penalty = min(1.0, FRESHNESS_BASELINE / max(times_seen, 1)).
    Aggregate via geometric mean, floored at 0.3.

    Effect: scaffold word seen 8× → 1.0 (no penalty),
    16× → 0.5, 80× → 0.1 (floored to 0.3 at sentence level).
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
    return max(0.3, geo_mean)


def _auto_introduce_words(
    db: Session,
    acquiring_count: int,
    knowledge_by_id: dict[int, UserLemmaKnowledge],
    now: datetime,
) -> list[int]:
    """Auto-introduce new words into the session if acquiring count is low.

    Picks highest-frequency encountered words and starts acquisition.
    Returns list of newly introduced lemma_ids.
    """
    import logging
    logger = logging.getLogger(__name__)

    if acquiring_count >= MAX_ACQUIRING_WORDS:
        return []

    # Check recent accuracy — pause introduction if struggling
    recent_reviews = (
        db.query(ReviewLog)
        .filter(ReviewLog.reviewed_at >= now - timedelta(days=2))
        .all()
    )
    if len(recent_reviews) >= 10:
        correct = sum(1 for r in recent_reviews if r.rating >= 3)
        accuracy = correct / len(recent_reviews)
        if accuracy < AUTO_INTRO_ACCURACY_FLOOR:
            logger.info(
                f"Auto-intro paused: recent accuracy {accuracy:.0%} < {AUTO_INTRO_ACCURACY_FLOOR:.0%}"
            )
            return []

    slots = min(MAX_AUTO_INTRO_PER_SESSION, MAX_ACQUIRING_WORDS - acquiring_count)
    if slots <= 0:
        return []

    from app.services.word_selector import select_next_words, introduce_word
    from app.services.material_generator import generate_material_for_word

    candidates = select_next_words(db, count=slots)
    if not candidates:
        return []

    introduced_ids: list[int] = []
    for cand in candidates[:slots]:
        lid = cand["lemma_id"]
        try:
            result = introduce_word(db, lid, source="auto_intro", due_immediately=True)
            if result.get("already_known"):
                continue
            introduced_ids.append(lid)
            logger.info(f"Auto-introduced word {lid}: {cand.get('lemma_ar', '?')}")

            # Trigger sentence generation for this word (uses its own DB session)
            try:
                generate_material_for_word(lid, needed=3)
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
        )

    return introduced_ids


def build_session(
    db: Session,
    limit: int = 10,
    mode: str = "reading",
    log_events: bool = True,
) -> dict:
    """Assemble a sentence-based review session.

    Returns a dict matching SentenceSessionOut schema:
    {session_id, items, total_due_words, covered_due_words}
    """
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    # Comprehension-aware recency cutoffs
    cutoff_understood = now - timedelta(days=7)
    cutoff_partial = now - timedelta(days=2)
    cutoff_grammar_confused = now - timedelta(days=1)
    cutoff_no_idea = now - timedelta(hours=4)

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

    # Auto-introduce new words if acquiring count is low
    acquiring_count = sum(
        1 for k in all_knowledge if k.knowledge_state == "acquiring"
    )
    auto_introduced_ids = _auto_introduce_words(
        db, acquiring_count, knowledge_by_id, now
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

    if not due_lemma_ids and not struggling_ids:
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
        return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, [], limit, reintro_cards=reintro_cards)

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
                (comp_col == "grammar_confused") & (shown_col < cutoff_grammar_confused),
                (comp_col == "no_idea") & (shown_col < cutoff_no_idea),
                (comp_col.is_(None)) & (shown_col < cutoff_understood),
            ),
        )
        .all()
    )

    if not sentences:
        return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, [], limit, reintro_cards=reintro_cards)

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
            db.flush()

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
            is_func = bare in FUNCTION_WORDS
            gloss = lemma.gloss_en if lemma else FUNCTION_WORD_GLOSSES.get(bare)
            wm = WordMeta(
                lemma_id=effective_id,
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

        # Comprehensibility gate: skip sentences where <70% of content words are known
        # "encountered" counts as passive vocabulary — learner has seen the word
        total_content = sum(1 for w in word_metas if not w.is_function_word and w.lemma_id)
        known_content = sum(
            1 for w in word_metas if not w.is_function_word and w.lemma_id
            and w.knowledge_state in ("known", "learning", "lapsed", "acquiring", "encountered")
        )
        if total_content > 0 and known_content / total_content < 0.7:
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
        score = (len(due_covered) ** 1.5) * dmq * gfit * diversity * freshness

        candidates.append(SentenceCandidate(
            sentence_id=sent.id,
            sentence=sent,
            words_meta=word_metas,
            due_words_covered=due_covered,
            score=score,
        ))

    # 3. Greedy set cover
    selected: list[SentenceCandidate] = []
    remaining_due = set(due_lemma_ids)

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
            c.score = (len(overlap) ** 1.5) * dmq * gfit * diversity * freshness

        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        if best.score <= 0:
            break

        selected.append(best)
        remaining_due -= best.due_words_covered
        candidates.remove(best)

        if log_events:
            log_interaction(
                event="sentence_selected",
                session_id=session_id,
                sentence_id=best.sentence_id,
                selection_order=len(selected),
                score=round(best.score, 3),
                due_words_covered=len(best.due_words_covered),
                remaining_due=len(remaining_due),
            )

    # Track covered
    covered_ids: set[int] = set()
    for c in selected:
        covered_ids |= c.due_words_covered

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
            "grammar_features": grammar_by_sentence.get(cand.sentence_id, []),
        })

    db.commit()

    return _with_fallbacks(db, session_id, due_lemma_ids, stability_map, total_due, items, limit, covered_ids, reintro_cards=reintro_cards)


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
        }
        cards.append(card)

    return cards


MAX_ON_DEMAND_PER_SESSION = 5


def _generate_on_demand(
    db: Session,
    uncovered_ids: set[int],
    stability_map: dict[int, float],
    max_items: int,
) -> list[dict]:
    """Generate sentences on-demand for due words with no comprehensible sentences.

    Calls LLM synchronously (capped at MAX_ON_DEMAND_PER_SESSION) to avoid
    showing word-only fallback cards.
    """
    import logging
    from app.services.sentence_generator import generate_validated_sentence, GenerationError
    from app.services.sentence_validator import (
        build_lemma_lookup,
        map_tokens_to_lemmas,
        strip_diacritics,
        tokenize,
    )

    logger = logging.getLogger(__name__)
    cap = min(max_items, MAX_ON_DEMAND_PER_SESSION)
    items: list[dict] = []

    # Build known words list from current ULK (only genuinely known/learning/acquiring)
    known_ulks = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state.in_(
            ["known", "learning", "lapsed", "acquiring"]
        ))
        .all()
    )
    known_lemma_ids = {k.lemma_id for k in known_ulks}
    known_lemmas = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.in_(known_lemma_ids))
        .all()
    ) if known_lemma_ids else []

    known_words = [
        {"arabic": lem.lemma_ar, "english": lem.gloss_en or "", "lemma_id": lem.lemma_id}
        for lem in known_lemmas
    ]
    lemma_lookup = build_lemma_lookup(known_lemmas) if known_lemmas else {}
    lemma_map = {lem.lemma_id: lem for lem in known_lemmas}

    # Also load target lemmas that may not be in known set
    target_lemmas = (
        db.query(Lemma).options(joinedload(Lemma.root))
        .filter(Lemma.lemma_id.in_(uncovered_ids))
        .all()
    )
    target_map = {lem.lemma_id: lem for lem in target_lemmas}

    generated_count = 0
    for lid in list(uncovered_ids):
        if generated_count >= cap:
            break

        lemma = target_map.get(lid)
        if not lemma:
            continue

        try:
            result = generate_validated_sentence(
                target_arabic=lemma.lemma_ar,
                target_translation=lemma.gloss_en or "",
                known_words=known_words,
                difficulty_hint="beginner",
                max_words=10,
            )
        except GenerationError:
            logger.warning(f"On-demand generation failed for lemma {lid}")
            continue
        except Exception:
            logger.exception(f"Unexpected error in on-demand generation for lemma {lid}")
            continue

        # Store sentence in DB
        sent = Sentence(
            arabic_text=result.arabic,
            arabic_diacritized=result.arabic,
            english_translation=result.english,
            transliteration=result.transliteration,
            source="llm",
            target_lemma_id=lid,
        )
        db.add(sent)
        db.flush()

        tokens = tokenize(result.arabic)
        mappings = map_tokens_to_lemmas(
            tokens=tokens,
            lemma_lookup=lemma_lookup,
            target_lemma_id=lid,
            target_bare=strip_diacritics(lemma.lemma_ar),
        )
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
            k_obj = None
            if m.lemma_id:
                for k in known_ulks:
                    if k.lemma_id == m.lemma_id:
                        k_state = k.knowledge_state or "new"
                        k_obj = k
                        break

            word_dicts.append({
                "lemma_id": m.lemma_id,
                "surface_form": m.surface_form,
                "gloss_en": mapped_lemma.gloss_en if mapped_lemma else FUNCTION_WORD_GLOSSES.get(bare),
                "stability": stability_map.get(m.lemma_id, 0.0) if m.lemma_id else None,
                "is_due": m.lemma_id in uncovered_ids if m.lemma_id else False,
                "is_function_word": bare in FUNCTION_WORDS,
                "knowledge_state": k_state,
                "root": root_obj.root if root_obj else None,
                "root_meaning": root_obj.core_meaning_en if root_obj else None,
                "root_id": root_obj.root_id if root_obj else None,
                "frequency_rank": mapped_lemma.frequency_rank if mapped_lemma else None,
                "cefr_level": mapped_lemma.cefr_level if mapped_lemma else None,
                "grammar_tags": [],
            })

        root_obj = lemma.root
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
) -> dict:
    """Generate on-demand sentences for uncovered due words."""
    if covered_ids is None:
        covered_ids = set()

    uncovered = due_lemma_ids - covered_ids
    if uncovered and len(items) < limit:
        generated_items = _generate_on_demand(db, uncovered, stability_map, limit - len(items))
        items.extend(generated_items)
        for item in generated_items:
            covered_ids.add(item["primary_lemma_id"])

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


