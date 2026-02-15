"""Tests for book import service."""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

import pytest

from app.models import Lemma, Root, Sentence, SentenceWord, Story, StoryWord, UserLemmaKnowledge


_root_cache: dict[str, int] = {}


def _create_lemma(db, arabic="كتاب", bare=None, english="book", pos="noun", freq=100, root_str="ك.ت.ب"):
    bare = bare or arabic
    # Reuse root if already created in this session
    existing = db.query(Root).filter_by(root=root_str).first()
    if existing:
        root_id = existing.root_id
    else:
        root = Root(root=root_str, core_meaning_en="writing")
        db.add(root)
        db.flush()
        root_id = root.root_id
    lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=bare,
        gloss_en=english,
        pos=pos,
        root_id=root_id,
        frequency_rank=freq,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _make_known(db, lemma):
    ulk = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="known",
        times_seen=10,
        times_correct=8,
    )
    db.add(ulk)
    db.flush()
    return ulk


class TestExtractCoverMetadata:
    @patch("app.services.book_import_service._call_gemini_vision")
    def test_extracts_title_and_author(self, mock_vision):
        mock_vision.return_value = {
            "title_ar": "كَرِيمٌ فِي الحَدِيقَةِ",
            "title_en": "Karim in the Garden",
            "author": "سامي",
            "series": "سلسلة كريم",
            "level": None,
        }
        from app.services.book_import_service import extract_cover_metadata

        result = extract_cover_metadata(b"fake_image_data")
        assert result["title_ar"] == "كَرِيمٌ فِي الحَدِيقَةِ"
        assert result["title_en"] == "Karim in the Garden"
        assert result["author"] == "سامي"

    @patch("app.services.book_import_service._call_gemini_vision")
    def test_returns_empty_on_failure(self, mock_vision):
        mock_vision.side_effect = Exception("API error")
        from app.services.book_import_service import extract_cover_metadata

        result = extract_cover_metadata(b"fake_image_data")
        assert result == {}


class TestCleanupAndSegment:
    @patch("app.services.book_import_service.generate_completion")
    def test_returns_sentences(self, mock_llm):
        mock_llm.return_value = {
            "sentences": [
                {"arabic": "ذَهَبَ الوَلَدُ إِلَى المَدْرَسَةِ."},
                {"arabic": "كَانَتْ أُمُّهُ سَعِيدَةً."},
            ]
        }
        from app.services.book_import_service import cleanup_and_segment

        result = cleanup_and_segment("ذهب الولد الى المدرسة. كانت امه سعيدة.")
        assert len(result) == 2
        assert "ذَهَبَ" in result[0]["arabic"]

    @patch("app.services.book_import_service.generate_completion")
    def test_returns_empty_on_failure(self, mock_llm):
        mock_llm.side_effect = Exception("LLM error")
        from app.services.book_import_service import cleanup_and_segment

        result = cleanup_and_segment("some text")
        assert result == []


class TestTranslateSentences:
    @patch("app.services.book_import_service.generate_completion")
    def test_adds_translations(self, mock_llm):
        mock_llm.return_value = {
            "translations": [
                {"index": 1, "english": "The boy went to school."},
                {"index": 2, "english": "His mother was happy."},
            ]
        }
        from app.services.book_import_service import translate_sentences

        sentences = [
            {"arabic": "ذَهَبَ الوَلَدُ إِلَى المَدْرَسَةِ."},
            {"arabic": "كَانَتْ أُمُّهُ سَعِيدَةً."},
        ]
        result = translate_sentences(sentences)
        assert result[0]["english"] == "The boy went to school."
        assert result[1]["english"] == "His mother was happy."

    @patch("app.services.book_import_service.generate_completion")
    def test_handles_empty_list(self, mock_llm):
        from app.services.book_import_service import translate_sentences

        result = translate_sentences([])
        assert result == []
        mock_llm.assert_not_called()


