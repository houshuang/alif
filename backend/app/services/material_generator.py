"""Shared material generation: sentences + audio for words.

Used by both learn.py (word introduction) and ocr_service.py (post-import).
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge

logger = logging.getLogger(__name__)

# Prevent concurrent warm_sentence_cache runs from overlapping prefetches
_warm_cache_lock = threading.Lock()


def _log_pipeline(log_dir: Path, entry: dict) -> None:
    """Append a generation pipeline event to JSONL log."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"generation_pipeline_{datetime.now():%Y-%m-%d}.jsonl"
        entry["ts"] = datetime.now().isoformat()
        with open(log_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def generate_material_for_word(lemma_id: int, needed: int = 2, model_override: str = "claude_sonnet") -> int:
    """Background task: generate sentences + audio for a word.

    Uses a generate-then-write pattern to avoid holding the DB lock during
    LLM calls (which can take 15-30s via Claude CLI). Three phases:
    1. DB read: load all needed data, close DB
    2. LLM generation + validation: no DB lock held
    3. DB write: open fresh session, write results, close (milliseconds)
    """
    from app.services.llm import generate_sentences_batch, AllProvidersFailed
    from app.services.sentence_generator import (
        get_content_word_counts,
        get_avoid_words,
        sample_known_words_weighted,
        KNOWN_SAMPLE_SIZE,
    )
    from app.services.sentence_validator import (
        build_comprehensive_lemma_lookup,
        build_lemma_lookup,
        correct_mapping,
        map_tokens_to_lemmas,
        sanitize_arabic_word,
        strip_diacritics,
        tokenize_display,
        validate_sentence,
        verify_and_correct_mappings_llm,
        _log_mapping_correction,
    )
    from app.services.word_selector import get_sentence_difficulty_params

    # ── Phase 1: DB read ──
    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            return 0
        # Snapshot lemma data for use after DB close
        lemma_ar = lemma.lemma_ar
        gloss_en = lemma.gloss_en or ""
        target_lemma_id = lemma.lemma_id

        active_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.knowledge_state.in_(
                ["known", "learning", "lapsed", "acquiring"]
            ))
            .all()
        )
        all_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.knowledge_state.in_(
                ["known", "learning", "lapsed", "acquiring", "encountered"]
            ))
            .all()
        )
        known_words = [
            {"arabic": lem.lemma_ar, "english": lem.gloss_en or "", "lemma_id": lem.lemma_id, "pos": lem.pos or ""}
            for lem in active_lemmas
        ]

        lemma_lookup = build_lemma_lookup(all_lemmas)
        mapping_lookup = build_comprehensive_lemma_lookup(db)

        # Build lemma map for LLM verification (all lemmas by id)
        all_lemma_by_id = {l.lemma_id: l for l in db.query(Lemma).all()}

        content_word_counts = get_content_word_counts(db)
        sample = sample_known_words_weighted(
            known_words, content_word_counts, KNOWN_SAMPLE_SIZE, target_lemma_id=lemma_id
        )
        avoid_words = get_avoid_words(content_word_counts, known_words)
        diff_params = get_sentence_difficulty_params(db, lemma_id)
    finally:
        db.close()

    # ── Phase 2: LLM generation + validation (no DB lock) ──
    from app.config import settings as _settings
    _log_dir = _settings.log_dir

    clean_target, san_warnings = sanitize_arabic_word(lemma_ar)
    if not clean_target or " " in clean_target or "too_short" in san_warnings:
        logger.warning(f"Skipping generation for uncleanable lemma {lemma_id}: {lemma_ar!r}")
        return 0
    target_bare = strip_diacritics(clean_target)
    all_bare_forms = set(lemma_lookup.keys())

    batch_requested = needed + 2
    try:
        results = generate_sentences_batch(
            target_word=clean_target,
            target_translation=gloss_en,
            known_words=sample,
            count=batch_requested,
            difficulty_hint=diff_params["difficulty_hint"],
            avoid_words=avoid_words,
            max_words=diff_params["max_words"],
            model_override=model_override,
        )
    except AllProvidersFailed:
        logger.warning(f"LLM unavailable for sentence generation (lemma {lemma_id})")
        _log_pipeline(_log_dir, {
            "event": "batch_failed", "lemma_id": lemma_id, "target": lemma_ar,
            "model": model_override, "reason": "all_providers_failed",
        })
        return 0

    _log_pipeline(_log_dir, {
        "event": "batch_returned", "lemma_id": lemma_id, "target": lemma_ar,
        "model": model_override, "requested": batch_requested, "returned": len(results),
        "difficulty": diff_params["difficulty_hint"], "known_sample_size": len(sample),
    })

    # Phase 2a: Deterministic validation + mapping (no LLM calls)
    from app.services.sentence_validator import batch_verify_sentences

    candidates: list[dict] = []  # sentences that pass deterministic checks
    for res_idx, res in enumerate(results):
        validation = validate_sentence(
            arabic_text=res.arabic,
            target_bare=target_bare,
            known_bare_forms=all_bare_forms,
        )
        if not validation.valid:
            _log_pipeline(_log_dir, {
                "event": "validation_failed", "lemma_id": lemma_id, "target": lemma_ar,
                "arabic": res.arabic, "issues": validation.issues,
                "unknown_words": validation.unknown_words,
            })
            continue

        tokens = tokenize_display(res.arabic)
        mappings = map_tokens_to_lemmas(
            tokens=tokens,
            lemma_lookup=mapping_lookup,
            target_lemma_id=target_lemma_id,
            target_bare=target_bare,
        )
        unmapped = [m.surface_form for m in mappings if m.lemma_id is None]
        if unmapped:
            logger.warning(f"Skipping sentence with unmapped words: {unmapped}")
            _log_pipeline(_log_dir, {
                "event": "unmapped_words", "lemma_id": lemma_id, "target": lemma_ar,
                "arabic": res.arabic, "unmapped": unmapped,
            })
            continue

        has_ambiguous = any(m.alternative_lemma_ids for m in mappings)
        candidates.append({
            "arabic": res.arabic,
            "english": res.english,
            "mappings": mappings,
            "has_ambiguous": has_ambiguous,
            "validation": validation,
            "tokens": tokens,
        })

    if not candidates:
        logger.warning(f"No candidates passed deterministic validation for lemma {lemma_id}")
        return 0

    # Phase 2b: Batch LLM disambiguation + verification (single CLI call)
    lemma_map_for_verify = {
        lid: all_lemma_by_id[lid]
        for c in candidates
        for m in c["mappings"]
        for lid in [m.lemma_id] + (m.alternative_lemma_ids or [])
        if lid and lid in all_lemma_by_id
    }

    batch_results = batch_verify_sentences(candidates, lemma_map_for_verify)
    if batch_results is None:
        logger.warning(f"Batch verification unavailable for lemma {lemma_id}, discarding all")
        return 0

    # Phase 2c: Apply disambiguation + corrections
    valid_sentences: list[dict] = []
    for cand, verify_result in zip(candidates, batch_results):
        if len(valid_sentences) >= needed:
            break

        mappings = cand["mappings"]

        # Apply disambiguation choices
        pos_to_mapping = {m.position: m for m in mappings}
        for choice in verify_result.get("disambiguation", []):
            pos = choice.get("position")
            new_lid = choice.get("lemma_id")
            m = pos_to_mapping.get(pos)
            if m and new_lid and new_lid != m.lemma_id:
                # Validate the chosen lemma_id is one of the alternatives
                valid_ids = set([m.lemma_id] + (m.alternative_lemma_ids or []))
                if new_lid in valid_ids:
                    m.lemma_id = new_lid

        # Apply corrections
        corrections = verify_result.get("issues", [])
        correction_failed = False
        if corrections:
            correction_db = SessionLocal()
            try:
                for corr in corrections:
                    pos = corr.get("position")
                    m = next((m for m in mappings if m.position == pos), None)
                    if not m:
                        continue
                    new_lid = correct_mapping(
                        correction_db,
                        corr.get("correct_lemma_ar", ""),
                        corr.get("correct_gloss", ""),
                        corr.get("correct_pos", ""),
                        current_lemma_id=m.lemma_id,
                        lemma_lookup=mapping_lookup,
                    )
                    if new_lid and new_lid != m.lemma_id:
                        logger.info(
                            f"Corrected mapping pos {pos} '{m.surface_form}': "
                            f"#{m.lemma_id} → #{new_lid}"
                        )
                        m.lemma_id = new_lid
                    elif not new_lid:
                        correction_failed = True
                    else:
                        # LLM flagged mapping as wrong but correct lemma
                        # not in DB — only the same (wrong) lemma found.
                        logger.warning(
                            f"Correction for pos {pos} '{m.surface_form}' "
                            f"returned same lemma #{m.lemma_id} — rejecting"
                        )
                        correction_failed = True
                correction_db.commit()
            except Exception:
                correction_db.rollback()
                correction_failed = True
            finally:
                correction_db.close()

            _log_mapping_correction(corrections, not correction_failed, cand["arabic"])

        if correction_failed:
            logger.warning(
                f"Mapping correction failed for sentence for lemma {lemma_id}, discarding"
            )
            _log_pipeline(_log_dir, {
                "event": "correction_failed", "lemma_id": lemma_id,
                "arabic": cand["arabic"], "corrections": corrections,
            })
            continue

        from app.services.transliteration import transliterate_arabic as _translit_ar

        validation = cand["validation"]
        tokens = cand["tokens"]
        word_count = len(tokens)
        known_count = len(validation.known_words)
        func_count = len(validation.function_words)
        scaffold_pct = round(known_count / max(word_count, 1), 2)

        _log_pipeline(_log_dir, {
            "event": "sentence_accepted", "lemma_id": lemma_id, "target": lemma_ar,
            "arabic": cand["arabic"], "english": cand["english"],
            "word_count": word_count, "known_count": known_count,
            "function_count": func_count, "scaffold_pct": scaffold_pct,
            "had_corrections": len(corrections),
            "had_disambiguation": cand["has_ambiguous"],
        })

        valid_sentences.append({
            "arabic": cand["arabic"],
            "english": cand["english"],
            "transliteration": _translit_ar(cand["arabic"]) or "",
            "mappings": mappings,
        })

    # Gate: reject sentences where any lemma has no gloss
    glossed_sentences = []
    for vs in valid_sentences:
        empty_gloss = [
            m.surface_form for m in vs["mappings"]
            if m.lemma_id and m.lemma_id in all_lemma_by_id
            and not all_lemma_by_id[m.lemma_id].gloss_en
        ]
        if empty_gloss:
            logger.warning(f"Rejecting sentence with glossless lemmas: {empty_gloss}")
            _log_pipeline(_log_dir, {
                "event": "glossless_lemma", "lemma_id": lemma_id,
                "arabic": vs["arabic"], "glossless_words": empty_gloss,
            })
            continue
        glossed_sentences.append(vs)
    valid_sentences = glossed_sentences

    if not valid_sentences:
        _log_pipeline(_log_dir, {
            "event": "batch_zero_valid", "lemma_id": lemma_id, "target": lemma_ar,
            "model": model_override, "returned": len(results),
        })
        return 0

    # ── Phase 3: DB write (milliseconds) ──
    db = SessionLocal()
    stored = 0
    try:
        for vs in valid_sentences:
            sent = Sentence(
                arabic_text=vs["arabic"],
                arabic_diacritized=vs["arabic"],
                english_translation=vs["english"],
                transliteration=vs["transliteration"],
                source="llm",
                target_lemma_id=target_lemma_id,
                created_at=datetime.now(timezone.utc),
                mappings_verified_at=datetime.now(timezone.utc),
            )
            db.add(sent)
            db.flush()

            for m in vs["mappings"]:
                sw = SentenceWord(
                    sentence_id=sent.id,
                    position=m.position,
                    surface_form=m.surface_form,
                    lemma_id=m.lemma_id,
                    is_target_word=m.is_target,
                )
                db.add(sw)
            stored += 1

        db.commit()
        logger.info(f"Generated {stored} sentences for lemma {lemma_id}")
    except Exception:
        logger.exception(f"Error writing sentences for lemma {lemma_id}")
        db.rollback()
    finally:
        db.close()
    return stored


