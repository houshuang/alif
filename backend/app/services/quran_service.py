"""Quranic verse reading mode — selection, review, and lazy lemmatization.

Verses are scheduled with a simple SRS (not FSRS):
- Level 0: unseen
- Level 1-7: learning with increasing intervals
- Level 8: graduated (no longer shown)
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from sqlalchemy import func

from app.models import Lemma, QuranicVerse, QuranicVerseWord, Root, UserLemmaKnowledge
from app.services.interaction_logger import log_interaction
from app.services.transliteration import transliterate_arabic
import re

from app.services.sentence_validator import (
    FUNCTION_WORD_GLOSSES,
    PROCLITICS,
    _is_function_word,
    build_lemma_lookup,
    lookup_lemma,
    normalize_alef,
    normalize_quranic_to_msa,
    resolve_existing_lemma,
    strip_diacritics,
    strip_tatweel,
    tokenize_display,
)

# Quran-specific Unicode: small ya (ۦ), paragraph markers (۞), small high letters, etc.
_QURAN_EXTRA_CHARS = re.compile("[\u06D6-\u06ED\u06E5\u06E6\u0670\u08F0-\u08FF\u065E\u065F۞]")


def _normalize_quran(text: str) -> str:
    """Extra normalization for Quran orthography beyond standard Arabic."""
    text = _QURAN_EXTRA_CHARS.sub("", text)
    text = text.replace("ء", "ا")  # hamza alone → alef (ءامن → اامن → امن after dedup)
    # Collapse double alef from hamza normalization: اا → ا
    text = text.replace("اا", "ا")
    return text


def _quran_bare(text: str) -> str:
    """Quran surface form → normalized bare key.

    Converts Mushaf presentation letters (notably the dagger alef U+0670 → ا)
    BEFORE stripping diacritics. The dagger alef sits in the diacritic range, so
    a plain strip_diacritics() deletes the long ā and collapses the word onto a
    bare consonant skeleton that can be a *different* word — e.g. خَٰلِدُونَ
    "abiding forever" → خلدون, which is the proper name Khaldūn (ابن خلدون).
    See normalize_quranic_to_msa().
    """
    text = normalize_quranic_to_msa(text)
    return _normalize_quran(normalize_alef(strip_tatweel(strip_diacritics(text))))

# Quranic function words not in the standard FUNCTION_WORD_GLOSSES
_QURAN_FUNCTION_GLOSSES: dict[str, str] = {
    # Disconnected object pronouns (إيّا) — fronted for exclusivity
    "اياك": "You alone (obj.)",
    "واياك": "and You alone (obj.)",
    "اياه": "Him alone (obj.)",
    "اياهم": "them alone (obj.)",
    "اياها": "her alone (obj.)",
    "اياي": "Me alone (obj.)",
    "ايانا": "us alone (obj.)",
    "اياكم": "you all alone (obj.)",
    # Muqatta'at — mystery letters opening surahs
    "الم": "Alif Lam Mim",
    "المص": "Alif Lam Mim Sad",
    "الر": "Alif Lam Ra",
    "المر": "Alif Lam Mim Ra",
    "كهيعص": "Kaf Ha Ya 'Ain Sad",
    "طه": "Ta Ha",
    "طسم": "Ta Sin Mim",
    "طس": "Ta Sin",
    "يس": "Ya Sin",
    "ص": "Sad",
    "حم": "Ha Mim",
    "ق": "Qaf",
    "ن": "Nun",
}

# Pronoun suffixes (longest first) and their English glosses
_PRONOUN_SUFFIXES: list[tuple[str, str]] = [
    ("هما", "them (dual)"),
    ("كما", "you (dual)"),
    ("هم", "them"),
    ("هن", "them (f)"),
    ("كم", "you (pl)"),
    ("كن", "you (f pl)"),
    ("نا", "us"),
    ("ها", "her/it"),
    ("ه", "him/it"),
    ("ك", "you"),
    ("ي", "me/my"),
]


def _gloss_with_pronoun_suffix(bare: str) -> str | None:
    """Try stripping pronoun suffixes to find a base function word + suffix gloss.

    Also handles proclitic+base+suffix combinations like ولهم → و+ل+هم.
    """
    for suffix, suffix_gloss in _PRONOUN_SUFFIXES:
        if not bare.endswith(suffix) or len(bare) <= len(suffix):
            continue
        base = bare[: -len(suffix)]
        # Try base as-is, then with ya↔alef-maqsura swap
        bases = [base]
        if base.endswith("ي"):
            bases.append(base[:-1] + "ى")
        elif base.endswith("ى"):
            bases.append(base[:-1] + "ي")
        for b in bases:
            base_gloss = FUNCTION_WORD_GLOSSES.get(b) or _QURAN_FUNCTION_GLOSSES.get(b)
            if base_gloss:
                return f"{base_gloss} + {suffix_gloss}"
        # Try stripping leading proclitic from base: ولهم → و + لهم → ل + هم
        for pro_str, pro_gloss in [("و", "and"), ("ف", "so/then")]:
            if base.startswith(pro_str) and len(base) > 1:
                inner = base[len(pro_str):]
                inner_gloss = FUNCTION_WORD_GLOSSES.get(inner) or _QURAN_FUNCTION_GLOSSES.get(inner)
                if inner_gloss:
                    return f"{pro_gloss} + {inner_gloss} + {suffix_gloss}"
    return None


def _gloss_via_lemma_lookup(bare: str, db: Session) -> Lemma | None:
    """Try stripping proclitics/suffixes and finding a matching lemma in DB."""
    # Try with and without al-prefix, with and without proclitics
    candidates = [bare]
    # Strip leading proclitic+al combos: والمفسدون → المفسدون → مفسدون
    for pre in ["وال", "فال", "بال", "لل", "كال", "و", "ف", "ب", "ل", "ك"]:
        if bare.startswith(pre) and len(bare) > len(pre) + 1:
            after = bare[len(pre):]
            candidates.append(after)
            if not after.startswith("ال"):
                candidates.append("ال" + after)
    # Strip al-prefix: الغيب → غيب
    if bare.startswith("ال") and len(bare) > 3:
        candidates.append(bare[2:])
    # Strip pronoun suffixes on content words: قلوبهم → قلوب
    for suffix, _ in _PRONOUN_SUFFIXES:
        if bare.endswith(suffix) and len(bare) > len(suffix) + 1:
            stem = bare[:-len(suffix)]
            candidates.append(stem)
            # Also try with proclitics stripped
            for pre in ["و", "ف"]:
                if stem.startswith(pre) and len(stem) > 2:
                    candidates.append(stem[len(pre):])

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen and c != bare:
            seen.add(c)
            unique.append(c)

    if not unique:
        return None

    # Batch lookup in DB
    lemmas = db.query(Lemma).filter(
        Lemma.lemma_ar_bare.in_(unique),
        Lemma.gloss_en.isnot(None),
        Lemma.gloss_en != "",
    ).all()
    if lemmas:
        return lemmas[0]
    return None


# In-memory cache for LLM-generated glosses (surface_form → gloss)
_llm_gloss_cache: dict[str, str] = {}


def _fill_glosses_llm(
    glossless: list[tuple[int, int, str]],
    verse_words_by_id: dict[int, list],
    all_verses: list,
) -> None:
    """Batch-translate glossless words via LLM. Results cached in memory."""
    # Check cache first
    uncached = [(vid, wi, sf) for vid, wi, sf in glossless if sf not in _llm_gloss_cache]
    if uncached:
        surface_forms = list({sf for _, _, sf in uncached})
        try:
            from app.services.llm import generate_completion
            prompt = (
                "Translate each Arabic word/phrase to a brief English gloss (1-4 words). "
                "These are from the Quran. Return a JSON object mapping Arabic → English.\n\n"
                + "\n".join(surface_forms)
            )
            result = generate_completion(
                prompt=prompt,
                json_mode=True,
                temperature=0.1,
                model_override="claude_haiku",
                task_type="quran_lemma_translation",
            )
            if isinstance(result, dict):
                for ar, en in result.items():
                    bare = _quran_bare(ar)
                    _llm_gloss_cache[ar] = str(en)
                    _llm_gloss_cache[bare] = str(en)
                # Also map by surface form directly
                for sf in surface_forms:
                    if sf not in _llm_gloss_cache:
                        bare = _quran_bare(sf)
                        if bare in _llm_gloss_cache:
                            _llm_gloss_cache[sf] = _llm_gloss_cache[bare]
        except Exception as e:
            logger.warning(f"LLM gloss fallback failed: {e}")

    # Apply cached glosses — hard guarantee: every word gets SOMETHING
    for vid, wi, sf in glossless:
        gloss = _llm_gloss_cache.get(sf)
        if vid in verse_words_by_id and wi < len(verse_words_by_id[vid]):
            if gloss:
                verse_words_by_id[vid][wi]["gloss_en"] = gloss
            elif not verse_words_by_id[vid][wi]["gloss_en"]:
                # Absolute last resort: transliterate the Arabic
                from app.services.transliteration import transliterate_arabic
                verse_words_by_id[vid][wi]["gloss_en"] = f"({transliterate_arabic(sf)})"
                logger.error(f"No gloss found for Quran word '{sf}' — using transliteration fallback")


logger = logging.getLogger(__name__)

# SRS interval progression (level -> timedelta after "got_it")
# Minimum 1 day — Quran verses are full sentences, not vocab flashcards
VERSE_INTERVALS = {
    1: timedelta(days=1),
    2: timedelta(days=3),
    3: timedelta(days=7),
    4: timedelta(days=14),
    5: timedelta(days=30),
    6: timedelta(days=60),
}
MAX_LEARNING_LEVEL = 7
GRADUATED_LEVEL = 8

# Scheduling constants
MAX_NON_UNDERSTOOD_BACKLOG = 20
LEMMATIZE_AHEAD = 20
LEMMATIZE_THRESHOLD = 10  # trigger when fewer than this many lemmatized unseen remain


MAX_NEW_VERSES_PER_DAY = 3


def select_verse_cards(
    db: Session,
    max_new: int = 1,
    max_total: int = 3,
) -> list[dict]:
    """Select verse cards for the current session.

    Returns due review verses + new verses (gated by backlog + daily cap).
    Only lemmatized verses can be introduced.
    """
    now = datetime.utcnow()

    # 1. Query due verses
    due_verses = (
        db.query(QuranicVerse)
        .filter(
            QuranicVerse.next_due.isnot(None),
            QuranicVerse.next_due <= now,
            QuranicVerse.srs_level >= 1,
            QuranicVerse.srs_level <= MAX_LEARNING_LEVEL,
        )
        .order_by(QuranicVerse.next_due.asc())
        .limit(max_total)
        .all()
    )

    # 2. Count non-understood backlog
    backlog = (
        db.query(QuranicVerse)
        .filter(
            QuranicVerse.srs_level >= 1,
            QuranicVerse.srs_level <= MAX_LEARNING_LEVEL,
            QuranicVerse.last_rating != "got_it",
        )
        .count()
    )

    # 3. Introduce new verses if backlog allows + daily cap
    new_verses: list[QuranicVerse] = []
    remaining = max_total - len(due_verses)
    # Daily cap: count verses introduced today
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    introduced_today = (
        db.query(QuranicVerse)
        .filter(
            QuranicVerse.introduced_at >= today_start,
            QuranicVerse.introduced_at.isnot(None),
        )
        .count()
    )
    daily_remaining = max(0, MAX_NEW_VERSES_PER_DAY - introduced_today)
    if remaining > 0 and backlog < MAX_NON_UNDERSTOOD_BACKLOG and daily_remaining > 0:
        new_limit = min(max_new, remaining, daily_remaining)
        new_verses = (
            db.query(QuranicVerse)
            .filter(
                QuranicVerse.srs_level == 0,
                QuranicVerse.lemmatized_at.isnot(None),
            )
            .order_by(QuranicVerse.surah.asc(), QuranicVerse.ayah.asc())
            .limit(new_limit)
            .all()
        )
        for v in new_verses:
            v.srs_level = 1
            v.introduced_at = now
            v.next_due = now
        if new_verses:
            db.commit()

    # 4. Check if we need to lemmatize more verses ahead
    lemmatized_unseen = (
        db.query(QuranicVerse)
        .filter(
            QuranicVerse.srs_level == 0,
            QuranicVerse.lemmatized_at.isnot(None),
        )
        .count()
    )
    if lemmatized_unseen < LEMMATIZE_THRESHOLD:
        try:
            count = lemmatize_quran_verses(db, limit=LEMMATIZE_AHEAD)
            if count > 0:
                logger.info(f"Lemmatized {count} Quran verses ahead")
        except Exception as e:
            logger.warning(f"Background lemmatization failed: {e}")

    # 5. Build response with word data
    all_verses = due_verses + new_verses
    verse_ids = [v.id for v in all_verses]

    # Build lemma lookup dict once for morphological fallback
    all_lemmas = db.query(Lemma).all()
    lemma_lookup = build_lemma_lookup(all_lemmas)
    lemma_by_id = {l.lemma_id: l for l in all_lemmas}

    # Batch load verse words + lemmas
    verse_words_by_id: dict[int, list] = {vid: [] for vid in verse_ids}
    if verse_ids:
        from sqlalchemy.orm import joinedload as jl
        vw_rows = (
            db.query(QuranicVerseWord)
            .options(jl(QuranicVerseWord.lemma))
            .filter(QuranicVerseWord.verse_id.in_(verse_ids))
            .order_by(QuranicVerseWord.verse_id, QuranicVerseWord.position)
            .all()
        )
        for vw in vw_rows:
            lemma = vw.lemma
            # For words without a gloss, try progressively harder lookups
            gloss = lemma.gloss_en if lemma else None
            resolved_lemma = lemma
            if not gloss:
                bare = _quran_bare(vw.surface_form)
                gloss = FUNCTION_WORD_GLOSSES.get(bare) or _QURAN_FUNCTION_GLOSSES.get(bare)
                if not gloss:
                    gloss = _gloss_with_pronoun_suffix(bare)
                if not gloss:
                    db_lemma = _gloss_via_lemma_lookup(bare, db)
                    if db_lemma:
                        gloss = db_lemma.gloss_en
                        resolved_lemma = db_lemma
                if not gloss:
                    # Full morphological lookup (handles conjugations, broken plurals via forms_json)
                    resolved_id = lookup_lemma(bare, lemma_lookup)
                    if resolved_id:
                        resolved_lemma = lemma_by_id.get(resolved_id)
                        if resolved_lemma:
                            gloss = resolved_lemma.gloss_en
            # Use resolved_lemma for richer word data when original lemma was missing
            rl = resolved_lemma  # may be same as lemma, or a morphologically resolved one
            verse_words_by_id[vw.verse_id].append({
                "surface_form": vw.surface_form,
                "lemma_id": vw.lemma_id or (rl.lemma_id if rl else None),
                "lemma_ar": (rl.lemma_ar if rl else None) or (lemma.lemma_ar if lemma else None),
                "gloss_en": gloss,
                "root": rl.root.root if rl and rl.root else None,
                "root_meaning": rl.root.core_meaning_en if rl and rl.root else None,
                "pos": rl.pos if rl else None,
                "is_function_word": vw.is_function_word or False,
            })

    # Safety net: LLM-translate any remaining glossless words
    glossless: list[tuple[int, int, str]] = []  # (verse_idx_in_list, word_idx, surface_form)
    for vid, words_list in verse_words_by_id.items():
        for wi, wd in enumerate(words_list):
            if not wd["gloss_en"]:
                glossless.append((vid, wi, wd["surface_form"]))
    if glossless:
        _fill_glosses_llm(glossless, verse_words_by_id, all_verses)

    result = []
    for v in all_verses:
        result.append({
            "verse_id": v.id,
            "surah": v.surah,
            "ayah": v.ayah,
            "surah_name_ar": v.surah_name_ar,
            "surah_name_en": v.surah_name_en,
            "arabic_text": v.arabic_text,
            "english_translation": v.english_translation,
            "transliteration": transliterate_arabic(v.arabic_text),
            "srs_level": v.srs_level,
            "is_new": v in new_verses,
            "words": verse_words_by_id.get(v.id, []),
        })
    return result


QURAN_PROMOTION_THRESHOLD = 3  # distinct "understood" verses needed to promote to acquiring


def _maybe_promote_quran_lemmas(db: Session, verse_id: int) -> list[dict]:
    """Promote encountered Quran lemmas to acquiring if seen in enough understood verses.

    A verse counts as "understood" if srs_level >= 2 (rated "got_it" at least once).
    When a lemma appears in >= QURAN_PROMOTION_THRESHOLD such verses, it enters acquisition.
    """
    from app.services.acquisition_service import start_acquisition

    # Get lemma_ids in this verse that are still "encountered"
    verse_lemma_ids = [
        row[0] for row in
        db.query(QuranicVerseWord.lemma_id)
        .filter(QuranicVerseWord.verse_id == verse_id, QuranicVerseWord.lemma_id.isnot(None))
        .all()
    ]
    if not verse_lemma_ids:
        return []

    encountered_ids = set(
        row[0] for row in
        db.query(UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.lemma_id.in_(verse_lemma_ids),
            UserLemmaKnowledge.knowledge_state == "encountered",
        )
        .all()
    )
    if not encountered_ids:
        return []

    # For each encountered lemma, count distinct understood verses it appears in
    # (verse srs_level >= 2 means rated "got_it" at least once)
    counts = (
        db.query(QuranicVerseWord.lemma_id, func.count(func.distinct(QuranicVerseWord.verse_id)))
        .join(QuranicVerse, QuranicVerse.id == QuranicVerseWord.verse_id)
        .filter(
            QuranicVerseWord.lemma_id.in_(encountered_ids),
            QuranicVerse.srs_level >= 2,
        )
        .group_by(QuranicVerseWord.lemma_id)
        .all()
    )

    promoted = []
    for lemma_id, verse_count in counts:
        if verse_count >= QURAN_PROMOTION_THRESHOLD:
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
            if not lemma or not lemma.gates_completed_at:
                continue
            start_acquisition(db, lemma_id, source="quran")
            db.commit()
            promoted.append({
                "lemma_id": lemma_id,
                "lemma_ar": lemma.lemma_ar,
                "gloss_en": lemma.gloss_en,
                "verse_count": verse_count,
            })
            logger.info(
                "Promoted Quran lemma %s (%s) to acquiring — appeared in %d understood verses",
                lemma.lemma_ar, lemma.gloss_en, verse_count,
            )

    if promoted:
        log_interaction(
            event="quran_lemma_promotion",
            context=f"promoted {len(promoted)} lemmas",
            extra={"promoted": promoted},
        )

    return promoted


def submit_verse_review(
    db: Session,
    verse_id: int,
    rating: str,
    session_id: str | None = None,
) -> dict:
    """Process a verse review rating and update SRS state."""
    now = datetime.utcnow()

    verse = db.query(QuranicVerse).filter(QuranicVerse.id == verse_id).first()
    if not verse:
        return {"error": "verse not found"}

    old_level = verse.srs_level

    if rating == "not_yet":
        verse.srs_level = 1
        verse.next_due = now  # immediate, next session
    elif rating == "partially":
        verse.srs_level = max(verse.srs_level - 1, 1)
        verse.next_due = now + timedelta(hours=2)
    elif rating == "got_it":
        if verse.srs_level < MAX_LEARNING_LEVEL:
            interval = VERSE_INTERVALS.get(verse.srs_level, timedelta(days=21))
            verse.srs_level += 1
            verse.next_due = now + interval
        else:
            # Graduate
            verse.srs_level = GRADUATED_LEVEL
            verse.next_due = None

    verse.last_rating = rating
    verse.last_reviewed = now
    verse.times_reviewed = (verse.times_reviewed or 0) + 1
    db.commit()

    log_interaction(
        event="verse_review",
        context=f"surah:{verse.surah},ayah:{verse.ayah}",
        session_id=session_id,
        extra={"verse_id": verse_id, "rating": rating, "old_level": old_level, "new_level": verse.srs_level},
    )

    promoted = _maybe_promote_quran_lemmas(db, verse_id)

    return {
        "verse_id": verse.id,
        "surah": verse.surah,
        "ayah": verse.ayah,
        "new_level": verse.srs_level,
        "next_due": verse.next_due.isoformat() if verse.next_due else None,
        "promoted_lemmas": promoted,
    }


def _hamzat_wasl_lookup(bare_norm: str, lemma_lookup: dict[str, int]) -> int | None:
    """Try proclitic stripping + hamzat al-wasl restoration.

    In Quranic (and classical) Arabic, words starting with hamzat al-wasl
    (اسم, ابن, اثنان, امرأة, etc.) lose their initial alef when preceded by
    a proclitic: بِ + اسم → بِسم.  Standard clitic stripping yields سم but
    misses اسم because it doesn't restore the dropped alef.
    """
    for pre in PROCLITICS:
        if bare_norm.startswith(pre) and len(bare_norm) > len(pre):
            stem = bare_norm[len(pre):]
            if len(stem) >= 2 and not stem.startswith("ا"):
                # Restore hamzat al-wasl
                with_alef = "ا" + stem
                if with_alef in lemma_lookup:
                    return lemma_lookup[with_alef]
                # Also try with al- (e.g. والسم → و + السم)
                with_al = "ال" + stem
                if with_al in lemma_lookup:
                    return lemma_lookup[with_al]
    return None


def lemmatize_quran_verses(db: Session, limit: int = 20) -> int:
    """Lemmatize the next batch of unlemmatized Quran verses.

    Tokenizes each verse, looks up lemmas using the existing pipeline,
    creates QuranicVerseWord records. For unknown words, creates new
    Lemma records with source="quran" and ULK with state="encountered".

    Returns number of verses processed.
    """
    from app.services.morphology import find_best_db_match

    # Phase 1: Read — query verses and build lookup
    verses = (
        db.query(QuranicVerse)
        .filter(QuranicVerse.lemmatized_at.is_(None))
        .order_by(QuranicVerse.surah.asc(), QuranicVerse.ayah.asc())
        .limit(limit)
        .all()
    )
    if not verses:
        return 0

    all_lemmas = db.query(Lemma).all()
    lemma_lookup = build_lemma_lookup(all_lemmas)
    known_bare_forms = {normalize_alef(l.lemma_ar_bare) for l in all_lemmas}
    lemma_by_bare = {normalize_alef(l.lemma_ar_bare): l for l in all_lemmas}

    # Collect all tokens and resolve what we can without LLM
    verse_tokens: list[tuple[QuranicVerse, list[tuple[str, str, int | None, bool]]]] = []
    unknown_forms: dict[str, str] = {}  # bare_norm -> surface_form (for LLM batch)

    for verse in verses:
        tokens = tokenize_display(verse.arabic_text)
        resolved: list[tuple[str, str, int | None, bool]] = []  # (surface, bare_norm, lemma_id, is_func)

        for pos, surface in enumerate(tokens):
            clean = strip_diacritics(surface)
            # Dagger alef → alef for the *content* skeleton, so خَٰلِدُونَ resolves
            # as the participle خالدون rather than the proper-name skeleton خلدون
            # (= Khaldūn). Silent-alef demonstratives where MSA omits the alef
            # (هَٰذَا→هذا, ذَٰلِكَ→ذلك) are function words, matched below on `clean`,
            # so over-generating their alef here is harmless.
            clean_msa = strip_diacritics(normalize_quranic_to_msa(surface))
            bare_norm = normalize_alef(strip_tatweel(clean_msa))
            is_func = _is_function_word(clean)

            lemma_id = None
            if not is_func:
                lemma_id = lookup_lemma(bare_norm, lemma_lookup)
                if not lemma_id:
                    match = find_best_db_match(clean_msa, known_bare_forms)
                    if match:
                        lex_norm = normalize_alef(match["lex_bare"])
                        lemma_id = lemma_lookup.get(lex_norm)
                # Uthmani ta maftouha → modern ta marbuta fallback
                if not lemma_id and bare_norm.endswith("ت"):
                    ta_marbuta = bare_norm[:-1] + "ة"
                    lemma_id = lookup_lemma(ta_marbuta, lemma_lookup)
                # Hamzat al-wasl restoration (بسم → ب + اسم)
                if not lemma_id:
                    lemma_id = _hamzat_wasl_lookup(bare_norm, lemma_lookup)

            # Function-word-looking tokens may be cliticized content words
            # (e.g. بسم starts with بِ but is actually بِ + اسم)
            if is_func and not lemma_id:
                clitic_id = _hamzat_wasl_lookup(bare_norm, lemma_lookup)
                if clitic_id:
                    lemma_id = clitic_id
                    is_func = False

            # Check if resolved lemma is actually a function word
            if lemma_id and not is_func:
                lemma = lemma_by_bare.get(bare_norm)
                if not lemma:
                    for l in all_lemmas:
                        if l.lemma_id == lemma_id:
                            lemma = l
                            break
                if lemma and _is_function_word(lemma.lemma_ar_bare):
                    is_func = True

            if not lemma_id and not is_func and bare_norm:
                unknown_forms[bare_norm] = surface

            resolved.append((surface, bare_norm, lemma_id, is_func))

        verse_tokens.append((verse, resolved))

    # Phase 2: LLM — translate unknown words (if any)
    new_lemma_map: dict[str, int] = {}  # bare_norm -> new lemma_id
    if unknown_forms:
        new_lemma_map = _create_unknown_quran_lemmas(db, unknown_forms, all_lemmas)

    # Phase 3: Write — create QuranicVerseWord records
    now = datetime.utcnow()
    for verse, resolved in verse_tokens:
        for pos, (surface, bare_norm, lemma_id, is_func) in enumerate(resolved):
            if not lemma_id and bare_norm in new_lemma_map:
                lemma_id = new_lemma_map[bare_norm]

            vw = QuranicVerseWord(
                verse_id=verse.id,
                position=pos,
                surface_form=surface,
                lemma_id=lemma_id,
                is_function_word=is_func,
            )
            db.add(vw)

        verse.lemmatized_at = now

    db.commit()
    logger.info(f"Lemmatized {len(verses)} Quran verses, {len(unknown_forms)} unknown forms, {len(new_lemma_map)} new lemmas created")
    return len(verses)


def _camel_canonicalize_unknowns(
    unknown_forms: dict[str, str],
    lemma_lookup: dict[str, int],
) -> tuple[dict[str, int], dict[str, dict], dict[str, str]]:
    """Lemmatize unknown Quran surfaces to dictionary forms via CAMeL Tools.

    Splits the input into three buckets:
      - already_resolved: surface_bare -> existing lemma_id (CAMeL canonical
        is already in DB, link to it)
      - canonical_groups: canonical_bare -> {lex_vocalized, root, pos,
        surface_bares: [...]} for canonicals to create as new lemmas
      - fallback_forms: surface_bare -> surface (CAMeL had no analysis,
        fall back to LLM-only handling)

    Multiple inflected surfaces sharing a canonical collapse into one group.
    """
    from app.services.morphology import CAMEL_AVAILABLE, get_best_lemma_mle

    already_resolved: dict[str, int] = {}
    canonical_groups: dict[str, dict] = {}
    fallback_forms: dict[str, str] = {}

    if not CAMEL_AVAILABLE:
        return already_resolved, canonical_groups, dict(unknown_forms)

    for surface_bare, surface in unknown_forms.items():
        # Convert Mushaf presentation letters (dagger alef → ا) before CAMeL sees
        # them, else a dropped long vowel makes CAMeL pick a proper-name analysis
        # (خَٰلِدُونَ → خلدون → Khaldūn) instead of the participle خالِد.
        surface_msa = normalize_quranic_to_msa(surface)
        mle = get_best_lemma_mle(surface_msa) or get_best_lemma_mle(strip_diacritics(surface_msa))
        lex = (mle or {}).get("lex") or ""
        if not lex:
            fallback_forms[surface_bare] = surface
            continue

        # NB: strip (not convert) any dagger alef in CAMeL's lex. CAMeL spells
        # silent-alef words with a dagger (لكن → lex لٰكِنَّ); converting it to a
        # full alef would yield لاكن and miss the MSA-spelled DB lemma لكن. The
        # خالدون fix lives on the surface side above, where CAMeL already returns
        # the full-alef lex خالِد.
        lex_bare = normalize_alef(strip_diacritics(lex))
        if not lex_bare:
            fallback_forms[surface_bare] = surface
            continue

        existing_id = lemma_lookup.get(lex_bare)
        if existing_id is not None:
            already_resolved[surface_bare] = existing_id
            continue

        entry = canonical_groups.setdefault(lex_bare, {
            "lex_vocalized": lex,
            "root": (mle or {}).get("root"),
            "pos": (mle or {}).get("pos"),
            "surface_bares": [],
        })
        entry["surface_bares"].append(surface_bare)

    return already_resolved, canonical_groups, fallback_forms


def _create_unknown_quran_lemmas(
    db: Session,
    unknown_forms: dict[str, str],  # bare_norm -> surface_form
    all_lemmas: list[Lemma],
) -> dict[str, int]:
    """Create Lemma + ULK records for unknown Quran words via LLM translation.

    CAMeL Tools lemmatizes each surface to its dictionary form first, so
    conjugated verbs and inflected nouns are stored at their citation forms
    (نَزَّلَ for نَزَّلْنَا), not as fresh canonicals at the inflected form.
    Multiple inflected surfaces sharing a canonical collapse into one Lemma row.

    The LLM still supplies gloss + root + pos, but receives the canonical lex
    so the gloss matches the dictionary form.

    Returns map of bare_norm -> new lemma_id.
    """
    import re
    from app.services.llm import generate_completion
    from app.services.morphology import is_valid_root

    if not unknown_forms:
        return {}

    lemma_lookup = build_lemma_lookup(all_lemmas)
    already_resolved, canonical_groups, fallback_forms = _camel_canonicalize_unknowns(
        unknown_forms, lemma_lookup
    )

    # Build LLM batch: canonicals (preferred) + CAMeL-less fallbacks
    word_list: list[dict] = []
    for canon_bare, info in canonical_groups.items():
        word_list.append({"bare": canon_bare, "surface": info["lex_vocalized"]})
    for surface_bare, surface in fallback_forms.items():
        word_list.append({"bare": surface_bare, "surface": surface})

    prompt = (
        "For each Arabic word, provide its GENERAL Arabic meaning (not Quran-specific "
        "theological meanings) and its consonantal root.\n\n"
        "The inputs are already DICTIONARY forms wherever possible (3rd person masc "
        "sing perfect for verbs, indefinite singular for nouns). Do NOT re-lemmatize "
        "them — return the bare form unchanged. If you see an obviously inflected form "
        "(verb with -na, -tu, -tum suffix; noun with -i/-u/-an case ending; possessive "
        "with -hu, -ha etc.), still return the bare you received but mark the gloss "
        "as the infinitive/citation meaning, never the conjugated/inflected meaning.\n\n"
        "Return a JSON array with:\n"
        "- bare: the bare form (return EXACTLY as given, do not change)\n"
        "- gloss_en: general Arabic meaning (short, 1-3 words). Use the everyday "
        "meaning, not a Quran-specific divine-attribute gloss. E.g. رحيم = "
        "'merciful, compassionate' NOT 'Most Merciful'. For verbs use the infinitive "
        "('to send down'), not a conjugated meaning ('we sent down').\n"
        "- pos: part of speech (noun/verb/adj/adv/prep/particle/name)\n"
        "- root: consonantal root in dotted notation (e.g. ك.ت.ب), or null if none. "
        "For derived forms (II, III, IV, V, VIII, X etc.), give the underlying "
        "trilateral root (e.g. اِسْتَعَانَ → ع.و.ن, أَنْفَقَ → ن.ف.ق, نَزَّلَ → ن.ز.ل)\n"
        "- is_name: true if it's a proper noun\n\n"
        "Words:\n"
    )
    for w in word_list[:50]:  # cap batch size
        prompt += f"- {w['surface']} ({w['bare']})\n"

    result_map: dict[str, int] = dict(already_resolved)
    if not word_list:
        return result_map

    try:
        result = generate_completion(prompt, json_mode=True, task_type="quran_lemma_translation", model_override="claude_haiku")
        translations = result if isinstance(result, list) else result.get("words", result.get("translations", []))
    except Exception as e:
        logger.warning(f"LLM translation failed for Quran lemmas: {e}")
        return result_map

    if not isinstance(translations, list):
        return result_map

    # Index translations by bare_norm of what we sent
    trans_by_bare: dict[str, dict] = {}
    for t in translations:
        if not isinstance(t, dict):
            continue
        bn = normalize_alef(t.get("bare", ""))
        if bn:
            trans_by_bare[bn] = t

    all_roots = db.query(Root).all()
    root_by_dotted = {r.root: r for r in all_roots}
    new_lemma_ids: list[int] = []
    existing_bare_set = {normalize_alef(l.lemma_ar_bare) for l in all_lemmas}

    def _resolve_root_id(root_str: str | None) -> int | None:
        if not root_str:
            return None
        cleaned = re.sub(r'[^\u0600-\u06FF.]', '', root_str)
        if not cleaned or not is_valid_root(cleaned):
            return None
        root = root_by_dotted.get(cleaned)
        if not root:
            root = Root(root=cleaned)
            db.add(root)
            db.flush()
            root_by_dotted[cleaned] = root
        return root.root_id

    def _create_lemma(lemma_ar: str, lemma_ar_bare: str, gloss: str,
                      pos: str | None, root_id: int | None, is_name: bool) -> Lemma:
        lemma = Lemma(
            lemma_ar=lemma_ar,
            lemma_ar_bare=lemma_ar_bare,
            gloss_en=gloss,
            pos=pos,
            source="quran",
            root_id=root_id,
            word_category="proper_name" if is_name else None,
        )
        db.add(lemma)
        db.flush()
        bn = normalize_alef(lemma_ar_bare)
        existing_bare_set.add(bn)
        lemma_lookup[bn] = lemma.lemma_id
        existing_ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == lemma.lemma_id)
            .first()
        )
        if not existing_ulk:
            db.add(UserLemmaKnowledge(
                lemma_id=lemma.lemma_id,
                knowledge_state="encountered",
                source="quran",
                total_encounters=0,
            ))
        new_lemma_ids.append(lemma.lemma_id)
        return lemma

    # Create one canonical lemma per CAMeL-detected canonical bare
    for canon_bare, info in canonical_groups.items():
        t = trans_by_bare.get(canon_bare, {})
        gloss = (t.get("gloss_en") or "").strip()
        if not gloss:
            continue
        pos = t.get("pos") or info.get("pos") or "noun"
        is_name = bool(t.get("is_name", False))

        # Prefer LLM root; fall back to CAMeL root (dotting it if needed)
        root_str = t.get("root")
        if not root_str and info.get("root"):
            cr = info["root"]
            if isinstance(cr, str):
                root_str = ".".join(list(cr)) if "." not in cr and 1 < len(cr) <= 5 else cr

        # Race: another iteration in this batch may have created it
        existing_id = lemma_lookup.get(canon_bare)
        if existing_id is None:
            existing_id = resolve_existing_lemma(info["lex_vocalized"], lemma_lookup)
        if existing_id is not None:
            for sb in info["surface_bares"]:
                result_map[sb] = existing_id
            continue

        lemma = _create_lemma(
            # CAMeL's lex is the display form; its bare (canon_bare) is strip-only,
            # so keep the lex verbatim to stay consistent (don't convert a dagger
            # the bare dropped). The surface-side conversion above is what makes
            # CAMeL return the right lex (خالِد, already full-alef) in the first place.
            lemma_ar=info["lex_vocalized"],
            lemma_ar_bare=canon_bare,
            gloss=gloss,
            pos=pos,
            root_id=_resolve_root_id(root_str),
            is_name=is_name,
        )
        for sb in info["surface_bares"]:
            result_map[sb] = lemma.lemma_id

    # Fallback path for surfaces CAMeL couldn't analyze (original behaviour)
    for surface_bare, surface in fallback_forms.items():
        t = trans_by_bare.get(surface_bare, {})
        gloss = (t.get("gloss_en") or "").strip()
        if not gloss:
            continue
        pos = t.get("pos", "noun")
        is_name = bool(t.get("is_name", False))

        existing_id = lemma_lookup.get(surface_bare)
        if existing_id is None:
            existing_id = resolve_existing_lemma(surface_bare, lemma_lookup)
        if existing_id is not None:
            result_map[surface_bare] = existing_id
            continue
        if surface_bare in existing_bare_set:
            continue

        lemma = _create_lemma(
            lemma_ar=normalize_quranic_to_msa(surface),
            lemma_ar_bare=surface_bare,
            gloss=gloss,
            pos=pos,
            root_id=_resolve_root_id(t.get("root")),
            is_name=is_name,
        )
        result_map[surface_bare] = lemma.lemma_id

    db.commit()

    # Run centralized quality gates (finalize + variants + enrich + stamp)
    if new_lemma_ids:
        from app.services.lemma_quality import run_quality_gates
        run_quality_gates(db, new_lemma_ids, background_enrich=False)

    return result_map
