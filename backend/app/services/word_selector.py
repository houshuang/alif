"""New word selection algorithm.

Picks optimal words to introduce next based on:
- Frequency rank (40%) — high-frequency words first
- Root familiarity (30%) — prefer words whose root is partially known
- Pattern coverage (10%) — fill morphological gaps
- Recency buffer (20%) — avoid words too similar to recently introduced ones

Also handles word introduction: creating FSRS cards, tracking root familiarity,
and scheduling initial reinforcement.
"""

import json as _json
import math
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import Root, Lemma, UserLemmaKnowledge, ReviewLog, Sentence, StoryWord, Story
from app.services.grammar_service import grammar_pattern_score


# Semantic categories that should NOT be introduced together
AVOID_SAME_SESSION = {
    "color", "number", "day", "month", "body_part",
    "family_member", "direction",
}

MAX_NEW_PER_SESSION = 5
DEFAULT_BATCH_SIZE = 3

# Gloss prefixes that indicate Wiktionary reference entries, not real words
_SKIP_GLOSS_PREFIXES = (
    "alternative form of",
    "alternative spelling of",
    "active participle of",
    "passive participle of",
    "accusative plural of",
    "genitive plural of",
    "nominative plural of",
    "accusative singular of",
    "genitive singular of",
    "judeo-arabic spelling of",
    "verbal noun of",
)

# Arabic Unicode block: U+0600–U+06FF, plus supplemental U+0750–U+077F and Arabic Presentation Forms
_NON_ARABIC_RE = re.compile(r"[^\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF\s\u0640]")


def _is_noise_lemma(lemma) -> bool:
    """Return True if this lemma is a Wiktionary reference entry or non-Arabic."""
    gloss = (lemma.gloss_en or "").lower().strip()
    if any(gloss.startswith(prefix) for prefix in _SKIP_GLOSS_PREFIXES):
        return True
    bare = lemma.lemma_ar_bare or ""
    if bare and _NON_ARABIC_RE.search(bare):
        return True
    return False