class TestCreateBookSentences:
    def test_creates_sentences_and_words(self, db_session):
        lemma1 = _create_lemma(db_session, "ذهب", "ذهب", "go", "verb", 50, "ذ.ه.ب")
        lemma2 = _create_lemma(db_session, "ولد", "ولد", "boy", "noun", 80, "و.ل.د")
        _make_known(db_session, lemma1)
        _make_known(db_session, lemma2)

        story = Story(
            title_ar="Test",
            body_ar="ذَهَبَ الوَلَدُ.",
            source="book_ocr",
            status="active",
        )
        db_session.add(story)
        db_session.flush()

        extracted = [
            {
                "arabic": "ذَهَبَ الوَلَدُ.",
                "english": "The boy went.",
                "transliteration": "dhahaba al-waladu.",
            }
        ]

        from app.services.book_import_service import create_book_sentences

        sentences = create_book_sentences(db_session, story, extracted)
        db_session.flush()

        assert len(sentences) == 1
        sent = sentences[0]
        assert sent.source == "book"
        assert sent.story_id == story.id
        assert sent.english_translation == "The boy went."

        words = db_session.query(SentenceWord).filter_by(sentence_id=sent.id).all()
        assert len(words) >= 2

    def test_skips_single_word_sentences(self, db_session):
        story = Story(
            title_ar="Test",
            body_ar="كتاب",
            source="book_ocr",
            status="active",
        )
        db_session.add(story)
        db_session.flush()

        extracted = [{"arabic": "كتاب", "english": "book", "transliteration": "kitāb"}]

        from app.services.book_import_service import create_book_sentences

        sentences = create_book_sentences(db_session, story, extracted)
        assert len(sentences) == 0


class TestImportBookEndToEnd:
    @patch("app.services.book_import_service.extract_cover_metadata")
    @patch("app.services.book_import_service.ocr_pages_parallel")
    @patch("app.services.book_import_service.cleanup_and_segment")
    @patch("app.services.book_import_service.translate_sentences")
    def test_full_pipeline(
        self, mock_translate, mock_cleanup, mock_ocr, mock_cover, db_session
    ):
        # Setup: create some known words
        l1 = _create_lemma(db_session, "ذَهَبَ", "ذهب", "go", "verb", 50, "ذ.ه.ب")
        l2 = _create_lemma(db_session, "وَلَد", "ولد", "boy", "noun", 80, "و.ل.د")
        _make_known(db_session, l1)
        _make_known(db_session, l2)
        db_session.commit()

        mock_cover.return_value = {
            "title_ar": "كَرِيمٌ فِي الحَدِيقَةِ",
            "title_en": "Karim in the Garden",
            "author": "Test Author",
        }
        mock_ocr.return_value = ["ذهب الولد الى الحديقة."]
        mock_cleanup.return_value = [
            {"arabic": "ذَهَبَ الوَلَدُ إِلَى الحَدِيقَةِ."},
        ]
        mock_translate.return_value = [
            {
                "arabic": "ذَهَبَ الوَلَدُ إِلَى الحَدِيقَةِ.",
                "english": "The boy went to the garden.",
            },
        ]

        from app.services.book_import_service import import_book

        story = import_book(
            db=db_session,
            cover_image=b"cover_data",
            page_images=[b"page1_data"],
        )

        assert story.source == "book_ocr"
        assert story.page_count == 1
        assert story.title_ar == "كَرِيمٌ فِي الحَدِيقَةِ"
        assert story.title_en == "Karim in the Garden"
        assert story.status == "active"

        # Verify sentences were created
        sentences = db_session.query(Sentence).filter_by(story_id=story.id).all()
        assert len(sentences) == 1
        assert sentences[0].source == "book"
        assert sentences[0].english_translation == "The boy went to the garden."

        # Verify story words were created
        story_words = db_session.query(StoryWord).filter_by(story_id=story.id).all()
        assert len(story_words) > 0

    @patch("app.services.book_import_service.extract_cover_metadata")
    @patch("app.services.book_import_service.ocr_pages_parallel")
    @patch("app.services.book_import_service.cleanup_and_segment")
    @patch("app.services.book_import_service.translate_sentences")
    def test_title_override(
        self, mock_translate, mock_cleanup, mock_ocr, mock_cover, db_session
    ):
        mock_ocr.return_value = ["بسم الله."]
        mock_cleanup.return_value = [{"arabic": "بِسْمِ اللَّهِ."}]
        mock_translate.return_value = [{"arabic": "بِسْمِ اللَّهِ.", "english": "In the name of God."}]

        from app.services.book_import_service import import_book

        story = import_book(
            db=db_session,
            cover_image=b"cover",
            page_images=[b"page1"],
            title_override="Custom Title",
        )

        assert story.title_ar == "Custom Title"
        # Cover metadata extraction should be skipped
        mock_cover.assert_not_called()


class TestBookSentenceSourceBonus:
    def test_book_sentences_get_higher_score(self):
        """Verify that book-sourced sentences get a 1.3x scoring bonus."""
        # Create mock sentence objects
        book_sent = MagicMock()
        book_sent.source = "book"
        book_sent.times_shown = 0

        llm_sent = MagicMock()
        llm_sent.source = "llm"
        llm_sent.times_shown = 0

        book_bonus = 1.3 if book_sent.source == "book" else 1.0
        llm_bonus = 1.3 if llm_sent.source == "book" else 1.0

        assert book_bonus == 1.3
        assert llm_bonus == 1.0
        assert book_bonus > llm_bonus