BATCH_WORD_SIZE = 15  # max words per batch generation call


def batch_generate_material(
    lemma_ids: list[int],
    count_per_word: int = 2,
    model_override: str = "claude_sonnet",
) -> dict:
    """Generate sentences for multiple words in 2 CLI calls.

    Phase 1: DB read (once for all words)
    Phase 2a: 1 Sonnet call — generate sentences for all words
    Phase 2b: Deterministic validation + mapping
    Phase 2c: 1 Haiku call — batch verify all candidates
    Phase 2d: Apply corrections
    Phase 3: DB write

    Returns: {"generated": N, "words_covered": N, "words_failed": [ids]}
    """
    from app.services.llm import generate_sentences_for_words, AllProvidersFailed
    from app.services.sentence_validator import (
        batch_verify_sentences,
        build_comprehensive_lemma_lookup,
        build_lemma_lookup,
        correct_mapping,
        map_tokens_to_lemmas,
        sanitize_arabic_word,
        strip_diacritics,
        tokenize_display,
        validate_sentence,
        _log_mapping_correction,
    )
    from app.services.sentence_generator import (
        get_content_word_counts,
        get_avoid_words,
        sample_known_words_weighted,
        KNOWN_SAMPLE_SIZE,
    )
    from app.services.transliteration import transliterate_arabic as _translit_ar
    from app.config import settings as _settings

    _log_dir = _settings.log_dir

    # ���─ Phase 1: DB read ──
    db = SessionLocal()
    try:
        target_lemmas = (
            db.query(Lemma)
            .filter(Lemma.lemma_id.in_(lemma_ids))
            .all()
        )
        lemma_by_id_target = {l.lemma_id: l for l in target_lemmas}

        active_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.knowledge_state.in_(
                ["known", "learning", "lapsed", "acquiring"]
            ))
            .all()
        )
        all_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.knowledge_state.in_(
                ["known", "learning", "lapsed", "acquiring", "encountered"]
            ))
            .all()
        )
        known_words = [
            {"arabic": lem.lemma_ar, "english": lem.gloss_en or "", "lemma_id": lem.lemma_id, "pos": lem.pos or ""}
            for lem in active_lemmas
        ]

        lemma_lookup = build_lemma_lookup(all_lemmas)
        mapping_lookup = build_comprehensive_lemma_lookup(db)
        all_lemma_by_id = {l.lemma_id: l for l in db.query(Lemma).all()}

        content_word_counts = get_content_word_counts(db)
        # Sample slightly larger pool since shared across many words
        sample = sample_known_words_weighted(
            known_words, content_word_counts, min(KNOWN_SAMPLE_SIZE + 100, len(known_words)),
        )
        avoid_words = get_avoid_words(content_word_counts, known_words)
    finally:
        db.close()

    # Build target info list — filter uncleanable words
    targets = []  # [{lemma_id, clean_target, target_bare, gloss_en}]
    for lid in lemma_ids:
        lem = lemma_by_id_target.get(lid)
        if not lem:
            continue
        clean, warnings = sanitize_arabic_word(lem.lemma_ar)
        if not clean or "too_short" in warnings or " " in clean:
            logger.warning(f"Batch: skipping uncleanable lemma {lid}: {lem.lemma_ar!r}")
            continue
        targets.append({
            "lemma_id": lid,
            "clean_target": clean,
            "target_bare": strip_diacritics(clean),
            "gloss_en": lem.gloss_en or "",
        })

    if not targets:
        return {"generated": 0, "words_covered": 0, "words_failed": lemma_ids}

    all_bare_forms = set(lemma_lookup.keys())

    # ── Phase 2a: Sonnet call — generate sentences for all words ──
    target_words_for_llm = [
        {"arabic": t["clean_target"], "english": t["gloss_en"]}
        for t in targets
    ]

    try:
        results = generate_sentences_for_words(
            target_words=target_words_for_llm,
            known_words=sample,
            count_per_word=count_per_word + 1,  # request extras for validation failures
            difficulty_hint="simple",
            model_override=model_override,
            avoid_words=avoid_words,
            max_words=10,
        )
    except (AllProvidersFailed, Exception) as e:
        logger.warning(f"Batch generation failed: {e}")
        return {"generated": 0, "words_covered": 0, "words_failed": [t["lemma_id"] for t in targets]}

    logger.info(
        "Batch generation returned %d sentences for %d words",
        len(results), len(targets),
    )

    # ─�� Phase 2b: Deterministic validation + mapping ──
    candidates = []  # validated sentences ready for LLM verification
    # Track per-word counts to know which words got coverage
    word_candidate_count: dict[int, int] = {t["lemma_id"]: 0 for t in targets}

    for res in results:
        idx = res.target_index
        if idx < 0 or idx >= len(targets):
            continue
        target = targets[idx]
        target_lemma_id = target["lemma_id"]
        target_bare = target["target_bare"]

        # Only keep up to count_per_word valid candidates per word
        if word_candidate_count[target_lemma_id] >= count_per_word:
            continue

        validation = validate_sentence(
            arabic_text=res.arabic,
            target_bare=target_bare,
            known_bare_forms=all_bare_forms,
        )
        if not validation.valid:
            _log_pipeline(_log_dir, {
                "event": "batch_validation_failed",
                "lemma_id": target_lemma_id,
                "arabic": res.arabic,
                "issues": validation.issues,
            })
            continue

        tokens = tokenize_display(res.arabic)
        mappings = map_tokens_to_lemmas(
            tokens=tokens,
            lemma_lookup=mapping_lookup,
            target_lemma_id=target_lemma_id,
            target_bare=target_bare,
        )
        unmapped = [m.surface_form for m in mappings if m.lemma_id is None]
        if unmapped:
            continue

        candidates.append({
            "arabic": res.arabic,
            "english": res.english,
            "transliteration": res.transliteration,
            "mappings": mappings,
            "has_ambiguous": any(m.alternative_lemma_ids for m in mappings),
            "validation": validation,
            "tokens": tokens,
            "target_lemma_id": target_lemma_id,
        })
        word_candidate_count[target_lemma_id] += 1

    if not candidates:
        logger.warning("Batch: no candidates passed deterministic validation")
        return {"generated": 0, "words_covered": 0, "words_failed": [t["lemma_id"] for t in targets]}

    logger.info(
        "Batch: %d/%d sentences passed deterministic validation",
        len(candidates), len(results),
    )

    # ── Phase 2c: Haiku call — batch verify all candidates ──
    lemma_map_for_verify = {
        lid: all_lemma_by_id[lid]
        for c in candidates
        for m in c["mappings"]
        for lid in [m.lemma_id] + (m.alternative_lemma_ids or [])
        if lid and lid in all_lemma_by_id
    }

    batch_results = batch_verify_sentences(candidates, lemma_map_for_verify)
    if batch_results is None:
        logger.warning("Batch verification unavailable, discarding all")
        return {"generated": 0, "words_covered": 0, "words_failed": [t["lemma_id"] for t in targets]}

    # ── Phase 2d: Apply corrections ──
    valid_sentences = []  # final sentences ready to store
    for cand, verify_result in zip(candidates, batch_results):
        mappings = cand["mappings"]

        # Apply disambiguation choices
        pos_to_mapping = {m.position: m for m in mappings}
        for choice in verify_result.get("disambiguation", []):
            pos = choice.get("position")
            new_lid = choice.get("lemma_id")
            m = pos_to_mapping.get(pos)
            if m and new_lid and new_lid != m.lemma_id:
                valid_ids = set([m.lemma_id] + (m.alternative_lemma_ids or []))
                if new_lid in valid_ids:
                    m.lemma_id = new_lid

        # Apply corrections
        corrections = verify_result.get("issues", [])
        correction_failed = False
        if corrections:
            correction_db = SessionLocal()
            try:
                for corr in corrections:
                    pos = corr.get("position")
                    m = next((m for m in mappings if m.position == pos), None)
                    if not m:
                        continue
                    new_lid = correct_mapping(
                        correction_db,
                        corr.get("correct_lemma_ar", ""),
                        corr.get("correct_gloss", ""),
                        corr.get("correct_pos", ""),
                        current_lemma_id=m.lemma_id,
                        lemma_lookup=mapping_lookup,
                    )
                    if new_lid and new_lid != m.lemma_id:
                        m.lemma_id = new_lid
                    elif not new_lid:
                        correction_failed = True
                    else:
                        logger.warning(
                            f"Batch correction for pos {pos} '{m.surface_form}' "
                            f"returned same lemma #{m.lemma_id} — rejecting"
                        )
                        correction_failed = True
                correction_db.commit()
            except Exception:
                correction_db.rollback()
                correction_failed = True
            finally:
                correction_db.close()
            _log_mapping_correction(corrections, not correction_failed, cand["arabic"])

        if correction_failed:
            continue

        # Gloss gate: reject if any lemma has no English gloss
        empty_gloss = [
            m.surface_form for m in mappings
            if m.lemma_id and m.lemma_id in all_lemma_by_id
            and not all_lemma_by_id[m.lemma_id].gloss_en
        ]
        if empty_gloss:
            continue

        valid_sentences.append({
            "arabic": cand["arabic"],
            "english": cand["english"],
            "transliteration": _translit_ar(cand["arabic"]) or cand.get("transliteration", ""),
            "mappings": mappings,
            "target_lemma_id": cand["target_lemma_id"],
        })

    # ── Phase 3: DB write ─��
    db = SessionLocal()
    stored = 0
    covered_ids: set[int] = set()
    try:
        for vs in valid_sentences:
            sent = Sentence(
                arabic_text=vs["arabic"],
                arabic_diacritized=vs["arabic"],
                english_translation=vs["english"],
                transliteration=vs["transliteration"],
                source="llm",
                target_lemma_id=vs["target_lemma_id"],
                created_at=datetime.now(timezone.utc),
                mappings_verified_at=datetime.now(timezone.utc),
            )
            db.add(sent)
            db.flush()

            for m in vs["mappings"]:
                sw = SentenceWord(
                    sentence_id=sent.id,
                    position=m.position,
                    surface_form=m.surface_form,
                    lemma_id=m.lemma_id,
                    is_target_word=m.is_target,
                )
                db.add(sw)

            stored += 1
            covered_ids.add(vs["target_lemma_id"])

        db.commit()
        logger.info(f"Batch: stored {stored} sentences for {len(covered_ids)} words")
    except Exception:
        logger.exception("Error writing batch sentences")
        db.rollback()
    finally:
        db.close()

    all_target_ids = [t["lemma_id"] for t in targets]
    words_failed = [lid for lid in all_target_ids if lid not in covered_ids]

    return {
        "generated": stored,
        "words_covered": len(covered_ids),
        "words_failed": words_failed,
    }


