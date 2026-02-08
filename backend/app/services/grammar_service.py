"""Grammar feature tracking with progression tiers.

Tracks which grammatical features a learner has been exposed to,
calculates comfort scores, and determines which features are unlocked
based on a tiered progression system.
"""

import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import (
    GrammarFeature,
    UserGrammarExposure,
    UserLemmaKnowledge,
)

SEED_FEATURES = [
    # number
    ("number", "singular", "Singular", "مُفْرَد", 1),
    ("number", "dual", "Dual", "مُثَنَّى", 2),
    ("number", "plural_sound", "Sound Plural", "جَمْع سَالِم", 3),
    ("number", "plural_broken", "Broken Plural", "جَمْع تَكْسِير", 4),
    # gender
    ("gender", "masculine", "Masculine", "مُذَكَّر", 10),
    ("gender", "feminine", "Feminine", "مُؤَنَّث", 11),
    # verb_tense
    ("verb_tense", "past", "Past Tense", "الماضِي", 20),
    ("verb_tense", "present", "Present Tense", "المُضارِع", 21),
    ("verb_tense", "imperative", "Imperative", "الأَمْر", 22),
    # verb_form
    ("verb_form", "form_1", "Form I", "فَعَلَ", 30),
    ("verb_form", "form_2", "Form II", "فَعَّلَ", 31),
    ("verb_form", "form_3", "Form III", "فاعَلَ", 32),
    ("verb_form", "form_4", "Form IV", "أَفْعَلَ", 33),
    ("verb_form", "form_5", "Form V", "تَفَعَّلَ", 34),
    ("verb_form", "form_6", "Form VI", "تَفاعَلَ", 35),
    ("verb_form", "form_7", "Form VII", "اِنْفَعَلَ", 36),
    ("verb_form", "form_8", "Form VIII", "اِفْتَعَلَ", 37),
    ("verb_form", "form_9", "Form IX", "اِفْعَلَّ", 38),
    ("verb_form", "form_10", "Form X", "اِسْتَفْعَلَ", 39),
    # syntax
    ("syntax", "idafa", "Idafa (Construct)", "إِضافَة", 50),
    ("syntax", "comparative", "Comparative", "أَفْعَل التَّفْضِيل", 51),
    ("syntax", "superlative", "Superlative", "الأَفْعَل", 52),
    ("syntax", "passive", "Passive Voice", "المَبْنِيّ لِلْمَجْهُول", 53),
    ("syntax", "negation", "Negation", "النَّفْي", 54),
]

TIER_FEATURES: dict[int, list[str]] = {
    0: ["singular", "masculine", "present", "form_1"],
    1: ["feminine", "past", "idafa"],
    2: ["plural_sound", "negation", "imperative"],
    3: ["plural_broken", "form_2", "form_3", "passive"],
    4: [
        "dual", "comparative", "superlative",
        "form_4", "form_5", "form_6", "form_7", "form_8", "form_9", "form_10",
    ],
}

TIER_REQUIREMENTS: dict[int, dict] = {
    0: {},
    1: {"min_words": 10},
    2: {"prev_tier": 1, "comfort_threshold": 0.3},
    3: {"prev_tier": 2, "comfort_threshold": 0.4},
    4: {"prev_tier": 3, "comfort_threshold": 0.5},
}


def seed_grammar_features(db: Session) -> int:
    """Populate grammar_features table. Returns count of newly inserted rows."""
    existing = {
        f.feature_key
        for f in db.query(GrammarFeature.feature_key).all()
    }
    added = 0
    for category, key, label_en, label_ar, sort_order in SEED_FEATURES:
        if key in existing:
            continue
        db.add(GrammarFeature(
            category=category,
            feature_key=key,
            label_en=label_en,
            label_ar=label_ar,
            sort_order=sort_order,
        ))
        added += 1
    if added:
        db.commit()
    return added


def compute_comfort(
    times_seen: int,
    times_correct: int,
    last_seen_at: Optional[datetime],
) -> float:
    """Calculate comfort score for a grammar feature."""
    if times_seen == 0:
        return 0.0

    exposure = min(math.log2(times_seen + 1) / math.log2(31), 0.6)
    accuracy = (times_correct / times_seen) * 0.4

    if last_seen_at is None:
        decay = 0.0
    else:
        if last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)
        days_since = (datetime.now(timezone.utc) - last_seen_at).total_seconds() / 86400
        decay = 0.5 ** (days_since / 30.0)

    return min((exposure + accuracy) * decay, 1.0)


def _get_total_known_words(db: Session) -> int:
    return (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]))
        .scalar() or 0
    )


def _tier_avg_comfort(db: Session, tier: int) -> float:
    """Average comfort score across all features in a tier."""
    keys = TIER_FEATURES.get(tier, [])
    if not keys:
        return 0.0

    exposures = (
        db.query(UserGrammarExposure)
        .join(GrammarFeature)
        .filter(GrammarFeature.feature_key.in_(keys))
        .all()
    )

    if not exposures:
        return 0.0

    total = sum(
        compute_comfort(e.times_seen, e.times_correct, e.last_seen_at)
        for e in exposures
    )
    return total / len(keys)


