"""Quranic verse reading mode — selection, review, and lazy lemmatization.

Verses are scheduled with a simple SRS (not FSRS):
- Level 0: unseen
- Level 1-7: learning with increasing intervals
- Level 8: graduated (no longer shown)
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

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

# Quranic function words not in the standard FUNCTION_WORD_GLOSSES
_QURAN_FUNCTION_GLOSSES: dict[str, str] = {
    "اياك": "you (obj.)",
    "واياك": "and you (obj.)",
    "اياه": "him (obj.)",
    "اياهم": "them (obj.)",
    "اياها": "her (obj.)",
    "اياي": "me (obj.)",
    "ايانا": "us (obj.)",
    "اياكم": "you all (obj.)",
    "الم": "Alif Lam Mim",
    "الر": "Alif Lam Ra",
    "حم": "Ha Mim",
    "طه": "Ta Ha",
    "يس": "Ya Sin",
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


def _gloss_via_lemma_lookup(bare: str, db: Session) -> str | None:
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
        return lemmas[0].gloss_en
    return None


logger = logging.getLogger(__name__)

# SRS interval progression (level -> timedelta after "got_it")
VERSE_INTERVALS = {
    1: timedelta(hours=4),
    2: timedelta(hours=12),
    3: timedelta(days=1),
    4: timedelta(days=3),
    5: timedelta(days=7),
    6: timedelta(days=21),
}
MAX_LEARNING_LEVEL = 7
GRADUATED_LEVEL = 8

# Scheduling constants
MAX_NON_UNDERSTOOD_BACKLOG = 20
LEMMATIZE_AHEAD = 20
LEMMATIZE_THRESHOLD = 10  # trigger when fewer than this many lemmatized unseen remain


def select_verse_cards(
    db: Session,
    max_new: int = 3,
    max_total: int = 8,
) -> list[dict]:
    """Select verse cards for the current session.

    Returns due review verses + new verses (gated by backlog).
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

    # 3. Introduce new verses if backlog allows
    new_verses: list[QuranicVerse] = []
    remaining = max_total - len(due_verses)
    if remaining > 0 and backlog < MAX_NON_UNDERSTOOD_BACKLOG:
        new_limit = min(max_new, remaining)
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
    lemma_lookup = build_lemma_lookup(db)
    # Pre-load lemma objects for resolved IDs
    all_lemma_ids = set()

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
                bare = _normalize_quran(normalize_alef(strip_tatweel(strip_diacritics(vw.surface_form))))
                gloss = FUNCTION_WORD_GLOSSES.get(bare) or _QURAN_FUNCTION_GLOSSES.get(bare)
                if not gloss:
                    gloss = _gloss_with_pronoun_suffix(bare)
                if not gloss:
                    gloss = _gloss_via_lemma_lookup(bare, db)
                if not gloss:
                    # Full morphological lookup (handles conjugations, broken plurals via forms_json)
                    resolved_id = lookup_lemma(bare, lemma_lookup)
                    if resolved_id:
                        resolved_lemma = db.query(Lemma).get(resolved_id)
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

    return {
        "verse_id": verse.id,
        "surah": verse.surah,
        "ayah": verse.ayah,
        "new_level": verse.srs_level,
        "next_due": verse.next_due.isoformat() if verse.next_due else None,
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
            bare_norm = normalize_alef(strip_tatweel(clean))
            is_func = _is_function_word(clean)

            lemma_id = None
            if not is_func:
                lemma_id = lookup_lemma(bare_norm, lemma_lookup)
                if not lemma_id:
                    match = find_best_db_match(clean, known_bare_forms)
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


def _create_unknown_quran_lemmas(
    db: Session,
    unknown_forms: dict[str, str],  # bare_norm -> surface_form
    all_lemmas: list[Lemma],
) -> dict[str, int]:
    """Create Lemma + ULK records for unknown Quran words via LLM translation.

    Gets general Arabic glosses (not Quran-specific theological meanings),
    roots, and triggers background enrichment for forms/etymology.

    Returns map of bare_norm -> new lemma_id.
    """
    import re
    from app.services.llm import generate_completion
    from app.services.morphology import is_valid_root

    if not unknown_forms:
        return {}

    # Batch LLM call — general Arabic meanings + root extraction
    word_list = [{"bare": bare, "surface": surf} for bare, surf in unknown_forms.items()]
    prompt = (
        "For each Arabic word, provide its GENERAL Arabic meaning (not Quran-specific "
        "theological meanings) and its consonantal root.\n\n"
        "Return a JSON array with:\n"
        "- bare: the bare form (as given)\n"
        "- gloss_en: general Arabic meaning (short, 1-3 words). Use the everyday "
        "meaning, not a Quran-specific divine-attribute gloss. E.g. رحيم = "
        "'merciful, compassionate' NOT 'Most Merciful'\n"
        "- pos: part of speech (noun/verb/adj/adv/prep/particle/name)\n"
        "- root: consonantal root in dotted notation (e.g. ك.ت.ب), or null if none. "
        "For derived forms (IV, V, VIII, X etc.), give the underlying trilateral root "
        "(e.g. اِسْتَعَانَ → ع.و.ن, أَنْفَقَ → ن.ف.ق)\n"
        "- is_name: true if it's a proper noun\n\n"
        "Words:\n"
    )
    for w in word_list[:50]:  # cap batch size
        prompt += f"- {w['surface']} ({w['bare']})\n"

    try:
        result = generate_completion(prompt, json_mode=True, task_type="quran_lemma_translation")
        translations = result if isinstance(result, list) else result.get("words", result.get("translations", []))
    except Exception as e:
        logger.warning(f"LLM translation failed for Quran lemmas: {e}")
        return {}

    if not isinstance(translations, list):
        return {}

    # Load existing roots for linking
    all_roots = db.query(Root).all()
    root_by_dotted = {r.root: r for r in all_roots}

    # Create lemmas
    result_map: dict[str, int] = {}
    new_lemma_ids: list[int] = []
    existing_bare_set = {normalize_alef(l.lemma_ar_bare) for l in all_lemmas}

    for t in translations:
        bare = t.get("bare", "")
        bare_norm = normalize_alef(bare)
        if not bare_norm or bare_norm in existing_bare_set:
            continue
        gloss = t.get("gloss_en", "")
        if not gloss:
            continue
        pos = t.get("pos", "noun")
        is_name = t.get("is_name", False)

        # Resolve root
        root_id = None
        root_str = t.get("root")
        if root_str:
            cleaned = re.sub(r'[^\u0600-\u06FF.]', '', root_str)
            if cleaned and is_valid_root(cleaned):
                root = root_by_dotted.get(cleaned)
                if not root:
                    root = Root(root=cleaned)
                    db.add(root)
                    db.flush()
                    root_by_dotted[cleaned] = root
                root_id = root.root_id

        surface = unknown_forms.get(bare_norm, bare)

        lemma = Lemma(
            lemma_ar=surface,
            lemma_ar_bare=bare,
            gloss_en=gloss,
            pos=pos,
            source="quran",
            root_id=root_id,
            word_category="proper_name" if is_name else None,
        )
        db.add(lemma)
        db.flush()

        result_map[bare_norm] = lemma.lemma_id
        new_lemma_ids.append(lemma.lemma_id)
        existing_bare_set.add(bare_norm)

        # Create "encountered" ULK — does NOT enter learning pipeline
        existing_ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == lemma.lemma_id)
            .first()
        )
        if not existing_ulk:
            ulk = UserLemmaKnowledge(
                lemma_id=lemma.lemma_id,
                knowledge_state="encountered",
                source="quran",
                total_encounters=0,
            )
            db.add(ulk)

    db.commit()

    # Trigger background enrichment (forms, etymology, transliteration)
    if new_lemma_ids:
        try:
            from app.services.lemma_enrichment import enrich_lemmas_batch
            enriched = enrich_lemmas_batch(new_lemma_ids)
            logger.info(f"Enriched {len(new_lemma_ids)} new Quran lemmas: {enriched}")
        except Exception as e:
            logger.warning(f"Enrichment failed for new Quran lemmas: {e}")

    return result_map
