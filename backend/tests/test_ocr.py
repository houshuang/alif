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

    @patch("app.services.ocr_service._step3_translate")
    @patch("app.services.ocr_service._step2_morphology")
    @patch("app.services.ocr_service._step1_extract_words")
    def test_extract_words_from_image(self, mock_step1, mock_step2, mock_step3):
        from app.services.ocr_service import extract_words_from_image

        mock_step1.return_value = ["كِتَاب", "جَمِيل", "قَلَم"]
        mock_step2.return_value = [
            {"arabic": "كِتَاب", "bare": "كتاب", "root": "ك.ت.ب", "pos": "noun"},
            {"arabic": "جَمِيل", "bare": "جميل", "root": "ج.م.ل", "pos": "adj"},
            {"arabic": "قَلَم", "bare": "قلم", "root": "ق.ل.م", "pos": "noun"},
        ]
        mock_step3.return_value = [
            {"arabic": "كِتَاب", "bare": "كتاب", "root": "ك.ت.ب", "pos": "noun", "english": "book"},
            {"arabic": "جَمِيل", "bare": "جميل", "root": "ج.م.ل", "pos": "adj", "english": "beautiful"},
            {"arabic": "قَلَم", "bare": "قلم", "root": "ق.ل.م", "pos": "noun", "english": "pen"},
        ]

        result = extract_words_from_image(b"fake_image_data")
        assert len(result) == 3
        assert result[0]["arabic"] == "كِتَاب"
        assert result[1]["english"] == "beautiful"

    @patch("app.services.ocr_service._step1_extract_words")
    def test_extract_words_empty_result(self, mock_step1):
        from app.services.ocr_service import extract_words_from_image

        mock_step1.return_value = []
        result = extract_words_from_image(b"fake_image_data")
        assert result == []

    @patch("app.services.ocr_service._call_gemini_vision")
    def test_extract_words_malformed_result(self, mock_vision):
        """Step 1 returns empty when vision returns bad data."""
        from app.services.ocr_service import _step1_extract_words

        mock_vision.return_value = {"words": "not a list"}
        result = _step1_extract_words(b"fake_image_data")
        assert result == []