def store_multi_target_sentence(
    db,
    result,
    lemma_lookup: dict[str, int],
    target_bares: dict[str, int],
) -> Sentence | None:
    """Store a multi-target generated sentence with SentenceWord rows.

    All LLM calls (disambiguation, verification) happen BEFORE any DB writes
    to avoid holding the SQLite write lock during slow external calls.

    Args:
        db: SQLAlchemy session.
        result: MultiTargetGeneratedSentence with arabic, english, etc.
        lemma_lookup: Bare form -> lemma_id lookup.
        target_bares: Dict of bare_form -> lemma_id for all target words.

    Returns:
        The stored Sentence object, or None if storage failed.
    """
    from app.services.sentence_validator import (
        map_tokens_to_lemmas,
        strip_diacritics,
        strip_punctuation,
        tokenize_display,
        normalize_alef,
        _strip_clitics,
    )

    # ── Phase 1: Validate + LLM calls (no DB writes) ──

    # Build expanded target forms for matching
    target_normalized: dict[str, int] = {}
    for bare, lid in target_bares.items():
        norm = normalize_alef(bare)
        target_normalized[norm] = lid
        if not norm.startswith("ال"):
            target_normalized["ال" + norm] = lid
        if norm.startswith("ال") and len(norm) > 2:
            target_normalized[norm[2:]] = lid

    tokens = tokenize_display(result.arabic)
    primary_bare = None
    for bare, lid in target_bares.items():
        if lid == result.primary_target_lemma_id:
            primary_bare = bare
            break
    if not primary_bare:
        primary_bare = next(iter(target_bares.keys()), "")

    mappings = map_tokens_to_lemmas(
        tokens=tokens,
        lemma_lookup=lemma_lookup,
        target_lemma_id=result.primary_target_lemma_id,
        target_bare=primary_bare,
    )

    unmapped = [m.surface_form for m in mappings if m.lemma_id is None]
    if unmapped:
        logger.warning(f"Skipping multi-target sentence with unmapped words: {unmapped}")
        return None

    # Disambiguate tokens with multiple candidate lemmas using sentence context
    has_ambiguous = any(m.alternative_lemma_ids for m in mappings)
    if has_ambiguous:
        from app.services.sentence_validator import disambiguate_mappings_llm
        all_ids = set()
        for m in mappings:
            if m.lemma_id:
                all_ids.add(m.lemma_id)
            for a in (m.alternative_lemma_ids or []):
                all_ids.add(a)
        lemma_map_for_disambig = {l.lemma_id: l for l in db.query(Lemma).filter(
            Lemma.lemma_id.in_(list(all_ids))
        ).all()}
        disambig_result = disambiguate_mappings_llm(
            result.arabic, result.english, mappings, lemma_map_for_disambig,
        )
        if disambig_result is None:
            logger.warning("Disambiguation failed for ambiguous multi-target sentence, discarding")
            return None
        mappings = disambig_result

    # Verify mappings — None means verification failed, discard sentence
    from app.services.sentence_validator import (
        verify_and_correct_mappings_llm,
        correct_mapping as _correct_mapping,
        _log_mapping_correction,
    )
    lemma_map_for_verify = {l.lemma_id: l for l in db.query(Lemma).filter(
        Lemma.lemma_id.in_([m.lemma_id for m in mappings if m.lemma_id])
    ).all()}
    corrections = verify_and_correct_mappings_llm(
        result.arabic, result.english, mappings, lemma_map_for_verify,
    )
    if corrections is None:
        logger.warning("Mapping verification unavailable for multi-target sentence, discarding")
        return None
    if corrections:
        correction_failed = False
        for corr in corrections:
            pos = corr["position"]
            m = next((m for m in mappings if m.position == pos), None)
            if not m:
                continue
            new_lid = _correct_mapping(
                db,
                corr.get("correct_lemma_ar", ""),
                corr.get("correct_gloss", ""),
                corr.get("correct_pos", ""),
                current_lemma_id=m.lemma_id,
            )
            if new_lid and new_lid != m.lemma_id:
                logger.info(
                    f"Corrected mapping pos {pos} '{m.surface_form}': "
                    f"#{m.lemma_id} → #{new_lid}"
                )
                m.lemma_id = new_lid
            elif not new_lid:
                correction_failed = True

        _log_mapping_correction(corrections, not correction_failed, result.arabic)
        if correction_failed:
            logger.warning("Mapping correction failed in multi-target sentence, discarding")
            return None

    # ── Phase 2: DB write (fast, no LLM calls) ──

    from app.services.transliteration import transliterate_arabic as _translit_ar
    sent = Sentence(
        arabic_text=result.arabic,
        arabic_diacritized=result.arabic,
        english_translation=result.english,
        transliteration=_translit_ar(result.arabic) or result.transliteration,
        source="llm",
        target_lemma_id=result.primary_target_lemma_id,
        created_at=datetime.now(timezone.utc),
        mappings_verified_at=datetime.now(timezone.utc),
    )
    db.add(sent)
    db.flush()

    for m in mappings:
        is_target = m.is_target
        if not is_target:
            bare = strip_punctuation(strip_diacritics(m.surface_form))
            bare_norm = normalize_alef(bare.replace("\u0640", ""))
            if bare_norm in target_normalized:
                is_target = True
            else:
                for stem in _strip_clitics(bare_norm):
                    if normalize_alef(stem) in target_normalized:
                        is_target = True
                        break

        sw = SentenceWord(
            sentence_id=sent.id,
            position=m.position,
            surface_form=m.surface_form,
            lemma_id=m.lemma_id,
            is_target_word=is_target,
        )
        db.add(sw)

    return sent


