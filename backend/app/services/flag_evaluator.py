"""Background LLM evaluation for flagged content.

Uses GPT-5.2 for gloss/sentence evaluation, Claude CLI for word mapping evaluation.
Auto-fixes high-confidence corrections, retires unfixable sentences.
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ContentFlag, Lemma, Sentence, SentenceWord
from app.services.activity_log import log_activity
from app.services.llm import generate_completion, LLMError


def evaluate_flag(flag_id: int) -> None:
    """Background task: evaluate flagged content via LLM and auto-fix if possible."""
    db = SessionLocal()
    try:
        flag = db.query(ContentFlag).filter(ContentFlag.id == flag_id).first()
        if not flag or flag.status != "pending":
            return

        flag.status = "reviewing"
        db.commit()

        if flag.content_type == "word_gloss":
            _evaluate_word_gloss(db, flag)
        elif flag.content_type == "word_mapping":
            _evaluate_word_mapping(db, flag)
        elif flag.content_type.startswith("sentence_"):
            _evaluate_sentence(db, flag)

        db.commit()
    except Exception as e:
        logger.exception("Flag evaluation failed for flag_id=%s", flag_id)
        db.rollback()
        flag = db.query(ContentFlag).filter(ContentFlag.id == flag_id).first()
        if flag:
            flag.status = "dismissed"
            flag.resolution_note = f"Evaluation error: {e}"
            flag.resolved_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def _evaluate_word_gloss(db: Session, flag: ContentFlag) -> None:
    lemma = db.query(Lemma).filter(Lemma.lemma_id == flag.lemma_id).first()
    if not lemma:
        flag.status = "dismissed"
        flag.resolution_note = "Lemma not found"
        flag.resolved_at = datetime.now(timezone.utc)
        return

    flag.original_value = lemma.gloss_en

    root_info = ""
    if lemma.root:
        root_info = f", root: {lemma.root.root} ({lemma.root.core_meaning_en})"

    prompt = f"""Evaluate this Arabic word's English translation.

Arabic: {lemma.lemma_ar}
Bare form: {lemma.lemma_ar_bare}
Current translation: "{lemma.gloss_en}"
POS: {lemma.pos}{root_info}
Transliteration: {lemma.transliteration_ala_lc or ""}

Is the English translation correct and natural? If not, provide a better one.

Respond with JSON:
{{"correct": true/false, "suggested_gloss": "better translation if incorrect", "confidence": 0.0-1.0, "explanation": "brief reason"}}"""

    try:
        result = generate_completion(prompt, model_override="openai", temperature=0.3, task_type="flag_evaluation")
    except LLMError:
        flag.status = "dismissed"
        flag.resolution_note = "LLM evaluation failed"
        flag.resolved_at = datetime.now(timezone.utc)
        return

    correct = result.get("correct", True)
    confidence = result.get("confidence", 0.0)
    suggested = result.get("suggested_gloss", "")
    explanation = result.get("explanation", "")

    if not correct and confidence >= 0.8 and suggested:
        flag.corrected_value = suggested
        flag.status = "fixed"
        flag.resolution_note = explanation
        lemma.gloss_en = suggested
        _log_activity(db, "flag_resolved",
                      f"Fixed translation: {lemma.lemma_ar_bare} '{flag.original_value}' → '{suggested}'",
                      {"flag_id": flag.id, "lemma_id": lemma.lemma_id})
    elif correct:
        flag.status = "dismissed"
        flag.resolution_note = f"Translation appears correct: {explanation}"
        _log_activity(db, "flag_resolved",
                      f"Translation confirmed correct: {lemma.lemma_ar_bare} = '{lemma.gloss_en}'",
                      {"flag_id": flag.id, "lemma_id": lemma.lemma_id})
    else:
        flag.status = "dismissed"
        flag.resolution_note = f"Low confidence ({confidence}): {explanation}. Suggested: {suggested}"
        _log_activity(db, "flag_resolved",
                      f"Flag reviewed but not auto-fixed: {lemma.lemma_ar_bare} (confidence {confidence})",
                      {"flag_id": flag.id, "lemma_id": lemma.lemma_id})

    flag.resolved_at = datetime.now(timezone.utc)


def _evaluate_word_mapping(db: Session, flag: ContentFlag) -> None:
    """Evaluate word-lemma mappings in a flagged sentence using Claude CLI."""
    sentence = db.query(Sentence).filter(Sentence.id == flag.sentence_id).first()
    if not sentence:
        flag.status = "dismissed"
        flag.resolution_note = "Sentence not found"
        flag.resolved_at = datetime.now(timezone.utc)
        return

    words = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence.id)
        .order_by(SentenceWord.position)
        .all()
    )
    if not words:
        flag.status = "dismissed"
        flag.resolution_note = "No words found for sentence"
        flag.resolved_at = datetime.now(timezone.utc)
        return

    # Build current mapping description for the LLM
    lemma_ids = [w.lemma_id for w in words if w.lemma_id]
    lemmas_by_id = {}
    if lemma_ids:
        for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all():
            lemmas_by_id[lemma.lemma_id] = lemma

    word_lines = []
    for w in words:
        lemma = lemmas_by_id.get(w.lemma_id)
        if lemma:
            word_lines.append(
                f"  pos {w.position}: \"{w.surface_form}\" → lemma \"{lemma.lemma_ar_bare}\" (gloss: \"{lemma.gloss_en}\", POS: {lemma.pos or '?'})"
            )
        else:
            word_lines.append(f"  pos {w.position}: \"{w.surface_form}\" → (unmapped)")

    flag.original_value = json.dumps(
        {w.position: {"surface": w.surface_form, "lemma_id": w.lemma_id} for w in words},
        ensure_ascii=False,
    )

    prompt = f"""Evaluate the word-to-lemma mappings in this Arabic sentence.

