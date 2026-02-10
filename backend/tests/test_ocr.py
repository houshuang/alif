"""Tests for OCR service and textbook scanner endpoints."""

import json
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.orm import Session

from app.models import Lemma, Root, UserLemmaKnowledge, PageUpload
from app.services.fsrs_service import create_new_card


def _seed_words(db: Session) -> list[int]:
    """Create some test words in the DB."""
    root = Root(root="ك.ت.ب", core_meaning_en="writing")
    db.add(root)
    db.flush()

    lemma1 = Lemma(
        lemma_ar="كِتَاب",
        lemma_ar_bare="كتاب",
        root_id=root.root_id,
        pos="noun",
        gloss_en="book",
        source="duolingo",
    )
    lemma2 = Lemma(
        lemma_ar="كَاتِب",
        lemma_ar_bare="كاتب",
        root_id=root.root_id,
        pos="noun",
        gloss_en="writer",
        source="duolingo",
    )
    lemma3 = Lemma(
        lemma_ar="مَدْرَسَة",
        lemma_ar_bare="مدرسة",
        root_id=None,
        pos="noun",
        gloss_en="school",
        source="duolingo",
    )
    db.add_all([lemma1, lemma2, lemma3])
    db.flush()

    # Add knowledge for lemma1 and lemma2
    ulk1 = UserLemmaKnowledge(
        lemma_id=lemma1.lemma_id,
        knowledge_state="known",
        fsrs_card_json=create_new_card(),
        source="duolingo",
        total_encounters=5,
    )
    ulk2 = UserLemmaKnowledge(
        lemma_id=lemma2.lemma_id,
        knowledge_state="learning",
        fsrs_card_json=create_new_card(),
        source="duolingo",
        total_encounters=2,
    )
    db.add_all([ulk1, ulk2])
    db.commit()

    return [lemma1.lemma_id, lemma2.lemma_id, lemma3.lemma_id]


class TestOCRService:
    """Tests for the OCR service functions."""

    @patch("app.services.ocr_service._call_gemini_vision")
    def test_extract_text_from_image(self, mock_vision):
        from app.services.ocr_service import extract_text_from_image

        mock_vision.return_value = {
            "arabic_text": "هَذَا كِتَابٌ جَمِيلٌ. أُحِبُّ القِرَاءَةَ."
        }

        result = extract_text_from_image(b"fake_image_data")
        assert "كِتَابٌ" in result
        assert "القِرَاءَةَ" in result
        mock_vision.assert_called_once()

    @patch("app.services.ocr_service._call_gemini_vision")
    def test_extract_words_from_image(self, mock_vision):
        from app.services.ocr_service import extract_words_from_image

        mock_vision.return_value = {
            "words": [
                {"arabic": "كِتَاب", "arabic_bare": "كتاب", "english": "book", "pos": "noun", "root": "ك.ت.ب"},
                {"arabic": "جَمِيل", "arabic_bare": "جميل", "english": "beautiful", "pos": "adj", "root": "ج.م.ل"},
                {"arabic": "قَلَم", "arabic_bare": "قلم", "english": "pen", "pos": "noun", "root": "ق.ل.م"},
            ]
        }

        result = extract_words_from_image(b"fake_image_data")
        assert len(result) == 3
        assert result[0]["arabic"] == "كِتَاب"
        assert result[1]["english"] == "beautiful"

    @patch("app.services.ocr_service._call_gemini_vision")
    def test_extract_words_empty_result(self, mock_vision):
        from app.services.ocr_service import extract_words_from_image

        mock_vision.return_value = {"words": []}
        result = extract_words_from_image(b"fake_image_data")
        assert result == []

    @patch("app.services.ocr_service._call_gemini_vision")
    def test_extract_words_malformed_result(self, mock_vision):
        from app.services.ocr_service import extract_words_from_image

        mock_vision.return_value = {"words": "not a list"}
        result = extract_words_from_image(b"fake_image_data")
        assert result == []


