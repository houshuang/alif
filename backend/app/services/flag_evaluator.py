"""Background LLM evaluation for flagged content.

Uses GPT-5.2 (not Flash) for quality evaluation. Auto-fixes high-confidence
corrections, retires unfixable sentences.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ActivityLog, ContentFlag, Lemma, Sentence
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
        elif flag.content_type.startswith("sentence_"):
            _evaluate_sentence(db, flag)

        db.commit()
    except Exception as e:
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
        result = generate_completion(prompt, model_override="openai", temperature=0.3)
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
        result = generate_completion(prompt, model_override="openai", temperature=0.3)
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
    entry = ActivityLog(
        event_type=event_type,
        summary=summary,
        detail_json=detail,
    )
    db.add(entry)