Arabic: {sentence.arabic_diacritized or sentence.arabic_text}
English: {sentence.english_translation}

Current mappings:
{chr(10).join(word_lines)}

For each mapping, check if the surface form is correctly mapped to its lemma.

ONLY flag a mapping as wrong if:
- The word in context is a COMPLETELY DIFFERENT word from the assigned lemma (e.g. حَوْلَ "around" mapped to حَالَ "to change")
- A clitic combination was misidentified (e.g. بِأَنَّ "with that" mapped to بَانَ "to appear")
- The word's POS is clearly wrong (e.g. a verb mapped to an unrelated noun with same consonants)

Do NOT flag:
- Conjugated verbs mapped to their base/past-tense form (يَكْتُبُ → كَتَبَ is correct)
- Plurals mapped to singular or vice versa
- Feminine mapped to masculine lemma
- Words with possessive/preposition suffixes mapped to base word
- Masdar mapped to its verb or vice versa

Respond with JSON:
{{"issues": [
  {{"position": <int>, "surface_form": "<word>", "current_lemma_wrong": true, "correct_lemma_ar": "<bare Arabic>", "correct_gloss": "<English>", "correct_pos": "<noun/verb/adj/adv/prep/conj/pron/particle>", "explanation": "<brief>"}}
], "all_correct": true/false}}

