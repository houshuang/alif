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
    build_lemma_lookup,
    lookup_lemma_id,
    strip_diacritics,
)


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

    # 1. Fetch all due words (exclude suspended)
    all_knowledge = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.fsrs_card_json.isnot(None),
            UserLemmaKnowledge.knowledge_state != "suspended",
        )
        .all()
    )

    due_lemma_ids: set[int] = set()
    stability_map: dict[int, float] = {}
    knowledge_by_id: dict[int, UserLemmaKnowledge] = {}

    for k in all_knowledge:
        stability_map[k.lemma_id] = _get_stability(k)
        knowledge_by_id[k.lemma_id] = k
        due_dt = _get_due_dt(k)
        if due_dt and due_dt <= now:
            due_lemma_ids.add(k.lemma_id)

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
            stab = stability_map.get(sw.lemma_id, 0.0) if sw.lemma_id else None
            is_due = sw.lemma_id in due_lemma_ids if sw.lemma_id else False

            k_state = "new"
            if sw.lemma_id:
                k = knowledge_map.get(sw.lemma_id)
                if k:
                    k_state = k.knowledge_state or "new"

            bare = strip_diacritics(sw.surface_form)
            wm = WordMeta(
                lemma_id=sw.lemma_id,
                surface_form=sw.surface_form,
                gloss_en=lemma.gloss_en if lemma else None,
                stability=stab,
                is_due=is_due,
                is_function_word=bare in FUNCTION_WORDS,
                knowledge_state=k_state,
            )
            word_metas.append(wm)

            if sw.lemma_id and is_due:
                due_covered.add(sw.lemma_id)
            elif sw.lemma_id and stab is not None:
                scaffold_stabilities.append(stab)

        if not due_covered:
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
    """Add word-only fallback items for uncovered due words."""
    if covered_ids is None:
        covered_ids = set()

    uncovered = due_lemma_ids - covered_ids
    # Fetch knowledge states for uncovered words
    k_states: dict[int, str] = {}
    if uncovered:
        for uk in db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id.in_(uncovered)).all():
            k_states[uk.lemma_id] = uk.knowledge_state or "new"

    for lid in uncovered:
        if len(items) >= limit:
            break
        lemma = db.query(Lemma).options(joinedload(Lemma.root)).filter(Lemma.lemma_id == lid).first()
        if lemma is None:
            continue
        root_obj = lemma.root
        items.append({
            "sentence_id": None,
            "arabic_text": lemma.lemma_ar,
            "arabic_diacritized": lemma.lemma_ar,
            "english_translation": lemma.gloss_en or "",
            "transliteration": lemma.transliteration_ala_lc,
            "primary_lemma_id": lid,
            "primary_lemma_ar": lemma.lemma_ar,
            "primary_gloss_en": lemma.gloss_en or "",
            "words": [{
                "lemma_id": lid,
                "surface_form": lemma.lemma_ar,
                "gloss_en": lemma.gloss_en,
                "stability": stability_map.get(lid, 0.0),
                "is_due": True,
                "is_function_word": False,
                "knowledge_state": k_states.get(lid, "new"),
                "root": root_obj.root if root_obj else None,
                "root_meaning": root_obj.core_meaning_en if root_obj else None,
                "root_id": root_obj.root_id if root_obj else None,
            }],
        })
        covered_ids.add(lid)

    intro_candidates = _get_intro_candidates(db, items)

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
        "intro_candidates": intro_candidates,
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


MAX_INTRO_PER_SESSION = 2


def _get_intro_candidates(
    db: Session,
    items: list[dict],
) -> list[dict]:
    """Suggest new words to introduce during a review session.

    Returns up to MAX_INTRO_PER_SESSION candidates with insertion positions.
    User controls acceptance via Learn/Skip buttons on the card.
    """
    if len(items) == 0:
        return []

    from app.services.word_selector import select_next_words, get_root_family

    candidates = select_next_words(db, count=MAX_INTRO_PER_SESSION)
    if not candidates:
        return []

    result = []
    # Insert at positions 4 and 8 (0-indexed: after 3rd and 7th review items)
    insert_positions = [3, 7]
    for i, cand in enumerate(candidates[:MAX_INTRO_PER_SESSION]):
        pos = insert_positions[i] if i < len(insert_positions) else len(items) - 1
        pos = min(pos, len(items))
        root_family = get_root_family(db, cand["root_id"]) if cand.get("root_id") else []
        result.append({
            "lemma_id": cand["lemma_id"],
            "lemma_ar": cand["lemma_ar"],
            "gloss_en": cand["gloss_en"],
            "pos": cand.get("pos"),
            "transliteration": cand.get("transliteration"),
            "root": cand.get("root"),
            "root_meaning": cand.get("root_meaning"),
            "root_id": cand.get("root_id"),
            "insert_at": pos,
            "forms_json": cand.get("forms_json"),
            "example_ar": cand.get("example_ar"),
            "example_en": cand.get("example_en"),
            "audio_url": cand.get("audio_url"),
            "grammar_features": cand.get("grammar_features", []),
            "grammar_details": [],
            "root_family": root_family,
        })

    return result
