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
  {{"position": <int>, "surface_form": "<word>", "current_lemma_wrong": true, "correct_lemma_ar": "<bare Arabic>", "correct_gloss": "<English>", "explanation": "<brief>"}}
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
            })
            sw.lemma_id = candidate.lemma_id

    if changes:
        flag.corrected_value = json.dumps(changes, ensure_ascii=False)
        flag.status = "fixed"
        flag.resolution_note = f"Fixed {len(changes)} word mapping(s)"
        _log_activity(db, "flag_resolved",
                      f"Fixed {len(changes)} word mapping(s) in sentence #{sentence.id}",
                      {"flag_id": flag.id, "sentence_id": sentence.id, "changes": changes})
    else:
        flag.status = "dismissed"
        flag.resolution_note = (
            f"LLM found {len(issues)} issue(s) but no matching lemmas in DB to fix them"
        )
        _log_activity(db, "flag_resolved",
                      f"Word mapping issues found but unfixable for sentence #{sentence.id}",
                      {"flag_id": flag.id, "sentence_id": sentence.id, "issues": issues})

    flag.resolved_at = datetime.now(timezone.utc)


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
        fixable = result.get("fixable", False)
        corrected = result.get("corrected", "")

        if not acceptable and not fixable:
            sentence.is_active = False
            flag.status = "fixed"
            flag.resolution_note = f"Sentence retired (unfixable): {explanation}"
            _log_activity(db, "flag_resolved",
                          f"Retired bad sentence: '{current_value[:50]}...'",
                          {"flag_id": flag.id, "sentence_id": sentence.id})
        elif not acceptable and fixable and confidence >= 0.8 and corrected:
            flag.corrected_value = corrected
            flag.status = "fixed"
            flag.resolution_note = explanation
            sentence.arabic_diacritized = corrected
            sentence.arabic_text = corrected
            _log_activity(db, "flag_resolved",
                          f"Fixed Arabic sentence #{sentence.id}",
                          {"flag_id": flag.id, "sentence_id": sentence.id})
        else:
            flag.status = "dismissed"
            flag.resolution_note = f"Sentence appears acceptable: {explanation}"
    else:
        correct = result.get("correct", True)
        suggested = result.get("suggested", "")

        if not correct and confidence >= 0.8 and suggested:
            flag.corrected_value = suggested
            flag.status = "fixed"
            flag.resolution_note = explanation
            setattr(sentence, field_name, suggested)
            _log_activity(db, "flag_resolved",
                          f"Fixed {field_label} for sentence #{sentence.id}",
                          {"flag_id": flag.id, "sentence_id": sentence.id})
        elif correct:
            flag.status = "dismissed"
            flag.resolution_note = f"{field_label.capitalize()} appears correct: {explanation}"
        else:
            flag.status = "dismissed"
            flag.resolution_note = f"Low confidence ({confidence}): {explanation}"

    flag.resolved_at = datetime.now(timezone.utc)


def _log_activity(db: Session, event_type: str, summary: str, detail: dict | None = None) -> None:
    log_activity(db, event_type, summary, detail, commit=False)