def _get_recently_failed_roots(db: Session) -> set[int]:
    """Get root_ids that have a sibling which failed (rating=1) in the last 7 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    failed_lemma_ids = (
        db.query(ReviewLog.lemma_id)
        .filter(
            ReviewLog.rating == 1,
            ReviewLog.reviewed_at >= cutoff,
        )
        .distinct()
        .all()
    )
    if not failed_lemma_ids:
        return set()

    failed_ids = {r[0] for r in failed_lemma_ids}
    roots = (
        db.query(Lemma.root_id)
        .filter(Lemma.lemma_id.in_(failed_ids), Lemma.root_id.isnot(None))
        .distinct()
        .all()
    )
    return {r[0] for r in roots}


def _active_story_lemma_ids(db: Session) -> dict[int, str]:
    """Get lemma_ids of unknown words in active stories → story title."""
    rows = (
        db.query(StoryWord.lemma_id, Story.title_en, Story.title_ar)
        .join(Story, StoryWord.story_id == Story.id)
        .filter(
            Story.status == "active",
            StoryWord.lemma_id.isnot(None),
            StoryWord.is_function_word == False,
            StoryWord.is_known_at_creation == False,
        )
        .all()
    )
    result: dict[int, str] = {}
    for lemma_id, title_en, title_ar in rows:
        if lemma_id not in result:
            result[lemma_id] = title_en or title_ar or "Story"
    return result


def _book_page_bonus(db: Session) -> dict[int, float]:
    """Get lemma_id → page bonus for words in active book stories.

    Earlier pages get higher bonus: page 1 → 1.0, page 2 → 0.8, page 3 → 0.6, etc.
    Minimum bonus is 0.2.
    """
    rows = (
        db.query(StoryWord.lemma_id, StoryWord.page_number)
        .join(Story, StoryWord.story_id == Story.id)
        .filter(
            Story.status == "active",
            Story.source == "book_ocr",
            StoryWord.lemma_id.isnot(None),
            StoryWord.page_number.isnot(None),
            StoryWord.is_function_word == False,
        )
        .all()
    )
    result: dict[int, float] = {}
    for lemma_id, page in rows:
        bonus = max(0.2, 1.0 - (page - 1) * 0.2)
        if lemma_id not in result or bonus > result[lemma_id]:
            result[lemma_id] = bonus
    return result


def _frequency_score(frequency_rank: Optional[int], max_rank: int = 50000) -> float:
    """Higher score for lower (more frequent) rank. Log scale."""
    if frequency_rank is None or frequency_rank <= 0:
        return 0.3  # unknown frequency gets moderate score
    return 1.0 / math.log2(frequency_rank + 2)


def _root_familiarity_score(
    db: Session, root_id: Optional[int]
) -> tuple[float, int, int]:
    """Score how familiar the root is. Returns (score, known_count, total_count).

    Highest score when root is partially known (some siblings learned).
    Zero score for completely unknown roots (no siblings known).
    Lower score for fully known roots (all siblings already learned).
    """
    if root_id is None:
        return 0.0, 0, 0

    total = (
        db.query(func.count(Lemma.lemma_id))
        .filter(Lemma.root_id == root_id)
        .scalar() or 0
    )
    if total <= 1:
        return 0.0, 0, total

    known = (
        db.query(func.count(UserLemmaKnowledge.id))
        .join(Lemma)
        .filter(
            Lemma.root_id == root_id,
            UserLemmaKnowledge.knowledge_state.in_(["known", "learning", "acquiring", "lapsed"]),
        )
        .scalar() or 0
    )

    if known == 0:
        return 0.0, 0, total
    if known >= total:
        return 0.1, known, total

    # Peak score when ~30-60% of root family is known
    ratio = known / total
    return ratio * (1.0 - ratio) * 4.0, known, total


def _days_since_introduced(db: Session, root_id: Optional[int]) -> float:
    """Average days since sibling words were introduced. Used for spacing."""
    if root_id is None:
        return 999.0

    latest = (
        db.query(func.max(UserLemmaKnowledge.introduced_at))
        .join(Lemma)
        .filter(Lemma.root_id == root_id)
        .scalar()
    )
    if latest is None:
        return 999.0

    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - latest
    return delta.total_seconds() / 86400


def get_root_family(db: Session, root_id: int) -> list[dict]:
    """Get all words from a root with their knowledge state.

    Deduplicates al- prefixed forms when a bare form exists in the same root.
    """
    lemmas = (
        db.query(Lemma)
        .filter(Lemma.root_id == root_id, Lemma.canonical_lemma_id.is_(None))
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .all()
    )
    # Collect bare forms to detect al- duplicates
    bare_forms = {l.lemma_ar_bare for l in lemmas if l.lemma_ar_bare and not l.lemma_ar_bare.startswith("ال")}

    result = []
    for lemma in lemmas:
        bare = lemma.lemma_ar_bare or ""
        # Skip al- form if bare counterpart exists in same root
        if bare.startswith("ال") and bare[2:] in bare_forms:
            continue
        knowledge = lemma.knowledge
        result.append({
            "lemma_id": lemma.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "lemma_ar_bare": lemma.lemma_ar_bare,
            "gloss_en": lemma.gloss_en,
            "pos": lemma.pos,
            "transliteration": lemma.transliteration_ala_lc,
            "state": knowledge.knowledge_state if knowledge else "unknown",
        })
    return result


def _root_familiarity_score_batch(
    root_id: Optional[int],
    root_total_counts: dict[int, int],
    root_known_counts: dict[int, int],
) -> tuple[float, int, int]:
    """Batch version of _root_familiarity_score using pre-fetched counts."""
    if root_id is None:
        return 0.0, 0, 0
    total = root_total_counts.get(root_id, 0)
    if total <= 1:
        return 0.0, 0, total
    known = root_known_counts.get(root_id, 0)
    if known == 0:
        return 0.0, 0, total
    if known >= total:
        return 0.1, known, total
    ratio = known / total
    return ratio * (1.0 - ratio) * 4.0, known, total


def _days_since_introduced_batch(
    root_id: Optional[int],
    root_latest_intro: dict[int, Optional[datetime]],
    now: datetime,
) -> float:
    """Batch version of _days_since_introduced using pre-fetched dates."""
    if root_id is None:
        return 999.0
    latest = root_latest_intro.get(root_id)
    if latest is None:
        return 999.0
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return (now - latest).total_seconds() / 86400


def _grammar_pattern_score_batch(
    grammar_features: Optional[list[str]],
    unlocked_set: set[str],
    exposure_map: dict,
) -> float:
    """Batch version of grammar_pattern_score using pre-fetched data."""
    if not grammar_features:
        return 0.1
    from app.services.grammar_service import compute_comfort
    scores = []
    for key in grammar_features:
        if key not in unlocked_set:
            continue
        exp = exposure_map.get(key)
        if exp is None:
            scores.append(1.0)
        else:
            comfort = compute_comfort(exp.times_seen, exp.times_correct, exp.last_seen_at)
            scores.append(max(1.0 - comfort, 0.1))
    if not scores:
        return 0.1
    return sum(scores) / len(scores)


def select_next_words(
    db: Session,
    count: int = DEFAULT_BATCH_SIZE,
    exclude_lemma_ids: Optional[list[int]] = None,
    domain: Optional[str] = None,
) -> list[dict]:
    """Select the best words to introduce next.

    Returns a list of word dicts with scoring breakdown, sorted by score descending.
    """
    exclude = set(exclude_lemma_ids or [])

    # Get lemma_ids that are already introduced (have FSRS cards or are acquiring/learning/known)
    # Exclude encountered-only — those ARE candidates
    introduced_ids = set()
    encountered_ids = set()
    encountered_query = (
        db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state)
        .all()
    )
    for lid, state in encountered_query:
        if state == "encountered":
            encountered_ids.add(lid)
        else:
            introduced_ids.add(lid)

    # Candidates: no ULK at all, OR ULK with knowledge_state="encountered"
    candidates = (
        db.query(Lemma)
        .filter(
            Lemma.canonical_lemma_id.is_(None),
            Lemma.lemma_id.notin_(introduced_ids) if introduced_ids else True,
            Lemma.lemma_id.notin_(exclude) if exclude else True,
        )
        .all()
    )

    candidates = [c for c in candidates if not _is_noise_lemma(c)]

    if not candidates:
        return []

    # Topic-aware filtering: prefer words from the active domain
    if domain:
        domain_candidates = [c for c in candidates if c.thematic_domain == domain]
        if domain_candidates:
            candidates = domain_candidates
        # else: fall back to all candidates

    # Root-sibling interference guard: skip words whose root siblings failed in last 7d
    recently_failed_roots = _get_recently_failed_roots(db)
    if recently_failed_roots:
        candidates = [
            c for c in candidates
            if c.root_id not in recently_failed_roots or c.lemma_id in encountered_ids
        ]

    story_lemmas = _active_story_lemma_ids(db)
    book_page_bonuses = _book_page_bonus(db)

    # --- Batch pre-fetch for scoring ---
    root_ids = {c.root_id for c in candidates if c.root_id}

    # Root familiarity: total lemma count per root
    root_total_counts = dict(
        db.query(Lemma.root_id, func.count(Lemma.lemma_id))
        .filter(Lemma.root_id.in_(root_ids))
        .group_by(Lemma.root_id)
        .all()
    ) if root_ids else {}

    # Root familiarity: known lemma count per root
    root_known_counts = dict(
        db.query(Lemma.root_id, func.count(UserLemmaKnowledge.id))
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(
            Lemma.root_id.in_(root_ids),
            UserLemmaKnowledge.knowledge_state.in_(["known", "learning", "acquiring", "lapsed"]),
        )
        .group_by(Lemma.root_id)
        .all()
    ) if root_ids else {}

    # Recency: latest introduction date per root
    root_latest_intro = dict(
        db.query(Lemma.root_id, func.max(UserLemmaKnowledge.introduced_at))
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(Lemma.root_id.in_(root_ids))
        .group_by(Lemma.root_id)
        .all()
    ) if root_ids else {}

    now = datetime.now(timezone.utc)

    # Grammar: get unlocked features once and batch-fetch exposure records
    from app.services.grammar_service import get_unlocked_features, compute_comfort
    from app.models import GrammarFeature, UserGrammarExposure

    unlocked_info = get_unlocked_features(db)
    unlocked_set = set(unlocked_info["unlocked_features"])

    all_grammar_keys: set[str] = set()
    for c in candidates:
        if c.grammar_features_json:
            feats = c.grammar_features_json
            if isinstance(feats, str):
                feats = _json.loads(feats)
            if isinstance(feats, list):
                all_grammar_keys.update(feats)

    exposure_map: dict[str, UserGrammarExposure] = {}
    if all_grammar_keys:
        rows = (
            db.query(GrammarFeature.feature_key, UserGrammarExposure)
            .join(UserGrammarExposure, UserGrammarExposure.feature_id == GrammarFeature.feature_id)
            .filter(GrammarFeature.feature_key.in_(all_grammar_keys))
            .all()
        )
        for key, exp in rows:
            exposure_map[key] = exp

    scored = []
    for lemma in candidates:
        freq_score = _frequency_score(lemma.frequency_rank)
        root_score, known_siblings, total_siblings = _root_familiarity_score_batch(
            lemma.root_id, root_total_counts, root_known_counts
        )
        days = _days_since_introduced_batch(lemma.root_id, root_latest_intro, now)

        # Slight boost for root family words introduced recently (1-3 days ago)
        # to cluster root family learning, but not on the same day
        recency_bonus = 0.0
        if 1.0 <= days <= 3.0 and root_score > 0:
            recency_bonus = 0.2

        feats = lemma.grammar_features_json
        if isinstance(feats, str):
            feats = _json.loads(feats)
        pattern_score = _grammar_pattern_score_batch(
            feats, unlocked_set, exposure_map
        )

        # Words in active stories get a flat boost on top of normal scoring
        # so they always rank above non-story words
        story_bonus = 1.0 if lemma.lemma_id in story_lemmas else 0.0

        # Book page bonus: earlier pages score higher (1.0 → 0.2 by page)
        page_bonus = book_page_bonuses.get(lemma.lemma_id, 0.0)

        # Encountered words (seen in textbook/story but not yet introduced) get a bonus
        encountered_bonus = 0.5 if lemma.lemma_id in encountered_ids else 0.0

        # Proper names and onomatopoeia are strongly deprioritized
        category_penalty = {
            "proper_name": -0.8,
            "onomatopoeia": -1.0,
        }.get(lemma.word_category or "", 0.0)

        total_score = (
            freq_score * 0.4
            + root_score * 0.3
            + recency_bonus * 0.2
            + pattern_score * 0.1
            + story_bonus
            + page_bonus
            + encountered_bonus
            + category_penalty
        )

        scored.append({
            "lemma_id": lemma.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "lemma_ar_bare": lemma.lemma_ar_bare,
            "gloss_en": lemma.gloss_en,
            "pos": lemma.pos,
            "transliteration": lemma.transliteration_ala_lc,
            "frequency_rank": lemma.frequency_rank,
            "root_id": lemma.root_id,
            "root": lemma.root.root if lemma.root else None,
            "root_meaning": lemma.root.core_meaning_en if lemma.root else None,
            "forms_json": lemma.forms_json,
            "grammar_features": lemma.grammar_features_json or [],
            "audio_url": lemma.audio_url,
            "example_ar": lemma.example_ar,
            "example_en": lemma.example_en,
            "etymology_json": lemma.etymology_json,
            "memory_hooks_json": lemma.memory_hooks_json,
            "word_category": lemma.word_category,
            "story_title": story_lemmas.get(lemma.lemma_id),
            "score": round(total_score, 3),
            "score_breakdown": {
                "frequency": round(freq_score, 3),
                "root_familiarity": round(root_score, 3),
                "recency_bonus": round(recency_bonus, 3),
                "story_bonus": round(story_bonus, 3),
                "encountered_bonus": round(encountered_bonus, 3),
                "category_penalty": round(category_penalty, 3),
                "known_siblings": known_siblings,
                "total_siblings": total_siblings,
            },
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:count]


def introduce_word(
    db: Session, lemma_id: int, source: str = "study", due_immediately: bool = False,
) -> dict:
    """Mark a word as introduced, starting acquisition (Leitner 3-box).

    Source values: study (Learn mode), auto_intro (inline review), collocate.
    If due_immediately=True, word is due right now (for auto-intro in current session).
    Returns the created knowledge record as dict.
    """
    from app.services.acquisition_service import start_acquisition

    lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
    if not lemma:
        raise ValueError(f"Lemma {lemma_id} not found")

    existing = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if existing:
        if existing.knowledge_state == "suspended":
            from app.services.fsrs_service import reactivate_if_suspended
            reactivate_if_suspended(db, lemma_id, source)
            return {
                "lemma_id": lemma_id,
                "state": "learning",
                "reactivated": True,
            }
        if existing.knowledge_state == "encountered":
            # Transition encountered → acquiring
            ulk = start_acquisition(db, lemma_id, source=source, due_immediately=due_immediately)
            from app.services.topic_service import record_introduction
            record_introduction(db)
            db.commit()
            root_family = []
            if lemma.root_id:
                root_family = get_root_family(db, lemma.root_id)
            return {
                "lemma_id": lemma_id,
                "lemma_ar": lemma.lemma_ar,
                "gloss_en": lemma.gloss_en,
                "state": "acquiring",
                "already_known": False,
                "introduced_at": ulk.introduced_at.isoformat() if ulk.introduced_at else None,
                "root": lemma.root.root if lemma.root else None,
                "root_meaning": lemma.root.core_meaning_en if lemma.root else None,
                "root_family": root_family,
            }
        return {
            "lemma_id": lemma_id,
            "state": existing.knowledge_state,
            "already_known": True,
        }

    # New word — start acquisition
    ulk = start_acquisition(db, lemma_id, source=source, due_immediately=due_immediately)
    from app.services.topic_service import record_introduction
    record_introduction(db)
    db.commit()

    root_family = []
    if lemma.root_id:
        root_family = get_root_family(db, lemma.root_id)

    return {
        "lemma_id": lemma_id,
        "lemma_ar": lemma.lemma_ar,
        "gloss_en": lemma.gloss_en,
        "state": "acquiring",
        "already_known": False,
        "introduced_at": ulk.introduced_at.isoformat() if ulk.introduced_at else None,
        "root": lemma.root.root if lemma.root else None,
        "root_meaning": lemma.root.core_meaning_en if lemma.root else None,
        "root_family": root_family,
    }


def get_sentence_difficulty_params(db: Session, lemma_id: int) -> dict:
    """Get sentence generation parameters scaled by word familiarity.

    Newly introduced words get shorter, simpler sentences.
    Well-known words get longer, more complex ones.
    """
    knowledge = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )

    if not knowledge or not knowledge.introduced_at:
        return {
            "max_words": 7,
            "difficulty_hint": "simple",
            "use_only_top_known": True,
            "description": "Brand new word — short, simple sentence",
        }

    times_seen = knowledge.times_seen or 0
    if knowledge.introduced_at.tzinfo is None:
        introduced = knowledge.introduced_at.replace(tzinfo=timezone.utc)
    else:
        introduced = knowledge.introduced_at
    hours_since = (datetime.now(timezone.utc) - introduced).total_seconds() / 3600

    # Stage 1: First session (< 2 hours, seen < 3 times)
    if hours_since < 2 and times_seen < 3:
        return {
            "max_words": 7,
            "difficulty_hint": "simple",
            "use_only_top_known": True,
            "description": "Initial reinforcement — short and simple",
        }

    # Stage 2: Same day (< 24 hours, seen < 6 times)
    if hours_since < 24 and times_seen < 6:
        return {
            "max_words": 9,
            "difficulty_hint": "simple",
            "use_only_top_known": True,
            "description": "Same-day reinforcement — moderate length",
        }

    # Stage 3: First week (< 168 hours, seen < 10 times)
    if hours_since < 168 and times_seen < 10:
        return {
            "max_words": 11,
            "difficulty_hint": "beginner",
            "use_only_top_known": False,
            "description": "Early consolidation — longer sentences",
        }

    # Stage 4: Established (> 1 week, seen 10+ times)
    return {
        "max_words": 14,
        "difficulty_hint": "intermediate",
        "use_only_top_known": False,
        "description": "Well-known — full natural sentences",
    }