class TestProcessTextbookPage:
    """Tests for the textbook page processing logic."""

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_finds_existing_words(self, mock_extract, db_session):
        from app.services.ocr_service import process_textbook_page

        lemma_ids = _seed_words(db_session)

        upload = PageUpload(batch_id="test123", filename="page1.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.return_value = [
            {"arabic": "كِتَاب", "arabic_bare": "كتاب", "english": "book", "pos": "noun", "root": "ك.ت.ب"},
        ]

        process_textbook_page(db_session, upload, b"fake_image")

        assert upload.status == "completed"
        assert upload.existing_words == 1
        assert upload.new_words == 0
        assert len(upload.extracted_words_json) == 1
        assert upload.extracted_words_json[0]["status"] == "existing"

        # Check encounter count incremented
        ulk = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == lemma_ids[0]
        ).first()
        assert ulk.total_encounters == 6  # was 5, now 6

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_imports_new_words(self, mock_extract, db_session):
        from app.services.ocr_service import process_textbook_page

        _seed_words(db_session)

        upload = PageUpload(batch_id="test456", filename="page2.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.return_value = [
            {"arabic": "جَمِيل", "arabic_bare": "جميل", "english": "beautiful", "pos": "adj", "root": "ج.م.ل"},
        ]

        process_textbook_page(db_session, upload, b"fake_image")

        assert upload.status == "completed"
        assert upload.new_words == 1
        assert upload.existing_words == 0

        # Check new lemma was created
        new_lemma = db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "جميل").first()
        assert new_lemma is not None
        assert new_lemma.gloss_en == "beautiful"
        assert new_lemma.source == "textbook_scan"

        # Check root was created
        root = db_session.query(Root).filter(Root.root == "ج.م.ل").first()
        assert root is not None
        assert new_lemma.root_id == root.root_id

        # Check knowledge record created
        ulk = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == new_lemma.lemma_id
        ).first()
        assert ulk is not None
        assert ulk.knowledge_state == "learning"
        assert ulk.source == "textbook_scan"

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_mixed_new_and_existing(self, mock_extract, db_session):
        from app.services.ocr_service import process_textbook_page

        _seed_words(db_session)

        upload = PageUpload(batch_id="test789", filename="page3.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.return_value = [
            {"arabic": "كِتَاب", "arabic_bare": "كتاب", "english": "book", "pos": "noun", "root": "ك.ت.ب"},
            {"arabic": "جَمِيل", "arabic_bare": "جميل", "english": "beautiful", "pos": "adj", "root": "ج.م.ل"},
            {"arabic": "قَلَم", "arabic_bare": "قلم", "english": "pen", "pos": "noun", "root": "ق.ل.م"},
        ]

        process_textbook_page(db_session, upload, b"fake_image")

        assert upload.status == "completed"
        assert upload.existing_words == 1  # كتاب
        assert upload.new_words == 2  # جميل + قلم

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_deduplicates_within_page(self, mock_extract, db_session):
        from app.services.ocr_service import process_textbook_page

        _seed_words(db_session)

        upload = PageUpload(batch_id="testdup", filename="page4.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.return_value = [
            {"arabic": "جَمِيل", "arabic_bare": "جميل", "english": "beautiful", "pos": "adj", "root": None},
            {"arabic": "جَمِيل", "arabic_bare": "جميل", "english": "beautiful", "pos": "adj", "root": None},
        ]

        process_textbook_page(db_session, upload, b"fake_image")

        assert upload.new_words == 1  # Only one, not two

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_skips_function_words(self, mock_extract, db_session):
        from app.services.ocr_service import process_textbook_page

        upload = PageUpload(batch_id="testfunc", filename="page5.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.return_value = [
            {"arabic": "في", "arabic_bare": "في", "english": "in", "pos": "prep", "root": None},
            {"arabic": "من", "arabic_bare": "من", "english": "from", "pos": "prep", "root": None},
        ]

        process_textbook_page(db_session, upload, b"fake_image")

        assert upload.status == "completed"
        assert upload.new_words == 0
        assert upload.existing_words == 0

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_handles_empty_extraction(self, mock_extract, db_session):
        from app.services.ocr_service import process_textbook_page

        upload = PageUpload(batch_id="testempty", filename="blank.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.return_value = []

        process_textbook_page(db_session, upload, b"fake_image")

        assert upload.status == "completed"
        assert upload.new_words == 0
        assert upload.existing_words == 0

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_handles_failure(self, mock_extract, db_session):
        from app.services.ocr_service import process_textbook_page

        upload = PageUpload(batch_id="testfail", filename="bad.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.side_effect = ValueError("Gemini API error")

        process_textbook_page(db_session, upload, b"fake_image")

        assert upload.status == "failed"
        assert "Gemini API error" in upload.error_message

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_creates_ulk_for_existing_lemma_without_knowledge(self, mock_extract, db_session):
        """When a lemma exists but has no ULK record, process should create one."""
        from app.services.ocr_service import process_textbook_page

        _seed_words(db_session)  # lemma3 (مدرسة) has no ULK

        upload = PageUpload(batch_id="testulk", filename="page6.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.return_value = [
            {"arabic": "مَدْرَسَة", "arabic_bare": "مدرسة", "english": "school", "pos": "noun", "root": None},
        ]

        process_textbook_page(db_session, upload, b"fake_image")

        assert upload.status == "completed"
        assert upload.existing_words == 1

        # Check ULK was created
        lemma = db_session.query(Lemma).filter(Lemma.lemma_ar_bare == "مدرسة").first()
        ulk = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == lemma.lemma_id
        ).first()
        assert ulk is not None
        assert ulk.source == "textbook_scan"
        assert ulk.knowledge_state == "learning"


class TestOCREndpoints:
    """Tests for the OCR API endpoints."""

    @patch("app.routers.ocr._process_page_background")
    def test_scan_pages_endpoint(self, mock_process, client):
        """Test the scan-pages endpoint accepts file uploads."""
        import io

        # Create a fake image file
        fake_image = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        response = client.post(
            "/api/ocr/scan-pages",
            files=[("files", ("page1.jpg", fake_image, "image/jpeg"))],
        )

        assert response.status_code == 200
        data = response.json()
        assert "batch_id" in data
        assert len(data["pages"]) == 1
        assert data["pages"][0]["status"] == "pending"
        assert data["pages"][0]["filename"] == "page1.jpg"

    @patch("app.routers.ocr._process_page_background")
    def test_scan_multiple_pages(self, mock_process, client):
        """Test uploading multiple pages in one batch."""
        import io

        files = [
            ("files", (f"page{i}.jpg", io.BytesIO(b"\x89PNG" + b"\x00" * 50), "image/jpeg"))
            for i in range(3)
        ]

        response = client.post("/api/ocr/scan-pages", files=files)

        assert response.status_code == 200
        data = response.json()
        assert len(data["pages"]) == 3

    def test_get_batch_status_not_found(self, client):
        """Test getting status for non-existent batch."""
        response = client.get("/api/ocr/batch/nonexistent")
        assert response.status_code == 404

    @patch("app.routers.ocr._process_page_background")
    def test_get_batch_status(self, mock_process, client):
        """Test getting batch status after upload."""
        import io

        fake_image = io.BytesIO(b"\x89PNG" + b"\x00" * 50)
        upload_response = client.post(
            "/api/ocr/scan-pages",
            files=[("files", ("page1.jpg", fake_image, "image/jpeg"))],
        )
        batch_id = upload_response.json()["batch_id"]

        response = client.get(f"/api/ocr/batch/{batch_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["batch_id"] == batch_id
        assert len(data["pages"]) == 1

    def test_list_uploads_empty(self, client):
        """Test listing uploads when none exist."""
        response = client.get("/api/ocr/uploads")
        assert response.status_code == 200
        data = response.json()
        assert data["batches"] == []

    @patch("app.services.ocr_service.extract_text_from_image")
    def test_extract_text_endpoint(self, mock_extract, client):
        """Test the extract-text endpoint for story import."""
        import io

        mock_extract.return_value = "هَذَا نَصٌّ عَرَبِيٌّ."

        fake_image = io.BytesIO(b"\x89PNG" + b"\x00" * 50)
        response = client.post(
            "/api/ocr/extract-text",
            files=[("file", ("story.jpg", fake_image, "image/jpeg"))],
        )

        assert response.status_code == 200
        data = response.json()
        assert data["extracted_text"] == "هَذَا نَصٌّ عَرَبِيٌّ."

    @patch("app.services.ocr_service.extract_text_from_image")
    def test_extract_text_empty_result(self, mock_extract, client):
        """Test extract-text returns 422 when no text found."""
        import io

        mock_extract.return_value = ""

        fake_image = io.BytesIO(b"\x89PNG" + b"\x00" * 50)
        response = client.post(
            "/api/ocr/extract-text",
            files=[("file", ("blank.jpg", fake_image, "image/jpeg"))],
        )

        assert response.status_code == 422