def generate_word_audio(lemma_id: int) -> None:
    """Background task: generate TTS audio for the word itself."""
    from app.services.tts import (
        DEFAULT_VOICE_ID,
        TTSError,
        TTSKeyMissing,
        cache_key_for,
        generate_and_cache,
        get_cached_path,
    )

    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma or lemma.audio_url:
            return

        key = cache_key_for(lemma.lemma_ar, DEFAULT_VOICE_ID)
        if get_cached_path(key):
            lemma.audio_url = f"/api/tts/audio/{key}.mp3"
            db.commit()
            return

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                generate_and_cache(lemma.lemma_ar, DEFAULT_VOICE_ID, cache_key=key)
            )
            lemma.audio_url = f"/api/tts/audio/{key}.mp3"
            db.commit()
        except (TTSError, TTSKeyMissing):
            logger.warning(f"TTS failed for word {lemma_id}")
        finally:
            loop.close()
    except Exception:
        logger.exception(f"Error generating word audio for lemma {lemma_id}")
    finally:
        db.close()


MIN_SENTENCES_PER_WORD = 3
PIPELINE_CAP = 2000  # safety valve only — tier-based lifecycle manages pool size


def rotate_stale_sentences(db, min_shown: int = 1, tier_lookup: dict | None = None) -> int:
    """Retire sentences based on tier lifecycle and scaffold staleness.

    Two retirement paths:
    1. Tier-4 excess: sentences for words not due for 72h+ are retired down to
       their tier floor (0), keeping only never-shown sentences < 24h old.
    2. Scaffold staleness: sentences where all scaffold words are fully known
       (no acquiring words) are retired down to the tier floor.

    The tier floor controls retention: tier 1 keeps ≥2, tier 2 ≥1, tier 3-4 ≥0.
    Returns the number of sentences retired.
    """
    from scripts.rotate_stale_sentences import compute_diversity_score
    from datetime import timedelta

    if tier_lookup is None:
        from app.services.pipeline_tiers import compute_word_tiers, build_tier_lookup
        word_tiers = compute_word_tiers(db)
        tier_lookup = build_tier_lookup(word_tiers)

    sentences = db.query(Sentence).filter(Sentence.is_active == True).all()  # noqa: E712
    all_sw = db.query(SentenceWord).all()
    all_ulk = db.query(UserLemmaKnowledge).all()

    knowledge_map = {k.lemma_id: k for k in all_ulk}
    sw_by_sentence: dict[int, list] = {}
    for sw in all_sw:
        sw_by_sentence.setdefault(sw.sentence_id, []).append(sw)

    active_per_target: dict[int | None, int] = {}
    for s in sentences:
        active_per_target[s.target_lemma_id] = active_per_target.get(s.target_lemma_id, 0) + 1

    now = datetime.utcnow()
    age_cutoff = now - timedelta(hours=24)

    retirable: list[tuple] = []
    for sent in sentences:
        target_id = sent.target_lemma_id
        wt = tier_lookup.get(target_id) if target_id else None
        tier = wt.tier if wt else 4

        # Path 1: Tier-4 excess — retire shown sentences or old never-shown ones
        if tier >= 4 and (sent.times_shown or 0) >= 1:
            retirable.append((sent, 0))  # priority 0 = retire first
            continue
        if tier >= 4 and sent.created_at and sent.created_at < age_cutoff:
            retirable.append((sent, 1))
            continue

        # Path 2: Scaffold staleness — all scaffold words fully known
        sws = sw_by_sentence.get(sent.id, [])
        if not sws:
            continue
        scores = compute_diversity_score(sws, knowledge_map)
        is_stale = (
            scores["acquiring_count"] == 0
            and scores["scaffold_count"] >= 2
            and (sent.times_shown or 0) >= min_shown
        )
        if is_stale:
            retirable.append((sent, 2 if (sent.times_shown or 0) >= 1 else 3))

    retirable.sort(key=lambda x: (x[1], getattr(x[0], 'last_reading_shown_at', None) or datetime.min))

    retire_per_target: dict[int | None, int] = {}
    retired = 0
    for sent, _ in retirable:
        target_id = sent.target_lemma_id
        already_retiring = retire_per_target.get(target_id, 0)
        active = active_per_target.get(target_id, 0)
        wt = tier_lookup.get(target_id) if target_id else None
        # Use tier floor directly — no min_active override
        effective_floor = wt.cap_floor if wt else 0
        if active - already_retiring > effective_floor:
            sent.is_active = False
            retire_per_target[target_id] = already_retiring + 1
            retired += 1

    if retired:
        db.commit()
        logger.info(f"Rotated {retired} sentences (tier lifecycle + staleness)")
        from app.services.activity_log import log_activity
        log_activity(
            db,
            event_type="sentences_retired",
            summary=f"Rotated {retired} sentences (tier lifecycle + staleness)",
            detail={"retired": retired, "total_active": len(sentences)},
        )

    return retired