If all mappings look correct, return {{"issues": [], "all_correct": true}}."""

    try:
        result = generate_completion(
            prompt,
            system_prompt="You are an Arabic morphology expert. Be conservative — only flag clear mismatches.",
            model_override="claude_haiku",
            temperature=0.0,
            task_type="flag_evaluation",
        )
    except LLMError:
        flag.status = "dismissed"
        flag.resolution_note = "LLM evaluation failed"
        flag.resolved_at = datetime.now(timezone.utc)
        return

    all_correct = result.get("all_correct", True)
    issues = result.get("issues", [])

    if all_correct or not issues:
        flag.status = "dismissed"
        flag.resolution_note = "All word mappings appear correct"
        _log_activity(db, "flag_resolved",
                      f"Word mappings confirmed correct for sentence #{sentence.id}",
                      {"flag_id": flag.id, "sentence_id": sentence.id})
        flag.resolved_at = datetime.now(timezone.utc)
        return

    # Try to fix each flagged position
    from app.services.sentence_validator import normalize_arabic

    changes = []
    for issue in issues:
        pos = issue.get("position")
        correct_ar = issue.get("correct_lemma_ar", "")
        correct_gloss = issue.get("correct_gloss", "")
        correct_pos = issue.get("correct_pos", "")
        explanation = issue.get("explanation", "")

        if pos is None or not correct_ar:
            continue

        # Find the sentence_word at this position
        sw = next((w for w in words if w.position == pos), None)
        if not sw:
            continue

        # Search for the correct lemma in the database
        correct_bare = normalize_arabic(correct_ar)
        candidate = (
            db.query(Lemma)
            .filter(Lemma.lemma_ar_bare == correct_bare)
            .first()
        )
        if not candidate:
            # Try without al-prefix
            if correct_bare.startswith("ال"):
                candidate = db.query(Lemma).filter(Lemma.lemma_ar_bare == correct_bare[2:]).first()
            else:
                candidate = db.query(Lemma).filter(Lemma.lemma_ar_bare == "ال" + correct_bare).first()

        auto_created = False
        if not candidate and correct_gloss and len(correct_bare) >= 2:
            candidate = _auto_create_lemma(db, correct_bare, correct_ar, correct_gloss, correct_pos)
            auto_created = candidate is not None

        if candidate and candidate.lemma_id != sw.lemma_id:
            old_lemma = lemmas_by_id.get(sw.lemma_id)
            old_desc = f"{old_lemma.lemma_ar_bare} ({old_lemma.gloss_en})" if old_lemma else "(unmapped)"
            changes.append({
                "position": pos,
                "surface_form": sw.surface_form,
                "old_lemma_id": sw.lemma_id,
                "old": old_desc,
                "new_lemma_id": candidate.lemma_id,
                "new": f"{candidate.lemma_ar_bare} ({candidate.gloss_en})",
                "explanation": explanation,
                "auto_created": auto_created,
            })
            sw.lemma_id = candidate.lemma_id

    if changes:
        flag.corrected_value = json.dumps(changes, ensure_ascii=False)
        flag.status = "fixed"
        flag.resolution_note = f"Fixed {len(changes)} word mapping(s)"
        _log_activity(db, "flag_resolved",
                      f"Fixed {len(changes)} word mapping(s) in sentence #{sentence.id}",
                      {"flag_id": flag.id, "sentence_id": sentence.id, "changes": changes})

        # Propagate fixes to other sentences with the same bad mappings
        total_propagated = 0
        for change in changes:
            propagated = _propagate_mapping_fix(
                db,
                surface_bare=normalize_arabic(change["surface_form"]),
                old_lemma_id=change["old_lemma_id"],
                new_lemma_id=change["new_lemma_id"],
                source_sentence_id=sentence.id,
            )
            total_propagated += propagated
        if total_propagated:
            flag.resolution_note += f", propagated {total_propagated} fix(es) to other sentences"
            _log_activity(db, "flag_resolved",
                          f"Propagated {total_propagated} mapping fix(es) from sentence #{sentence.id}",
                          {"flag_id": flag.id, "sentence_id": sentence.id,
                           "propagated_count": total_propagated})
    else:
        flag.status = "dismissed"
        flag.resolution_note = (
            f"LLM found {len(issues)} issue(s) but no matching lemmas in DB to fix them"
        )
        _log_activity(db, "flag_resolved",
                      f"Word mapping issues found but unfixable for sentence #{sentence.id}",
                      {"flag_id": flag.id, "sentence_id": sentence.id, "issues": issues})

    flag.resolved_at = datetime.now(timezone.utc)


def _auto_create_lemma(
    db: Session, bare: str, arabic: str, gloss: str, pos: str,
) -> "Lemma | None":
    """Create a minimal 'encountered' lemma when the correct one is missing from DB."""
    from app.models import UserLemmaKnowledge

    # Guard against race condition / duplicate
    existing = db.query(Lemma).filter(Lemma.lemma_ar_bare == bare).first()
    if existing:
        return existing

    # Normalize POS to match our conventions
    pos_map = {"adj": "adjective", "adv": "adverb", "prep": "preposition",
               "conj": "conjunction", "pron": "pronoun"}
    normalized_pos = pos_map.get(pos, pos) if pos else None

    new_lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=bare,
        gloss_en=gloss,
        pos=normalized_pos,
        source="flag_autocreate",
    )
    db.add(new_lemma)
    db.flush()  # get lemma_id

    ulk = UserLemmaKnowledge(
        lemma_id=new_lemma.lemma_id,
        knowledge_state="encountered",
        source="flag_autocreate",
        total_encounters=1,
    )
    db.add(ulk)
    db.flush()

    logger.info(f"Auto-created lemma #{new_lemma.lemma_id}: {bare} ({gloss}, {normalized_pos})")
    _log_activity(db, "flag_resolved",
                  f"Auto-created lemma: {bare} ({gloss})",
                  {"lemma_id": new_lemma.lemma_id, "source": "flag_autocreate"})
    return new_lemma


def _propagate_mapping_fix(
    db: Session,
    surface_bare: str,
    old_lemma_id: int,
    new_lemma_id: int,
    source_sentence_id: int,
    max_propagate: int = 50,
) -> int:
    """Find other sentences with the same bad mapping and fix them with LLM verification.

    Returns count of propagated fixes.
    """
    from app.services.sentence_validator import normalize_arabic, strip_punctuation

    # Find SentenceWord rows with the wrong lemma in active sentences
    candidates = (
        db.query(SentenceWord)
        .join(Sentence, SentenceWord.sentence_id == Sentence.id)
        .filter(
            SentenceWord.lemma_id == old_lemma_id,
            Sentence.is_active == True,
            Sentence.id != source_sentence_id,
        )
        .all()
    )

    # Filter to matching surface forms (need to normalize for comparison)
    surface_bare_clean = strip_punctuation(surface_bare)
    matching = []
    for sw in candidates:
        sw_bare = strip_punctuation(normalize_arabic(sw.surface_form))
        if sw_bare == surface_bare_clean:
            matching.append(sw)

    if not matching:
        return 0

    matching = matching[:max_propagate]

    # Load sentences for LLM verification
    sentence_ids = list({sw.sentence_id for sw in matching})
    sentences_by_id = {
        s.id: s for s in db.query(Sentence).filter(Sentence.id.in_(sentence_ids)).all()
    }

    old_lemma = db.query(Lemma).filter(Lemma.lemma_id == old_lemma_id).first()
    new_lemma = db.query(Lemma).filter(Lemma.lemma_id == new_lemma_id).first()
    if not old_lemma or not new_lemma:
        return 0

    # Batch LLM verification — group into batches of 10
    fixed_count = 0
    for batch_start in range(0, len(matching), 10):
        batch = matching[batch_start:batch_start + 10]
        sentence_lines = []
        batch_sentence_ids = []
        for sw in batch:
            sent = sentences_by_id.get(sw.sentence_id)
            if not sent:
                continue
            sentence_lines.append(
                f"Sentence #{sent.id}: {sent.arabic_diacritized or sent.arabic_text} / {sent.english_translation}"
            )
            batch_sentence_ids.append(sent.id)

        if not sentence_lines:
            continue

        prompt = f"""The word "{surface_bare}" was incorrectly mapped to lemma "{old_lemma.lemma_ar_bare}" ({old_lemma.gloss_en}, {old_lemma.pos}).