def get_all_features(db: Session) -> list[dict]:
    """Return all grammar features grouped by category."""
    features = (
        db.query(GrammarFeature)
        .order_by(GrammarFeature.sort_order)
        .all()
    )
    return [
        {
            "feature_id": f.feature_id,
            "category": f.category,
            "feature_key": f.feature_key,
            "label_en": f.label_en,
            "label_ar": f.label_ar,
            "sort_order": f.sort_order,
        }
        for f in features
    ]


def get_user_progress(db: Session) -> list[dict]:
    """Return user's exposure and comfort for each feature."""
    features = (
        db.query(GrammarFeature)
        .order_by(GrammarFeature.sort_order)
        .all()
    )

    exposure_map: dict[int, UserGrammarExposure] = {}
    for e in db.query(UserGrammarExposure).all():
        exposure_map[e.feature_id] = e

    result = []
    for f in features:
        exp = exposure_map.get(f.feature_id)
        if exp:
            comfort = compute_comfort(exp.times_seen, exp.times_correct, exp.last_seen_at)
            result.append({
                "feature_key": f.feature_key,
                "category": f.category,
                "label_en": f.label_en,
                "times_seen": exp.times_seen,
                "times_correct": exp.times_correct,
                "comfort_score": round(comfort, 3),
                "first_seen_at": exp.first_seen_at.isoformat() if exp.first_seen_at else None,
                "last_seen_at": exp.last_seen_at.isoformat() if exp.last_seen_at else None,
            })
        else:
            result.append({
                "feature_key": f.feature_key,
                "category": f.category,
                "label_en": f.label_en,
                "times_seen": 0,
                "times_correct": 0,
                "comfort_score": 0.0,
                "first_seen_at": None,
                "last_seen_at": None,
            })
    return result


def get_unlocked_features(db: Session) -> dict:
    """Determine which tiers/features are unlocked for the user."""
    total_words = _get_total_known_words(db)
    unlocked: list[str] = []
    max_tier = 0

    for tier in sorted(TIER_FEATURES.keys()):
        req = TIER_REQUIREMENTS[tier]

        if "min_words" in req and total_words < req["min_words"]:
            break
        if "prev_tier" in req:
            prev_comfort = _tier_avg_comfort(db, req["prev_tier"])
            if prev_comfort < req["comfort_threshold"]:
                break

        unlocked.extend(TIER_FEATURES[tier])
        max_tier = tier

    return {
        "current_tier": max_tier,
        "total_words": total_words,
        "unlocked_features": unlocked,
        "all_tiers": {
            tier: {
                "features": features,
                "requirements": TIER_REQUIREMENTS[tier],
                "unlocked": all(f in unlocked for f in features),
            }
            for tier, features in TIER_FEATURES.items()
        },
    }


def record_grammar_exposure(
    db: Session,
    feature_key: str,
    correct: bool,
) -> None:
    """Record that the user saw a grammar feature during review."""
    feature = (
        db.query(GrammarFeature)
        .filter(GrammarFeature.feature_key == feature_key)
        .first()
    )
    if not feature:
        return

    now = datetime.now(timezone.utc)
    exposure = (
        db.query(UserGrammarExposure)
        .filter(UserGrammarExposure.feature_id == feature.feature_id)
        .first()
    )

    if exposure:
        exposure.times_seen += 1
        if correct:
            exposure.times_correct += 1
        exposure.last_seen_at = now
        exposure.comfort_score = compute_comfort(
            exposure.times_seen, exposure.times_correct, now
        )
    else:
        exposure = UserGrammarExposure(
            feature_id=feature.feature_id,
            times_seen=1,
            times_correct=1 if correct else 0,
            first_seen_at=now,
            last_seen_at=now,
            comfort_score=compute_comfort(1, 1 if correct else 0, now),
        )
        db.add(exposure)

    db.commit()


def grammar_pattern_score(db: Session, lemma_grammar_features: Optional[list[str]]) -> float:
    """Score how much a word's grammar features would benefit the learner.

    Returns higher scores for words with features the user needs practice on
    (unlocked but low comfort). Used by word_selector as pattern_score.
    """
    if not lemma_grammar_features:
        return 0.1  # base score for words without grammar tagging

    unlocked_info = get_unlocked_features(db)
    unlocked_set = set(unlocked_info["unlocked_features"])

    exposure_map: dict[str, UserGrammarExposure] = {}
    for e in (
        db.query(UserGrammarExposure)
        .join(GrammarFeature)
        .filter(GrammarFeature.feature_key.in_(lemma_grammar_features))
        .all()
    ):
        exposure_map[e.feature.feature_key] = e

    scores = []
    for key in lemma_grammar_features:
        if key not in unlocked_set:
            continue
        exp = exposure_map.get(key)
        if exp is None:
            scores.append(1.0)  # never-seen unlocked feature = high value
        else:
            comfort = compute_comfort(exp.times_seen, exp.times_correct, exp.last_seen_at)
            scores.append(max(1.0 - comfort, 0.1))

    if not scores:
        return 0.1

    return sum(scores) / len(scores)
