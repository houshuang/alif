"""Grammar lesson generation and management.

Generates brief grammar explanations for reading comprehension,
tracks introduction status, and resurfaces lessons when confusion is high.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import (
    GrammarFeature,
    SentenceGrammarFeature,
    UserGrammarExposure,
)
from app.services.grammar_service import compute_comfort

# Static lesson content for each grammar concept.
# These are short reading-focused explanations — no LLM needed.
GRAMMAR_LESSONS: dict[str, dict] = {
    "definite_article": {
        "explanation": "The prefix ال (al-) makes a noun definite. It attaches directly to the word.",
        "examples": [
            {"ar": "كِتَابٌ", "en": "a book"},
            {"ar": "الكِتَابُ", "en": "the book"},
        ],
        "tip": "Look for ال at the start of a word — it always means 'the'.",
    },
    "proclitic_prepositions": {
        "explanation": "The prepositions بـ (bi, with/by), لـ (li, for/to), and كـ (ka, like) attach directly to the next word. When combined with ال, they fuse: لِلـ (lil), بِالـ (bil).",
        "examples": [
            {"ar": "الكِتَابُ", "en": "the book"},
            {"ar": "لِلْكِتَابِ", "en": "for the book"},
            {"ar": "بِالقَلَمِ", "en": "with the pen"},
        ],
        "tip": "If a word starts with لِلـ or بِالـ, mentally split: preposition + ال + noun.",
    },
    "attached_pronouns": {
        "explanation": "Pronouns attach to the end of nouns (possessive), verbs (object), and prepositions. Common suffixes: ـه (his), ـها (her), ـهم (their), ـك (your), ـنا (our), ـي (my).",
        "examples": [
            {"ar": "كِتَاب", "en": "book"},
            {"ar": "كِتَابُهُ", "en": "his book"},
            {"ar": "كِتَابِي", "en": "my book"},
        ],
        "tip": "A familiar word with an extra ending is likely base + pronoun.",
    },
    "feminine": {
        "explanation": "Most feminine nouns/adjectives end with تاء مربوطة (ة). It looks like ه with two dots above.",
        "examples": [
            {"ar": "كَبِيرٌ", "en": "big (masc)"},
            {"ar": "كَبِيرَةٌ", "en": "big (fem)"},
        ],
        "tip": "The ة ending almost always signals feminine gender.",
    },
    "past": {
        "explanation": "Past tense uses suffix conjugation. The root consonants stay, and suffixes indicate who did the action.",
        "examples": [
            {"ar": "كَتَبَ", "en": "he wrote"},
            {"ar": "كَتَبْتُ", "en": "I wrote"},
        ],
        "tip": "Past tense verbs have suffixes after the root: ـتُ (I), ـتَ (you m), ـتْ (she).",
    },
    "present": {
        "explanation": "Present tense uses prefix conjugation. A letter is added before the root, and sometimes a suffix too.",
        "examples": [
            {"ar": "يَكْتُبُ", "en": "he writes"},
            {"ar": "أَكْتُبُ", "en": "I write"},
        ],
        "tip": "Present verbs start with يـ (he), تـ (you/she), أ (I), or نـ (we).",
    },
    "idafa": {
        "explanation": "Idafa (construct state) expresses possession: two nouns side by side, the first WITHOUT ال. 'X of Y' or 'Y's X'.",
        "examples": [
            {"ar": "كِتَابُ الطَّالِبِ", "en": "the student's book"},
            {"ar": "بَابُ البَيْتِ", "en": "the door of the house"},
        ],
        "tip": "Two nouns together where the first lacks ال — it's possession.",
    },
    "plural_sound": {
        "explanation": "Sound plurals add a regular ending: ـونَ/ـينَ for masculine, ـاتٌ for feminine.",
        "examples": [
            {"ar": "مُعَلِّمٌ → مُعَلِّمُونَ", "en": "teacher → teachers (m)"},
            {"ar": "مُعَلِّمَةٌ → مُعَلِّمَاتٌ", "en": "teacher → teachers (f)"},
        ],
        "tip": "ـون/ـين or ـات endings are regular plurals — the base word is still visible.",
    },
    "plural_broken": {
        "explanation": "Broken plurals change the internal vowel pattern. There's no single rule — each must be memorized.",
        "examples": [
            {"ar": "كِتَابٌ → كُتُبٌ", "en": "book → books"},
            {"ar": "وَلَدٌ → أَوْلَادٌ", "en": "boy → boys"},
        ],
        "tip": "If a word looks unfamiliar, check if it's a broken plural of a word you know.",
    },
    "negation": {
        "explanation": "Arabic has several negation particles: لا (present), ما (past/nominal), لم (past via jussive), لن (future), ليس (is not).",
        "examples": [
            {"ar": "لا يَكْتُبُ", "en": "he does not write"},
            {"ar": "لَمْ يَكْتُبْ", "en": "he did not write"},
        ],
        "tip": "Look for لا، ما، لم، لن، ليس before verbs or nouns — they negate.",
    },
    "nominal_sentence": {
        "explanation": "A nominal sentence starts with a subject and has no verb in the present. Arabic has no word for 'is/are'.",
        "examples": [
            {"ar": "الكِتَابُ كَبِيرٌ", "en": "The book is big"},
            {"ar": "هُوَ طَالِبٌ", "en": "He is a student"},
        ],
        "tip": "Two nouns/adjectives next to each other with no verb? It's a nominal sentence — add 'is' mentally.",
    },
    "active_participle": {
        "explanation": "The active participle follows the فاعِل pattern for Form I verbs. It works as both adjective and noun.",
        "examples": [
            {"ar": "كَتَبَ → كاتِبٌ", "en": "wrote → writer/writing"},
            {"ar": "عَمِلَ → عامِلٌ", "en": "worked → worker/working"},
        ],
        "tip": "Pattern فاعِل (faa3il) — long 'a' after first root letter, kasra before last.",
    },
    "passive_participle": {
        "explanation": "The passive participle follows the مَفْعُول pattern for Form I. Very common as adjectives.",
        "examples": [
            {"ar": "كَتَبَ → مَكْتُوبٌ", "en": "wrote → written"},
            {"ar": "عَرَفَ → مَعْرُوفٌ", "en": "knew → known/famous"},
        ],
        "tip": "Pattern مَفْعُول (maf3uul) — م prefix, uu before last root letter.",
    },
    "masdar": {
        "explanation": "The verbal noun (masdar) is used where English often uses a verb. Each verb form has its own masdar pattern.",
        "examples": [
            {"ar": "كَتَبَ → كِتَابَة", "en": "wrote → writing"},
            {"ar": "دَرَسَ → دِرَاسَة", "en": "studied → studying/study"},
        ],
        "tip": "Arabic heavily uses nouns where English uses verbs. If you see an unfamiliar noun, check if it's a masdar.",
    },
    "kaana_sisters": {
        "explanation": "كان and its sisters (أصبح، ظلّ، ما زال) are past-tense verbs that introduce time/aspect to nominal sentences.",
        "examples": [
            {"ar": "الكِتَابُ كَبِيرٌ", "en": "The book is big"},
            {"ar": "كانَ الكِتَابُ كَبِيرًا", "en": "The book was big"},
        ],
        "tip": "كان before a nominal sentence = past tense. Look for the accusative ending on the predicate.",
    },
    "inna_sisters": {
        "explanation": "إنّ and its sisters (أنّ، لكنّ، لأنّ) are emphatic/connective particles that front a nominal sentence.",
        "examples": [
            {"ar": "إِنَّ الكِتَابَ كَبِيرٌ", "en": "Indeed, the book is big"},
            {"ar": "لأَنَّ الجَوَّ حارٌّ", "en": "Because the weather is hot"},
        ],
        "tip": "إنّ/أنّ/لكنّ are among the most common MSA words. The noun after them takes accusative.",
    },
    "relative_clauses": {
        "explanation": "الذي (m.sg), التي (f.sg), الذين (m.pl) connect clauses like 'who/which/that' in English.",
        "examples": [
            {"ar": "الكِتَابُ الَّذِي قَرَأْتُهُ", "en": "the book that I read"},
            {"ar": "الطَّالِبَةُ الَّتِي نَجَحَتْ", "en": "the student (f) who passed"},
        ],
        "tip": "الذي/التي/الذين after a definite noun starts a relative clause.",
    },
    "weak_hollow": {
        "explanation": "Hollow verbs have و or ي as their middle radical, which disappears or changes in different forms.",
        "examples": [
            {"ar": "قالَ / يَقُولُ", "en": "said / says (root: ق.و.ل)"},
            {"ar": "نامَ / يَنامُ", "en": "slept / sleeps (root: ن.و.م)"},
        ],
        "tip": "Very common verbs like قال، كان، زار are hollow — the middle letter shifts between و and ا.",
    },
    "weak_defective": {
        "explanation": "Defective verbs have و or ي as their final radical, which changes or drops in conjugation.",
        "examples": [
            {"ar": "مَشَى / يَمْشِي", "en": "walked / walks"},
            {"ar": "بَنَى / يَبْنِي", "en": "built / builds"},
        ],
        "tip": "If a verb ends in ى or ي, the final radical may be hidden — try removing it to find the root.",
    },
    "conditional": {
        "explanation": "Conditional sentences use إذا (real condition), لو (hypothetical), or إن (uncertain). Each affects verb mood.",
        "examples": [
            {"ar": "إِذا دَرَسْتَ نَجَحْتَ", "en": "If you study, you succeed"},
            {"ar": "لَوْ كُنْتُ غَنِيًّا", "en": "If I were rich"},
        ],
        "tip": "إذا/لو/إن at the start of a clause signals a condition — look for two linked clauses.",
    },
}


def get_lesson(
    db: Session,
    feature_key: str,
) -> Optional[dict]:
    """Get lesson content for a grammar feature."""
    feature = (
        db.query(GrammarFeature)
        .filter(GrammarFeature.feature_key == feature_key)
        .first()
    )
    if not feature:
        return None

    lesson_data = GRAMMAR_LESSONS.get(feature_key)

    exposure = (
        db.query(UserGrammarExposure)
        .filter(UserGrammarExposure.feature_id == feature.feature_id)
        .first()
    )

    result = {
        "feature_key": feature_key,
        "label_en": feature.label_en,
        "label_ar": feature.label_ar,
        "category": feature.category,
        "form_change_type": feature.form_change_type,
        "introduced_at": exposure.introduced_at.isoformat() if exposure and exposure.introduced_at else None,
        "times_seen": exposure.times_seen if exposure else 0,
        "times_confused": exposure.times_confused if exposure else 0,
        "comfort_score": round(
            compute_comfort(exposure.times_seen, exposure.times_correct, exposure.last_seen_at), 3
        ) if exposure else 0.0,
    }

    if lesson_data:
        result["explanation"] = lesson_data["explanation"]
        result["examples"] = lesson_data["examples"]
        result["tip"] = lesson_data["tip"]
    else:
        result["explanation"] = f"Grammar concept: {feature.label_en}"
        result["examples"] = []
        result["tip"] = None

    return result


def introduce_feature(
    db: Session,
    feature_key: str,
) -> Optional[dict]:
    """Mark a grammar feature as introduced."""
    feature = (
        db.query(GrammarFeature)
        .filter(GrammarFeature.feature_key == feature_key)
        .first()
    )
    if not feature:
        return None

    now = datetime.now(timezone.utc)
    exposure = (
        db.query(UserGrammarExposure)
        .filter(UserGrammarExposure.feature_id == feature.feature_id)
        .first()
    )

    if exposure:
        exposure.introduced_at = now
    else:
        exposure = UserGrammarExposure(
            feature_id=feature.feature_id,
            times_seen=0,
            times_correct=0,
            first_seen_at=now,
            last_seen_at=now,
            comfort_score=0.0,
            introduced_at=now,
            times_confused=0,
        )
        db.add(exposure)

    db.commit()
    return {"feature_key": feature_key, "introduced_at": now.isoformat()}


CONFUSION_RATE_THRESHOLD = 0.3
MIN_SEEN_FOR_CONFUSION = 5


def get_confused_features(db: Session) -> list[dict]:
    """Get features with high confusion rates that need resurfacing."""
    exposures = (
        db.query(UserGrammarExposure)
        .join(GrammarFeature)
        .filter(UserGrammarExposure.times_seen >= MIN_SEEN_FOR_CONFUSION)
        .all()
    )

    confused = []
    for exp in exposures:
        if not exp.times_confused:
            continue
        confusion_rate = exp.times_confused / exp.times_seen
        if confusion_rate >= CONFUSION_RATE_THRESHOLD:
            lesson = get_lesson(db, exp.feature.feature_key)
            if lesson:
                lesson["confusion_rate"] = round(confusion_rate, 3)
                lesson["is_refresher"] = True
                confused.append(lesson)

    return confused


def get_unintroduced_features_for_session(
    db: Session,
    sentence_ids: list[int],
) -> list[str]:
    """Find grammar features in session sentences that haven't been introduced yet."""
    if not sentence_ids:
        return []

    # Get all grammar features tagged on these sentences
    sgf_rows = (
        db.query(SentenceGrammarFeature.feature_id)
        .filter(SentenceGrammarFeature.sentence_id.in_(sentence_ids))
        .distinct()
        .all()
    )
    feature_ids = {row.feature_id for row in sgf_rows}
    if not feature_ids:
        return []

    # Check which are introduced
    introduced = set()
    exposures = (
        db.query(UserGrammarExposure)
        .filter(
            UserGrammarExposure.feature_id.in_(feature_ids),
            UserGrammarExposure.introduced_at.isnot(None),
        )
        .all()
    )
    introduced = {exp.feature_id for exp in exposures}

    # Get feature keys for un-introduced
    unintroduced_ids = feature_ids - introduced
    if not unintroduced_ids:
        return []

    features = (
        db.query(GrammarFeature)
        .filter(GrammarFeature.feature_id.in_(unintroduced_ids))
        .all()
    )

    # Only return features that have lesson content
    return [f.feature_key for f in features if f.feature_key in GRAMMAR_LESSONS]