The correct lemma may be "{new_lemma.lemma_ar_bare}" ({new_lemma.gloss_en}, {new_lemma.pos}).

For each sentence below, determine if "{surface_bare}" should be mapped to:
  A) "{old_lemma.lemma_ar_bare}" ({old_lemma.gloss_en})
  B) "{new_lemma.lemma_ar_bare}" ({new_lemma.gloss_en})

{chr(10).join(sentence_lines)}

Return JSON: {{"fixes": [{{"sentence_id": <int>, "choice": "A" or "B"}}]}}
Include ALL sentences. Choose A if the original mapping is actually correct in that context."""

        try:
            result = generate_completion(
                prompt,
                system_prompt="You are an Arabic morphology expert. Be conservative — only choose B when it clearly fits the sentence context better.",
                model_override="claude_haiku",
                temperature=0.0,
                task_type="flag_propagation",
            )
            fixes = result.get("fixes", [])
            fix_map = {f["sentence_id"]: f["choice"] for f in fixes if isinstance(f, dict)}

            for sw in batch:
                choice = fix_map.get(sw.sentence_id, "A")
                if choice == "B":
                    sw.lemma_id = new_lemma_id
                    fixed_count += 1
                    logger.info(
                        f"Propagated fix in sentence #{sw.sentence_id}: "
                        f"'{surface_bare}' #{old_lemma_id} → #{new_lemma_id}"
                    )
        except Exception as e:
            logger.warning(f"Propagation LLM failed for batch: {e}")
            continue

    return fixed_count


def _evaluate_sentence(db: Session, flag: ContentFlag) -> None:
    sentence = db.query(Sentence).filter(Sentence.id == flag.sentence_id).first()
    if not sentence:
        flag.status = "dismissed"
        flag.resolution_note = "Sentence not found"
        flag.resolved_at = datetime.now(timezone.utc)
        return

    field_map = {
        "sentence_arabic": ("arabic_diacritized", "Arabic text"),
        "sentence_english": ("english_translation", "English translation"),
        "sentence_transliteration": ("transliteration", "transliteration"),
    }

    field_name, field_label = field_map.get(flag.content_type, (None, None))
    if not field_name:
        flag.status = "dismissed"
        flag.resolution_note = f"Unknown content type: {flag.content_type}"
        flag.resolved_at = datetime.now(timezone.utc)
        return

    current_value = getattr(sentence, field_name, None) or ""
    flag.original_value = current_value

    if flag.content_type == "sentence_arabic":
        prompt = f"""Evaluate this Arabic sentence for naturalness and grammatical correctness.

Arabic: {sentence.arabic_diacritized or sentence.arabic_text}
English translation: {sentence.english_translation}

Is this natural, grammatically correct Arabic? If it has minor issues, provide a corrected version.
If it's fundamentally broken, say so.

