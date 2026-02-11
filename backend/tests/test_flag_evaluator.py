"""Tests for flag_evaluator background LLM evaluation service."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models import ActivityLog, ContentFlag, Lemma, Sentence
from app.services.llm import LLMError


def _seed_lemma(db, lemma_id=1, arabic="كتاب", gloss="book"):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        pos="noun",
        gloss_en=gloss,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _seed_sentence(db, sid=1, arabic="هذا كتاب", english="This is a book", translit="hādhā kitāb"):
    sent = Sentence(
        id=sid,
        arabic_text=arabic,
        arabic_diacritized=arabic,
        english_translation=english,
        transliteration=translit,
        target_lemma_id=1,
        is_active=True,
    )
    db.add(sent)
    db.flush()
    return sent


def _seed_flag(db, content_type, lemma_id=None, sentence_id=None):
    flag = ContentFlag(
        content_type=content_type,
        lemma_id=lemma_id,
        sentence_id=sentence_id,
        status="pending",
    )
    db.add(flag)
    db.flush()
    return flag


class TestWordGloss:
    @patch("app.services.flag_evaluator.generate_completion")
    def test_incorrect_gloss_auto_fixes(self, mock_llm, db_session):
        lemma = _seed_lemma(db_session, gloss="shoe")
        flag = _seed_flag(db_session, "word_gloss", lemma_id=lemma.lemma_id)
        db_session.commit()

        mock_llm.return_value = {
            "correct": False,
            "confidence": 0.9,
            "suggested_gloss": "book",
            "explanation": "كتاب means book",
        }

        from app.services.flag_evaluator import _evaluate_word_gloss
        _evaluate_word_gloss(db_session, flag)
        db_session.commit()

        assert flag.status == "fixed"
        assert flag.corrected_value == "book"
        assert lemma.gloss_en == "book"
        assert flag.original_value == "shoe"
        activity = db_session.query(ActivityLog).first()
        assert activity is not None
        assert "Fixed translation" in activity.summary

    @patch("app.services.flag_evaluator.generate_completion")
    def test_correct_gloss_dismissed(self, mock_llm, db_session):
        lemma = _seed_lemma(db_session, gloss="book")
        flag = _seed_flag(db_session, "word_gloss", lemma_id=lemma.lemma_id)
        db_session.commit()

        mock_llm.return_value = {
            "correct": True,
            "confidence": 0.95,
            "explanation": "Translation is accurate",
        }

        from app.services.flag_evaluator import _evaluate_word_gloss
        _evaluate_word_gloss(db_session, flag)
        db_session.commit()

        assert flag.status == "dismissed"
        assert "appears correct" in flag.resolution_note
        assert lemma.gloss_en == "book"

    @patch("app.services.flag_evaluator.generate_completion")
    def test_low_confidence_dismissed(self, mock_llm, db_session):
        lemma = _seed_lemma(db_session, gloss="shoe")
        flag = _seed_flag(db_session, "word_gloss", lemma_id=lemma.lemma_id)
        db_session.commit()

        mock_llm.return_value = {
            "correct": False,
            "confidence": 0.5,
            "suggested_gloss": "book",
            "explanation": "not sure",
        }

        from app.services.flag_evaluator import _evaluate_word_gloss
        _evaluate_word_gloss(db_session, flag)
        db_session.commit()

        assert flag.status == "dismissed"
        assert "Low confidence" in flag.resolution_note
        assert lemma.gloss_en == "shoe"

    @patch("app.services.flag_evaluator.generate_completion")
    def test_llm_failure_dismissed(self, mock_llm, db_session):
        lemma = _seed_lemma(db_session)
        flag = _seed_flag(db_session, "word_gloss", lemma_id=lemma.lemma_id)
        db_session.commit()

        mock_llm.side_effect = LLMError("API timeout")

        from app.services.flag_evaluator import _evaluate_word_gloss
        _evaluate_word_gloss(db_session, flag)
        db_session.commit()

        assert flag.status == "dismissed"
        assert "LLM evaluation failed" in flag.resolution_note

    def test_missing_lemma_dismissed(self, db_session):
        flag = _seed_flag(db_session, "word_gloss", lemma_id=99999)
        db_session.commit()

        from app.services.flag_evaluator import _evaluate_word_gloss
        _evaluate_word_gloss(db_session, flag)
        db_session.commit()

        assert flag.status == "dismissed"
        assert "Lemma not found" in flag.resolution_note


class TestSentence:
    @patch("app.services.flag_evaluator.generate_completion")
    def test_english_incorrect_auto_fixes(self, mock_llm, db_session):
        _seed_lemma(db_session)
        sent = _seed_sentence(db_session, english="This is a shoe")
        flag = _seed_flag(db_session, "sentence_english", sentence_id=sent.id)
        db_session.commit()

        mock_llm.return_value = {
            "correct": False,
            "confidence": 0.9,
            "suggested": "This is a book",
            "explanation": "Wrong translation",
        }

        from app.services.flag_evaluator import _evaluate_sentence
        _evaluate_sentence(db_session, flag)
        db_session.commit()

        assert flag.status == "fixed"
        assert sent.english_translation == "This is a book"

    @patch("app.services.flag_evaluator.generate_completion")
    def test_arabic_unfixable_retires(self, mock_llm, db_session):
        _seed_lemma(db_session)
        sent = _seed_sentence(db_session)
        flag = _seed_flag(db_session, "sentence_arabic", sentence_id=sent.id)
        db_session.commit()

        mock_llm.return_value = {
            "acceptable": False,
            "fixable": False,
            "explanation": "Fundamentally broken",
        }

        from app.services.flag_evaluator import _evaluate_sentence
        _evaluate_sentence(db_session, flag)
        db_session.commit()

        assert flag.status == "fixed"
        assert sent.is_active is False
        assert "retired" in flag.resolution_note.lower()

    @patch("app.services.flag_evaluator.generate_completion")
    def test_arabic_fixable_updates(self, mock_llm, db_session):
        _seed_lemma(db_session)
        sent = _seed_sentence(db_session, arabic="هذا كتب")
        flag = _seed_flag(db_session, "sentence_arabic", sentence_id=sent.id)
        db_session.commit()

        mock_llm.return_value = {
            "acceptable": False,
            "fixable": True,
            "corrected": "هذا كتاب",
            "confidence": 0.9,
            "explanation": "Fixed grammar",
        }

        from app.services.flag_evaluator import _evaluate_sentence
        _evaluate_sentence(db_session, flag)
        db_session.commit()

        assert flag.status == "fixed"
        assert sent.arabic_diacritized == "هذا كتاب"
        assert sent.arabic_text == "هذا كتاب"

    @patch("app.services.flag_evaluator.generate_completion")
    def test_transliteration_fixes(self, mock_llm, db_session):
        _seed_lemma(db_session)
        sent = _seed_sentence(db_session, translit="hatha kitab")
        flag = _seed_flag(db_session, "sentence_transliteration", sentence_id=sent.id)
        db_session.commit()

        mock_llm.return_value = {
            "correct": False,
            "confidence": 0.85,
            "suggested": "hādhā kitāb",
            "explanation": "Missing macrons",
        }

        from app.services.flag_evaluator import _evaluate_sentence
        _evaluate_sentence(db_session, flag)
        db_session.commit()

        assert flag.status == "fixed"
        assert sent.transliteration == "hādhā kitāb"

    @patch("app.services.flag_evaluator.generate_completion")
    def test_sentence_correct_dismissed(self, mock_llm, db_session):
        _seed_lemma(db_session)
        sent = _seed_sentence(db_session)
        flag = _seed_flag(db_session, "sentence_english", sentence_id=sent.id)
        db_session.commit()

        mock_llm.return_value = {
            "correct": True,
            "confidence": 0.95,
            "explanation": "Translation is good",
        }

        from app.services.flag_evaluator import _evaluate_sentence
        _evaluate_sentence(db_session, flag)
        db_session.commit()

        assert flag.status == "dismissed"
        assert sent.english_translation == "This is a book"


class TestEvaluateFlag:
    @patch("app.services.flag_evaluator.SessionLocal")
    @patch("app.services.flag_evaluator.generate_completion")
    def test_exception_dismisses_flag(self, mock_llm, mock_session_local, db_session):
        _seed_lemma(db_session)
        flag = _seed_flag(db_session, "word_gloss", lemma_id=1)
        db_session.commit()
        flag_id = flag.id

        mock_session_local.return_value = db_session
        mock_llm.side_effect = RuntimeError("Unexpected error")
        original_close = db_session.close
        db_session.close = lambda: None  # prevent evaluate_flag from closing our test session

        from app.services.flag_evaluator import evaluate_flag
        try:
            evaluate_flag(flag_id)
        finally:
            db_session.close = original_close

        db_session.expire_all()
        flag = db_session.query(ContentFlag).filter(ContentFlag.id == flag_id).first()
        assert flag.status == "dismissed"
        assert "Evaluation error" in flag.resolution_note

    @patch("app.services.flag_evaluator.SessionLocal")
    def test_not_pending_noop(self, mock_session_local, db_session):
        flag = _seed_flag(db_session, "word_gloss", lemma_id=1)
        flag.status = "fixed"
        db_session.commit()
        flag_id = flag.id

        mock_session_local.return_value = db_session
        original_close = db_session.close
        db_session.close = lambda: None

        from app.services.flag_evaluator import evaluate_flag
        try:
            evaluate_flag(flag_id)
        finally:
            db_session.close = original_close

        db_session.expire_all()
        flag = db_session.query(ContentFlag).filter(ContentFlag.id == flag_id).first()
        assert flag.status == "fixed"