def warm_sentence_cache(llm_model: str = "claude_sonnet") -> dict:
    """Background task: pre-generate sentences for words likely in the next session.

    Uses a generate-then-write pattern to avoid holding the DB lock during
    LLM calls (which can take 15-30s via Claude CLI). Three phases:
    1. DB read: identify gap words, build lookups, close DB
    2. LLM generation: generate sentences (no DB lock held)
    3. DB write: store results (milliseconds)

    Args:
        llm_model: Model override for sentence generation. Default: claude_sonnet (free via CLI).
    """
    # Prevent concurrent runs from overlapping prefetches — skip if already running
    if not _warm_cache_lock.acquire(blocking=False):
        logger.info("Warm cache: skipping, another run already in progress")
        return {"skipped": True}
    try:
        return _warm_sentence_cache_impl(llm_model)
    finally:
        _warm_cache_lock.release()


def _warm_sentence_cache_impl(llm_model: str = "claude_sonnet") -> dict:
    from app.services.cohort_service import get_focus_cohort
    from app.services.word_selector import select_next_words
    from app.services.topic_service import ensure_active_topic
    from app.services.sentence_generator import (
        group_words_for_multi_target,
        generate_validated_sentences_multi_target,
    )
    from app.services.sentence_validator import (
        build_lemma_lookup,
        build_comprehensive_lemma_lookup,
        strip_diacritics,
    )
    from sqlalchemy import func
    from sqlalchemy.orm import joinedload

    stats = {"cohort_gaps": 0, "intro_gaps": 0, "generated": 0, "multi_target": 0, "rotated": 0}

    # ── Phase 1: DB read ──
    db = SessionLocal()
    try:
        # Compute due-date tiers for tier-aware decisions
        from app.services.pipeline_tiers import compute_word_tiers, build_tier_lookup
        word_tiers = compute_word_tiers(db)
        tier_lookup = build_tier_lookup(word_tiers)

        # Tier-based lifecycle: rotate stale and tier-4 excess sentences
        rotated = rotate_stale_sentences(db, tier_lookup=tier_lookup)
        stats["rotated"] = rotated

        # Safety valve only — skip if way over cap (shouldn't happen with tier lifecycle)
        total_active = (
            db.query(func.count(Sentence.id))
            .filter(Sentence.is_active == True)
            .scalar() or 0
        )
        if total_active >= PIPELINE_CAP:
            logger.warning(f"Warm cache: over safety cap after rotation ({total_active} >= {PIPELINE_CAP}), skipping")
            return stats

        # Collect all words needing sentences
        gap_word_ids: list[int] = []

        # 1. Focus cohort words below their tier-based sentence target
        cohort = get_focus_cohort(db)
        if cohort:
            sentence_counts = dict(
                db.query(Sentence.target_lemma_id, func.count(Sentence.id))
                .filter(
                    Sentence.target_lemma_id.in_(cohort),
                    Sentence.is_active == True,
                )
                .group_by(Sentence.target_lemma_id)
                .all()
            )
            gaps = []
            for lid in cohort:
                wt = tier_lookup.get(lid)
                target = wt.backfill_target if wt else MIN_SENTENCES_PER_WORD
                if target > 0 and sentence_counts.get(lid, 0) < target:
                    gaps.append(lid)
            stats["cohort_gaps"] = len(gaps)
            gap_word_ids.extend(gaps[:20])

        # 2. Likely auto-introduction candidates (not yet in tier system, use default)
        active_topic = ensure_active_topic(db)
        candidates = select_next_words(db, count=5, domain=active_topic)
        for cand in candidates:
            lid = cand["lemma_id"]
            if lid in gap_word_ids:
                continue
            count = (
                db.query(func.count(Sentence.id))
                .filter(Sentence.target_lemma_id == lid, Sentence.is_active == True)
                .scalar() or 0
            )
            if count < MIN_SENTENCES_PER_WORD:
                gap_word_ids.append(lid)
                stats["intro_gaps"] = stats.get("intro_gaps", 0) + 1

        # 3. Recency-exhausted words: have enough sentences but ALL shown in last 24h
        from sqlalchemy import or_
        now = datetime.now(timezone.utc)
        recency_cutoff = now - timedelta(days=1)
        gap_word_set = set(gap_word_ids)
        recency_exhausted_count = 0
        MAX_RECENCY_EXHAUSTED = 20
        if cohort:
            for lid in cohort:
                if lid in gap_word_set:
                    continue
                if recency_exhausted_count >= MAX_RECENCY_EXHAUSTED:
                    break
                active_count = sentence_counts.get(lid, 0)
                wt = tier_lookup.get(lid)
                target = wt.backfill_target if wt else MIN_SENTENCES_PER_WORD
                if active_count < target:
                    continue  # already a gap word, handled above
                fresh_count = (
                    db.query(func.count(Sentence.id))
                    .filter(
                        Sentence.target_lemma_id == lid,
                        Sentence.is_active == True,
                        or_(
                            Sentence.last_reading_shown_at.is_(None),
                            Sentence.last_reading_shown_at < recency_cutoff,
                        ),
                    )
                    .scalar() or 0
                )
                if fresh_count == 0:
                    gap_word_ids.append(lid)
                    recency_exhausted_count += 1
            if recency_exhausted_count:
                stats["recency_exhausted"] = recency_exhausted_count
                logger.info(f"Warm cache: {recency_exhausted_count} recency-exhausted words need fresh sentences")

        # Sort gap words by tier urgency (most urgent first)
        gap_word_ids.sort(key=lambda lid: (
            tier_lookup[lid].tier if lid in tier_lookup else 4,
            tier_lookup[lid].due_dt or datetime.max.replace(tzinfo=timezone.utc) if lid in tier_lookup else datetime.max.replace(tzinfo=timezone.utc),
        ))

        if not gap_word_ids:
            logger.info(f"Warm cache: no gaps found")
            return stats

        # Load lemmas for gap words
        gap_lemmas = (
            db.query(Lemma).options(joinedload(Lemma.root))
            .filter(Lemma.lemma_id.in_(gap_word_ids))
            .all()
        )
        lemma_by_id = {l.lemma_id: l for l in gap_lemmas}

        # Build word dicts for multi-target grouping
        word_dicts = []
        for lid in gap_word_ids:
            lem = lemma_by_id.get(lid)
            if lem:
                word_dicts.append({
                    "lemma_id": lid,
                    "lemma_ar": lem.lemma_ar,
                    "gloss_en": lem.gloss_en or "",
                    "root_id": lem.root_id,
                })

        # Build known words for LLM prompt
        active_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.knowledge_state.in_(
                ["acquiring", "learning", "known", "lapsed"]
            ))
            .all()
        )
        known_words = [
            {"arabic": l.lemma_ar, "english": l.gloss_en or "", "lemma_id": l.lemma_id, "pos": l.pos or ""}
            for l in active_lemmas
        ]
        lemma_lookup = build_lemma_lookup(active_lemmas)
        mapping_lookup = build_comprehensive_lemma_lookup(db)
    except Exception:
        logger.exception("Error in warm_sentence_cache (read phase)")
        return stats
    finally:
        db.close()

    # ── Phase 2: LLM generation (no DB lock) ──
    groups = group_words_for_multi_target(word_dicts)
    all_results: list[tuple[list[dict], dict[str, int]]] = []  # (results, target_bares) per group

    for group in groups:
        try:
            results = generate_validated_sentences_multi_target(
                target_words=group,
                known_words=known_words,
                count=len(group),
                difficulty_hint="beginner",
                max_words=12,
                lemma_lookup=lemma_lookup,
                model_override=llm_model,
            )
            target_bares = {strip_diacritics(tw["lemma_ar"]): tw["lemma_id"] for tw in group}
            all_results.append((results, target_bares))
        except Exception:
            logger.warning(f"Warm cache: multi-target failed for group")

    # Single-target fallback for any remaining ungrouped words
    covered = set()
    for group in groups:
        for w in group:
            covered.add(w["lemma_id"])
    remaining = [lid for lid in gap_word_ids if lid not in covered]

    for lid in remaining:
        try:
            generate_material_for_word(lid, needed=MIN_SENTENCES_PER_WORD, model_override=llm_model)
            stats["generated"] += 1
        except Exception:
            logger.warning(f"Warm cache: failed for word {lid}")

    # ── Phase 3: DB write (milliseconds) ──
    if all_results:
        db = SessionLocal()
        try:
            for results, target_bares in all_results:
                for mres in results:
                    sent = store_multi_target_sentence(db, mres, mapping_lookup, target_bares)
                    if sent:
                        stats["generated"] += 1
                        stats["multi_target"] += 1
            db.commit()
        except Exception:
            logger.warning("Warm cache: failed to write multi-target sentences")
            db.rollback()
        finally:
            db.close()

    # ── Phase 4: Verify unverified active sentences (batch catch-up) ──
    # IMPORTANT: Don't hold DB session during LLM calls — they can hang for minutes
    # and block all other writes (caused "database is locked" cascades).
    MAX_VERIFY_BATCH = 20
    db = SessionLocal()
    try:
        unverified_ids = [
            row[0] for row in db.query(Sentence.id)
            .filter(
                Sentence.is_active == True,
                Sentence.mappings_verified_at.is_(None),
            )
            .limit(MAX_VERIFY_BATCH)
            .all()
        ]
    except Exception:
        logger.warning("Warm cache: verification read phase failed")
        unverified_ids = []
    finally:
        db.close()

    if unverified_ids:
        db = SessionLocal()
        try:
            v_stats = verify_sentence_mappings(db, unverified_ids)
            stats["verified"] = v_stats.get("verified", 0)
            stats["verify_corrected"] = v_stats.get("corrected", 0)
        except Exception:
            logger.warning("Warm cache: verification phase failed")
        finally:
            db.close()

    # ── Phase 5: Backfill empty glosses on active lemmas ──
    # Catches any lemmas that slipped through import without English translations.
    # Only processes a small batch per warm-cache run to stay fast.
    from sqlalchemy import or_ as sa_or_
    MAX_GLOSS_BACKFILL = 10
    db = SessionLocal()
    try:
        empty_gloss_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
            .filter(
                Lemma.canonical_lemma_id.is_(None),
                sa_or_(Lemma.gloss_en.is_(None), Lemma.gloss_en == ""),
                UserLemmaKnowledge.knowledge_state.in_(["acquiring", "known", "lapsed", "learning"]),
            )
            .limit(MAX_GLOSS_BACKFILL)
            .all()
        )
        if empty_gloss_lemmas:
            from app.services.llm import generate_completion
            words_for_llm = [
                f"- id={l.lemma_id}, word={l.lemma_ar}, pos={l.pos or 'unknown'}"
                for l in empty_gloss_lemmas
            ]
            prompt = (
                "Translate these Arabic words to concise English dictionary-form glosses.\n"
                "Verbs: infinitive ('to write'). Nouns: singular ('book'). Adj: base ('big').\n\n"
                + "\n".join(words_for_llm)
                + '\n\nReturn JSON array: [{"id": <lemma_id>, "gloss": "english"}]'
            )
            try:
                result = generate_completion(
                    prompt=prompt,
                    system_prompt="Translate Arabic to English. Concise dictionary glosses (1-3 words). JSON only.",
                    json_mode=True,
                    temperature=0.1,
                    task_type="backfill_glosses",
                )
                items = result if isinstance(result, list) else result.get("words", result.get("translations", []))
                lemma_map = {l.lemma_id: l for l in empty_gloss_lemmas}
                filled = 0
                if isinstance(items, list):
                    for item in items:
                        lid = item.get("id")
                        gloss = (item.get("gloss") or item.get("english", "")).strip()
                        if lid and gloss and lid in lemma_map:
                            lemma_map[lid].gloss_en = gloss
                            filled += 1
                    if filled:
                        db.commit()
                stats["glosses_backfilled"] = filled
                logger.info(f"Warm cache Phase 5: backfilled {filled}/{len(empty_gloss_lemmas)} empty glosses")
            except Exception:
                logger.warning("Warm cache: gloss backfill LLM call failed")
    except Exception:
        logger.warning("Warm cache: gloss backfill read phase failed")
    finally:
        db.close()

    logger.info(f"Warm cache complete: {stats}")
    return stats