Respond with JSON:
{{"acceptable": true/false, "fixable": true/false, "corrected": "fixed Arabic if fixable", "confidence": 0.0-1.0, "explanation": "brief reason"}}"""
    elif flag.content_type == "sentence_english":
        prompt = f"""Evaluate this English translation of an Arabic sentence.

Arabic: {sentence.arabic_diacritized or sentence.arabic_text}
Current English translation: "{sentence.english_translation}"

Is the English translation accurate? If not, provide a better one.

Respond with JSON:
{{"correct": true/false, "suggested": "better translation if incorrect", "confidence": 0.0-1.0, "explanation": "brief reason"}}"""
    else:  # sentence_transliteration
        prompt = f"""Evaluate this ALA-LC transliteration of an Arabic sentence.

Arabic: {sentence.arabic_diacritized or sentence.arabic_text}
Current transliteration: "{sentence.transliteration}"

Is the transliteration correct using ALA-LC standard (with macrons for long vowels)? If not, provide the correct version.

Respond with JSON:
{{"correct": true/false, "suggested": "corrected transliteration if incorrect", "confidence": 0.0-1.0, "explanation": "brief reason"}}"""

    try:
        result = generate_completion(prompt, model_override="openai", temperature=0.3, task_type="flag_evaluation")
    except LLMError:
        flag.status = "dismissed"
        flag.resolution_note = "LLM evaluation failed"
        flag.resolved_at = datetime.now(timezone.utc)
        return

    confidence = result.get("confidence", 0.0)
    explanation = result.get("explanation", "")

    if flag.content_type == "sentence_arabic":
        acceptable = result.get("acceptable", True)

        if not acceptable:
            # Always retire bad Arabic sentences — patching in place would leave
            # stale SentenceWord mappings. The cron pipeline will generate a fresh
            # replacement for the target word.
            sentence.is_active = False
            flag.status = "fixed"
            flag.resolution_note = f"Sentence retired (bad Arabic): {explanation}"
            _log_activity(db, "flag_resolved",
                          f"Retired sentence #{sentence.id} (flagged Arabic): '{current_value[:60]}…'",
                          {"flag_id": flag.id, "sentence_id": sentence.id,
                           "action": "retired", "reason": explanation})
        else:
            flag.status = "dismissed"
            flag.resolution_note = f"Sentence appears acceptable: {explanation}"
            _log_activity(db, "flag_resolved",
                          f"Arabic confirmed OK for sentence #{sentence.id}",
                          {"flag_id": flag.id, "sentence_id": sentence.id})
    else:
        correct = result.get("correct", True)
        suggested = result.get("suggested", "")

        if not correct and confidence >= 0.8 and suggested:
            flag.corrected_value = suggested
            flag.status = "fixed"
            flag.resolution_note = explanation
            setattr(sentence, field_name, suggested)
            _log_activity(db, "flag_resolved",
                          f"Fixed {field_label} for sentence #{sentence.id}: '{current_value[:40]}…' → '{suggested[:40]}…'",
                          {"flag_id": flag.id, "sentence_id": sentence.id,
                           "field": field_name, "old": current_value, "new": suggested})
        elif correct:
            flag.status = "dismissed"
            flag.resolution_note = f"{field_label.capitalize()} appears correct: {explanation}"
            _log_activity(db, "flag_resolved",
                          f"{field_label.capitalize()} confirmed OK for sentence #{sentence.id}",
                          {"flag_id": flag.id, "sentence_id": sentence.id})
        else:
            flag.status = "dismissed"
            flag.resolution_note = f"Low confidence ({confidence}): {explanation}"
            _log_activity(db, "flag_resolved",
                          f"Flag reviewed but not auto-fixed for sentence #{sentence.id} (confidence {confidence})",
                          {"flag_id": flag.id, "sentence_id": sentence.id})

    flag.resolved_at = datetime.now(timezone.utc)


def recover_stuck_flags() -> int:
    """Reset flags stuck in 'reviewing' (e.g. from server restart) back to pending.

    Call from startup or cron to ensure orphaned flags get re-evaluated.
    Returns count of recovered flags.
    """
    db = SessionLocal()
    try:
        stuck = db.query(ContentFlag).filter(ContentFlag.status == "reviewing").all()
        for flag in stuck:
            flag.status = "pending"
            logger.info("Recovered stuck flag #%s (type=%s) back to pending", flag.id, flag.content_type)
        if stuck:
            db.commit()
        return len(stuck)
    finally:
        db.close()


def _log_activity(db: Session, event_type: str, summary: str, detail: dict | None = None) -> None:
    log_activity(db, event_type, summary, detail, commit=False)