@patch("app.services.ocr_service.backfill_root_meanings", return_value=0)
class TestProcessTextbookPage:
    """Tests for the textbook page processing logic."""

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_finds_existing_words(self, mock_extract, mock_backfill, db_session):
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
    def test_process_imports_new_words(self, mock_extract, mock_backfill, db_session):
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
        assert ulk.knowledge_state == "encountered"
        assert ulk.source == "textbook_scan"
        assert ulk.fsrs_card_json is None

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_mixed_new_and_existing(self, mock_extract, mock_backfill, db_session):
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
    def test_process_deduplicates_within_page(self, mock_extract, mock_backfill, db_session):
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
    def test_process_imports_former_function_words(self, mock_extract, mock_backfill, db_session):
        """All words are now learnable — في and من get imported."""
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
        # في and من are no longer skipped — they're importable words
        assert upload.new_words + upload.existing_words == 2

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_handles_empty_extraction(self, mock_extract, mock_backfill, db_session):
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
    def test_process_handles_failure(self, mock_extract, mock_backfill, db_session):
        from app.services.ocr_service import process_textbook_page

        upload = PageUpload(batch_id="testfail", filename="bad.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.side_effect = ValueError("Gemini API error")

        process_textbook_page(db_session, upload, b"fake_image")

        assert upload.status == "failed"
        assert "Gemini API error" in upload.error_message

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_creates_ulk_for_existing_lemma_without_knowledge(self, mock_extract, mock_backfill, db_session):
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
        assert ulk.knowledge_state == "encountered"
        assert ulk.fsrs_card_json is None


@patch("app.services.ocr_service.backfill_root_meanings", return_value=0)
class TestBaseLemmaHandling:
    """Tests for base_lemma-aware import in OCR pipeline."""

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_uses_base_lemma_for_lookup(self, mock_extract, mock_backfill, db_session):
        """When base_lemma matches an existing lemma, should find it (not create new)."""
        from app.services.ocr_service import process_textbook_page

        # Seed كراج (garage) as existing
        root = Root(root="ك.ر.ج", core_meaning_en="garage")
        db_session.add(root)
        db_session.flush()
        lemma = Lemma(
            lemma_ar="كِرَاج", lemma_ar_bare="كراج", root_id=root.root_id,
            pos="noun", gloss_en="garage", source="duolingo",
        )
        db_session.add(lemma)
        db_session.flush()
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id, knowledge_state="learning",
            fsrs_card_json=create_new_card(), source="duolingo", total_encounters=3,
        )
        db_session.add(ulk)
        db_session.commit()

        upload = PageUpload(batch_id="test_bl1", filename="p.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        # OCR extracts كراجك (your garage) but base_lemma is كراج
        mock_extract.return_value = [{
            "arabic": "كِرَاجَك", "arabic_bare": "كراجك",
            "english": "your garage", "pos": "noun", "root": "ك.ر.ج",
            "base_lemma": "كراج",
        }]

        process_textbook_page(db_session, upload, b"fake")

        assert upload.status == "completed"
        assert upload.existing_words == 1
        assert upload.new_words == 0

        # Should have matched existing lemma via base_lemma
        ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
        assert ulk.total_encounters == 4  # was 3, now 4

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_imports_base_form_not_conjugated(self, mock_extract, mock_backfill, db_session):
        """When creating a new word with base_lemma, should use base form for lemma_ar_bare."""
        from app.services.ocr_service import process_textbook_page

        upload = PageUpload(batch_id="test_bl2", filename="p.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.return_value = [{
            "arabic": "تَبْدَأُونَ", "arabic_bare": "تبداون",
            "english": "to start", "pos": "verb", "root": "ب.د.أ",
            "base_lemma": "بدا",
        }]

        process_textbook_page(db_session, upload, b"fake")

        assert upload.new_words == 1

        # Lemma should be stored with base form, not conjugated
        new_lemma = db_session.query(Lemma).filter_by(source="textbook_scan").first()
        assert new_lemma.lemma_ar_bare == "بدا"  # base form, not تبداون

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_deduplicates_on_base_lemma(self, mock_extract, mock_backfill, db_session):
        """Two conjugated forms with the same base_lemma should produce one import."""
        from app.services.ocr_service import process_textbook_page

        upload = PageUpload(batch_id="test_bl3", filename="p.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.return_value = [
            {
                "arabic": "كِتَابَك", "arabic_bare": "كتابك",
                "english": "your book", "pos": "noun", "root": "ك.ت.ب",
                "base_lemma": "كتاب",
            },
            {
                "arabic": "كِتَابِي", "arabic_bare": "كتابي",
                "english": "my book", "pos": "noun", "root": "ك.ت.ب",
                "base_lemma": "كتاب",
            },
        ]

        process_textbook_page(db_session, upload, b"fake")

        assert upload.new_words == 1  # only one, not two

    @patch("app.services.ocr_service.extract_words_from_image")
    def test_process_falls_back_to_bare_when_no_base_lemma(self, mock_extract, mock_backfill, db_session):
        """When base_lemma is None, should behave as before (use bare)."""
        from app.services.ocr_service import process_textbook_page

        upload = PageUpload(batch_id="test_bl4", filename="p.jpg", status="pending")
        db_session.add(upload)
        db_session.commit()

        mock_extract.return_value = [{
            "arabic": "قَلَم", "arabic_bare": "قلم",
            "english": "pen", "pos": "noun", "root": "ق.ل.م",
            "base_lemma": None,
        }]

        process_textbook_page(db_session, upload, b"fake")

        assert upload.new_words == 1
        new_lemma = db_session.query(Lemma).filter_by(source="textbook_scan").first()
        assert new_lemma.lemma_ar_bare == "قلم"

    @patch("app.services.ocr_service._step3_translate")
    @patch("app.services.ocr_service._step2_morphology")
    @patch("app.services.ocr_service._step1_extract_words")
    def test_extract_words_passes_base_lemma(self, mock_step1, mock_step2, mock_step3, mock_backfill):
        """extract_words_from_image should include base_lemma in output."""
        from app.services.ocr_service import extract_words_from_image

        mock_step1.return_value = ["كِرَاجَك"]
        mock_step2.return_value = [
            {"arabic": "كِرَاجَك", "bare": "كراجك", "base_lemma": "كراج",
             "root": "ك.ر.ج", "pos": "noun"},
        ]
        mock_step3.return_value = [
            {"arabic": "كِرَاجَك", "bare": "كراجك", "base_lemma": "كراج",
             "root": "ك.ر.ج", "pos": "noun", "english": "garage"},
        ]

        result = extract_words_from_image(b"fake")
        assert len(result) == 1
        assert result[0]["base_lemma"] == "كراج"
        assert result[0]["arabic_bare"] == "كراجك"

    @patch("app.services.ocr_service._step3_translate")
    @patch("app.services.ocr_service._step2_morphology")
    @patch("app.services.ocr_service._step1_extract_words")
    def test_extract_words_deduplicates_on_base_lemma(self, mock_step1, mock_step2, mock_step3, mock_backfill):
        """Two conjugated forms with the same base_lemma should produce one output."""
        from app.services.ocr_service import extract_words_from_image

        mock_step1.return_value = ["كِتَابَك", "كِتَابِي"]
        mock_step2.return_value = [
            {"arabic": "كِتَابَك", "bare": "كتابك", "base_lemma": "كتاب",
             "root": "ك.ت.ب", "pos": "noun"},
            {"arabic": "كِتَابِي", "bare": "كتابي", "base_lemma": "كتاب",
             "root": "ك.ت.ب", "pos": "noun"},
        ]
        mock_step3.return_value = [
            {"arabic": "كِتَابَك", "bare": "كتابك", "base_lemma": "كتاب",
             "root": "ك.ت.ب", "pos": "noun", "english": "book"},
        ]

        result = extract_words_from_image(b"fake")
        assert len(result) == 1


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

    @patch("app.routers.ocr.extract_text_from_image")
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

    @patch("app.routers.ocr.extract_text_from_image")
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