def verify_sentence_mappings(db, sentence_ids: list[int]) -> dict:
    """Verify mappings for existing sentences in a single batched LLM call.

    Structured as read → LLM (no DB) → write to avoid holding the SQLite
    write lock during potentially slow LLM calls.

    Returns {"verified": N, "corrected": N, "failed": N}.
    """
    from app.services.sentence_validator import correct_mapping
    from app.services.llm import generate_completion, AllProvidersFailed

    if not sentence_ids:
        return {"verified": 0, "corrected": 0, "failed": 0}

    # ── Read phase: collect all data into plain dicts ──
    sentences = (
        db.query(Sentence)
        .filter(
            Sentence.id.in_(sentence_ids),
            Sentence.mappings_verified_at.is_(None),
        )
        .all()
    )
    if not sentences:
        return {"verified": 0, "corrected": 0, "failed": 0}

    all_sw = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id.in_([s.id for s in sentences]))
        .order_by(SentenceWord.sentence_id, SentenceWord.position)
        .all()
    )
    sw_by_sentence: dict[int, list] = {}
    for sw in all_sw:
        sw_by_sentence.setdefault(sw.sentence_id, []).append(sw)

    all_lemma_ids = {sw.lemma_id for sw in all_sw if sw.lemma_id}
    lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(all_lemma_ids)).all() if all_lemma_ids else []
    lemma_by_id = {l.lemma_id: l for l in lemmas}

    stats = {"verified": 0, "corrected": 0, "failed": 0}

    # Build prompt data — track which sentences need LLM vs are trivially OK
    sentence_blocks = []
    # sent_id → idx mapping for LLM results
    sent_id_by_idx: dict[int, int] = {}
    trivially_ok_ids: list[int] = []

    for idx, sent in enumerate(sentences):
        sws = sw_by_sentence.get(sent.id, [])
        if not sws:
            trivially_ok_ids.append(sent.id)
            stats["verified"] += 1
            continue

        word_lines = []
        for sw in sws:
            if sw.lemma_id and sw.lemma_id in lemma_by_id:
                lem = lemma_by_id[sw.lemma_id]
                word_lines.append(
                    f"    {sw.position}: {sw.surface_form} → {lem.lemma_ar} ({lem.gloss_en or '?'})"
                )
        if not word_lines:
            trivially_ok_ids.append(sent.id)
            stats["verified"] += 1
            continue

        sent_id_by_idx[idx] = sent.id
        sentence_blocks.append(
            f"[{idx}] Arabic: {sent.arabic_text}\n"
            f"    English: {sent.english_translation or '?'}\n"
            f"    Mappings:\n" + "\n".join(word_lines)
        )

    # Mark trivially-OK sentences now
    if trivially_ok_ids:
        now = datetime.now(timezone.utc)
        db.query(Sentence).filter(Sentence.id.in_(trivially_ok_ids)).update(
            {Sentence.mappings_verified_at: now}, synchronize_session="fetch"
        )
        try:
            db.commit()
        except Exception:
            db.rollback()

    if not sentence_blocks:
        return stats

    # ── LLM phase: no DB session needed ──
    prompt = f"""Check these {len(sentence_blocks)} Arabic sentences for wrong word-lemma mappings.

For each sentence, check if any word's lemma gloss doesn't match what the word means in context.

Flag as WRONG:
- Gloss doesn't match the word's meaning in this sentence
- A clitic prefix (و/ف/ب/ل/ك) wrongly stripped from a root letter
- Wrong part of speech (noun vs verb homograph)

Do NOT flag:
- Conjugated verbs mapped to dictionary form (when meaning matches)
- Plural/feminine/dual mapped to base lemma
- Possessive suffixes mapped to base noun

Return JSON: {{"flagged": []}} if all OK, or:
{{"flagged": [
  {{"sentence": <index>, "position": <word position>, "surface": "<word>", "current_gloss": "<wrong>", "correct_lemma_ar": "<bare>", "correct_gloss": "<correct>", "correct_pos": "<pos>"}}
]}}

Sentences:
{chr(10).join(sentence_blocks)}"""

    system = "You are an Arabic morphology expert. Check mappings against English translations. Only flag clear errors."
    flagged = None
    for model in ("claude_haiku",):
        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=system,
                json_mode=True,
                temperature=0.0,
                model_override=model,
                timeout=30,
                task_type="mapping_verification_batch",
            )
            flagged = result.get("flagged", [])
            if not isinstance(flagged, list):
                flagged = []
            break
        except (AllProvidersFailed, Exception) as e:
            logger.warning(f"Batch mapping verification failed with {model}: {e}")
            continue

    if flagged is None:
        logger.error("Batch mapping verification failed on ALL models — sentences stay unverified")
        stats["failed"] = len(sent_id_by_idx)
        return stats

    # ── Write phase: apply corrections using fresh DB reads ──
    now = datetime.now(timezone.utc)
    flagged_by_idx: dict[int, list[dict]] = {}
    for flag in flagged:
        if not isinstance(flag, dict) or "sentence" not in flag:
            continue
        sidx = int(flag["sentence"])
        flagged_by_idx.setdefault(sidx, []).append(flag)

    for idx, sent_id in sent_id_by_idx.items():
        corrections = flagged_by_idx.get(idx)
        if corrections:
            sws = sw_by_sentence.get(sent_id, [])
            has_unfixable = False
            for corr in corrections:
                pos = corr.get("position")
                if pos is None:
                    continue
                pos = int(pos)
                sw = next((s for s in sws if s.position == pos), None)
                if not sw:
                    continue
                new_lid = correct_mapping(
                    db,
                    corr.get("correct_lemma_ar", ""),
                    corr.get("correct_gloss", ""),
                    corr.get("correct_pos", ""),
                    current_lemma_id=sw.lemma_id,
                )
                if new_lid and new_lid != sw.lemma_id:
                    logger.info(
                        f"Batch verify: sentence {sent_id} pos {pos} "
                        f"'{sw.surface_form}': #{sw.lemma_id} → #{new_lid}"
                    )
                    sw.lemma_id = new_lid
                elif not new_lid:
                    has_unfixable = True

            if has_unfixable:
                sent_obj = db.query(Sentence).get(sent_id)
                if sent_obj:
                    sent_obj.is_active = False
                logger.info(f"Batch verify: retired sentence {sent_id} — unfixable mapping")
                stats["failed"] += 1
            else:
                stats["corrected"] += 1

    # Mark all checked sentences as verified
    all_checked_ids = list(sent_id_by_idx.values())
    if all_checked_ids:
        db.query(Sentence).filter(Sentence.id.in_(all_checked_ids)).update(
            {Sentence.mappings_verified_at: now}, synchronize_session="fetch"
        )

    stats["verified"] += len(sent_id_by_idx)

    try:
        db.commit()
    except Exception:
        logger.exception("Failed to commit verification results")
        db.rollback()
        stats["failed"] += stats["verified"]
        stats["verified"] = 0

    if stats["corrected"] or stats["failed"]:
        logger.info(f"Batch mapping verification: {stats}")

    return stats


def _generate_audio_for_lemma(db, lemma_id: int) -> None:
    """Generate TTS audio for sentences of a word."""
    from app.services.tts import (
        DEFAULT_VOICE_ID,
        TTSError,
        TTSKeyMissing,
        cache_key_for,
        generate_and_cache,
        get_cached_path,
    )

    sentences = (
        db.query(Sentence)
        .filter(
            Sentence.target_lemma_id == lemma_id,
            Sentence.audio_url.is_(None),
        )
        .all()
    )

    if not sentences:
        return

    loop = asyncio.new_event_loop()
    try:
        for sent in sentences:
            key = cache_key_for(sent.arabic_text, DEFAULT_VOICE_ID)
            if get_cached_path(key):
                sent.audio_url = f"/api/tts/audio/{key}.mp3"
                continue
            try:
                loop.run_until_complete(
                    generate_and_cache(sent.arabic_text, DEFAULT_VOICE_ID, cache_key=key)
                )
                sent.audio_url = f"/api/tts/audio/{key}.mp3"
            except (TTSError, TTSKeyMissing):
                logger.warning(f"TTS failed for sentence {sent.id}")
                continue

        db.commit()
    finally:
        loop.close()
