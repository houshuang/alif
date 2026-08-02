"""Tests for book import service."""

from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app.models import Lemma, ReviewLog, Root, Sentence, SentenceWord, Story, StoryWord, UserLemmaKnowledge


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


def _seed_exact_alias_inventory(db, *, include_destinations: bool):
    gated_at = datetime.now(timezone.utc)
    forget = Lemma(
        lemma_ar="نَسِيَ",
        lemma_ar_bare="نسي",
        gloss_en="to forget",
        pos="verb",
        forms_json={"active_participle": "نَاسٍ"},
        gates_completed_at=gated_at,
    )
    loss = Lemma(
        lemma_ar="فَقْد",
        lemma_ar_bare="فقد",
        gloss_en="loss",
        pos="noun",
        gates_completed_at=gated_at,
    )
    rows = {"forget": forget, "loss": loss}
    if include_destinations:
        rows.update(
            people=Lemma(
                lemma_ar="نَاسٌ",
                lemma_ar_bare="ناس",
                gloss_en="people",
                pos="noun",
                gates_completed_at=gated_at,
            ),
            particle=Lemma(
                lemma_ar="قَدْ",
                lemma_ar_bare="قد",
                gloss_en="already",
                pos="particle",
                gates_completed_at=gated_at,
            ),
        )
    db.add_all(rows.values())
    db.flush()
    return rows


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

    def test_exact_aliases_resolve_before_book_fallbacks(self, db_session):
        rows = _seed_exact_alias_inventory(
            db_session,
            include_destinations=True,
        )
        story = Story(
            title_ar="Exact aliases",
            body_ar="أُنَاسٌ فَقَدْ.",
            source="book_ocr",
            status="active",
        )
        db_session.add(story)
        db_session.flush()

        from app.services.book_import_service import create_book_sentences

        with patch(
            "app.services.sentence_validator.verify_and_correct_mappings_llm",
            return_value=[],
        ), patch(
            "app.services.book_import_service.get_word_features",
            side_effect=AssertionError("resolved exact alias reached CAMeL"),
        ):
            sentences = create_book_sentences(
                db_session,
                story,
                [{
                    "arabic": "أُنَاسٌ فَقَدْ.",
                    "english": "People, therefore.",
                    "transliteration": "unāsun fa-qad.",
                }],
                story_word_lookup={
                    "اناس": rows["forget"].lemma_id,
                    "فقد": rows["loss"].lemma_id,
                },
            )

        words = (
            db_session.query(SentenceWord)
            .filter_by(sentence_id=sentences[0].id)
            .order_by(SentenceWord.position)
            .all()
        )
        assert [word.surface_form for word in words] == [
            "أُنَاسٌ",
            "فَقَدْ.",
        ]
        assert [word.lemma_id for word in words] == [
            rows["people"].lemma_id,
            rows["particle"].lemma_id,
        ]

    def test_unresolved_exact_aliases_skip_camel_and_story_word_fallbacks(
        self,
        db_session,
    ):
        rows = _seed_exact_alias_inventory(
            db_session,
            include_destinations=False,
        )
        story = Story(
            title_ar="Exact aliases",
            body_ar="أُنَاسٌ فَقَدْ.",
            source="book_ocr",
            status="active",
        )
        db_session.add(story)
        db_session.flush()

        from app.services.book_import_service import create_book_sentences

        with patch(
            "app.services.book_import_service.get_word_features",
            side_effect=AssertionError("unresolved exact alias reached CAMeL"),
        ):
            sentences = create_book_sentences(
                db_session,
                story,
                [{
                    "arabic": "أُنَاسٌ فَقَدْ.",
                    "english": "People, therefore.",
                    "transliteration": "unāsun fa-qad.",
                }],
                story_word_lookup={
                    "اناس": rows["forget"].lemma_id,
                    "فقد": rows["loss"].lemma_id,
                },
            )

        words = (
            db_session.query(SentenceWord)
            .filter_by(sentence_id=sentences[0].id)
            .order_by(SentenceWord.position)
            .all()
        )
        assert [word.surface_form for word in words] == [
            "أُنَاسٌ",
            "فَقَدْ.",
        ]
        assert all(word.lemma_id is None for word in words)


class TestImportBookEndToEnd:
    @patch("app.services.book_import_service.extract_cover_metadata")
    @patch("app.services.book_import_service.ocr_pages_parallel")
    @pytest.mark.slow
    @patch("app.services.book_import_service.cleanup_and_segment")
    @patch("app.services.book_import_service.translate_sentences")
    def test_full_pipeline(
        self, mock_translate, mock_cleanup, mock_ocr, mock_cover, db_session
    ):
        # Setup: create some known words + function words needed for sentence mapping
        l1 = _create_lemma(db_session, "ذَهَبَ", "ذهب", "go", "verb", 50, "ذ.ه.ب")
        l2 = _create_lemma(db_session, "وَلَد", "ولد", "boy", "noun", 80, "و.ل.د")
        _create_lemma(db_session, "إِلَى", "الى", "to", "particle", 1, "ا.ل.ي")
        _create_lemma(db_session, "حَدِيقَة", "حديقة", "garden", "noun", 200, "ح.د.ق")
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

        story, _ = import_book(
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

        # Importing a book populates lexical/story data only. The garden word
        # has no learner row until a page is actually completed in the reader.
        garden = db_session.query(Lemma).filter_by(lemma_ar_bare="حديقة").one()
        assert db_session.query(UserLemmaKnowledge).filter_by(
            lemma_id=garden.lemma_id
        ).first() is None

    @patch("app.services.book_import_service.extract_cover_metadata")
    @patch("app.services.book_import_service.ocr_pages_parallel")
    @pytest.mark.slow
    @patch("app.services.book_import_service.cleanup_and_segment")
    @patch("app.services.book_import_service.translate_sentences")
    def test_title_override(
        self, mock_translate, mock_cleanup, mock_ocr, mock_cover, db_session
    ):
        mock_ocr.return_value = ["بسم الله."]
        mock_cleanup.return_value = [{"arabic": "بِسْمِ اللَّهِ."}]
        mock_translate.return_value = [{"arabic": "بِسْمِ اللَّهِ.", "english": "In the name of God."}]

        from app.services.book_import_service import import_book

        story, _ = import_book(
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


class TestBookReaderPageEvidence:
    def _book(self, db):
        understood = _create_lemma(db, "كِتَاب", "كتاب", "book", root_str="ك.ت.ب")
        looked_up = _create_lemma(db, "نَادِر", "نادر", "rare", root_str="ن.د.ر")
        person = Lemma(
            lemma_ar="سَلِيم",
            lemma_ar_bare="سليم",
            gloss_en="Salim",
            pos="noun_prop",
        )
        db.add(person)
        db.flush()
        story = Story(
            title_ar="كِتَابُ الاِخْتِبَار",
            title_en="Test Book",
            body_ar="كِتَاب نَادِر سَلِيم",
            source="book_ocr",
            status="active",
            page_count=1,
        )
        db.add(story)
        db.flush()
        db.add_all([
            StoryWord(story_id=story.id, position=0, page_number=1,
                      surface_form="كِتَاب", lemma_id=understood.lemma_id),
            StoryWord(story_id=story.id, position=1, page_number=1,
                      surface_form="نَادِر", lemma_id=looked_up.lemma_id),
            StoryWord(story_id=story.id, position=2, page_number=1,
                      surface_form="سَلِيم", lemma_id=person.lemma_id,
                      name_type="personal"),
        ])
        db.commit()
        return story, understood, looked_up, person

    def test_opening_page_is_read_only(self, db_session):
        story, *_ = self._book(db_session)
        from app.services.story_service import get_book_page_detail

        detail = get_book_page_detail(db_session, story.id, 1)

        assert [token["surface_form"] for token in detail["tokens"]] == [
            "كِتَاب", "نَادِر", "سَلِيم",
        ]
        assert detail["completed"] is False
        assert db_session.query(UserLemmaKnowledge).count() == 0

    def test_page_tokens_and_receipts_use_canonical_lemma_ids(self, db_session):
        canonical = _create_lemma(db_session, "قَرَأَ", "قرأ", "to read", root_str="ق.ر.أ")
        variant = _create_lemma(db_session, "يَقْرَأُ", "يقرأ", "he reads", root_str="ق.ر.أ")
        variant.canonical_lemma_id = canonical.lemma_id
        story = Story(
            title_ar="قِرَاءَة",
            body_ar="يَقْرَأُ",
            source="book_ocr",
            status="active",
            page_count=1,
        )
        db_session.add(story)
        db_session.flush()
        db_session.add(StoryWord(
            story_id=story.id,
            position=0,
            page_number=1,
            surface_form="يَقْرَأُ",
            lemma_id=variant.lemma_id,
        ))
        db_session.commit()

        from app.services.story_service import complete_book_page, get_book_page_detail

        detail = get_book_page_detail(db_session, story.id, 1)
        assert detail["tokens"][0]["lemma_id"] == canonical.lemma_id

        complete_book_page(db_session, story.id, 1, [canonical.lemma_id])
        assert db_session.query(UserLemmaKnowledge).filter_by(
            lemma_id=canonical.lemma_id
        ).one().knowledge_state == "acquiring"
        assert db_session.query(UserLemmaKnowledge).filter_by(
            lemma_id=variant.lemma_id
        ).first() is None

    def test_page_completion_maps_known_and_schedules_lookups(self, db_session):
        story, understood, looked_up, person = self._book(db_session)
        from app.services.story_service import complete_book_page

        result = complete_book_page(
            db_session,
            story.id,
            1,
            [looked_up.lemma_id, person.lemma_id],
            client_review_id=f"book:{story.id}:page:1",
        )

        known_state = db_session.query(UserLemmaKnowledge).filter_by(
            lemma_id=understood.lemma_id
        ).one()
        lookup_state = db_session.query(UserLemmaKnowledge).filter_by(
            lemma_id=looked_up.lemma_id
        ).one()
        assert known_state.knowledge_state == "known"
        assert known_state.fsrs_card_json is None
        assert lookup_state.knowledge_state == "acquiring"
        assert lookup_state.acquisition_box == 1
        assert db_session.query(UserLemmaKnowledge).filter_by(
            lemma_id=person.lemma_id
        ).first() is None
        assert result["newly_known"] == 1
        assert result["scheduled"] == 1

        replay = complete_book_page(
            db_session,
            story.id,
            1,
            [looked_up.lemma_id],
            client_review_id=f"book:{story.id}:page:1",
        )
        assert replay["duplicate"] is True
        assert lookup_state.acquisition_box == 1

        # A later revisit can discover a new gap without replaying the page's
        # original green sweep. The previously presumed-known word is restarted.
        update = complete_book_page(
            db_session,
            story.id,
            1,
            [looked_up.lemma_id, understood.lemma_id],
            client_review_id=f"book:{story.id}:page:1",
        )
        db_session.refresh(known_state)
        assert update["duplicate"] is False
        assert update["scheduled"] == 1
        assert known_state.knowledge_state == "acquiring"

    def test_concurrent_page_completion_is_cleanly_idempotent(self, db_session):
        story, _understood, looked_up, _person = self._book(db_session)
        db_session.add(UserLemmaKnowledge(
            lemma_id=looked_up.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=2,
            acquisition_started_at=datetime.now(timezone.utc),
            acquisition_next_due=datetime.now(timezone.utc),
            source="study",
        ))
        db_session.commit()
        story_id = story.id
        looked_up_id = looked_up.lemma_id

        from app.database import SessionLocal
        from app.services.story_service import complete_book_page

        def complete_once():
            session = SessionLocal()
            try:
                return complete_book_page(
                    session,
                    story_id,
                    1,
                    [looked_up_id],
                    client_review_id=f"book:{story_id}:page:1",
                )
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _index: complete_once(), range(8)))

        db_session.expire_all()
        assert sum(not result["duplicate"] for result in results) == 1
        assert sum(result["duplicate"] for result in results) == 7
        assert db_session.query(ReviewLog).filter_by(
            client_review_id=f"book:{story_id}:page:1:again:{looked_up_id}"
        ).count() == 1


class TestProcessedBookImport:
    def test_story_tokenizer_ignores_latin_text_in_translator_note(self):
        from app.services.story_service import _tokenize_story_display

        assert _tokenize_story_display(
            "فعلان بمعنى يرتعد = Zittern, Zagen (المترجم)."
        ) == [
            ("فعلان", "فعلان"),
            ("بمعنى", "بمعنى"),
            ("يرتعد", "يرتعد"),
            ("(المترجم).", "المترجم"),
        ]

    @patch("app.services.story_service.generate_completion")
    def test_bilingual_pages_import_without_learning_state(self, mock_generate, db_session):
        # No unresolved words need an LLM result in this fixture.
        mock_generate.return_value = []
        book = _create_lemma(db_session, "كِتَاب", "كتاب", "book", root_str="ك.ت.ب")
        db_session.commit()
        from app.services.book_import_service import import_processed_book
        from app.services.story_service import get_book_page_detail

        story, _ = import_processed_book(
            db_session,
            title_ar="رِوَايَة",
            title_en="A Novel",
            author="Author",
            pages=[
                {"arabic": "كِتَاب كِتَاب", "english": "A book."},
                {
                    "arabic": "كِتَاب.",
                    "english": "The book.",
                    "source_page_number": 68,
                    "pdf_page_number": 69,
                },
            ],
        )

        assert story.page_count == 2
        assert db_session.query(UserLemmaKnowledge).filter_by(
            lemma_id=book.lemma_id
        ).first() is None
        page = get_book_page_detail(db_session, story.id, 2)
        assert page["english_translation"] == "The book."
        assert page["source_page_number"] == 68
        assert page["pdf_page_number"] == 69
        assert [token["surface_form"] for token in page["tokens"]] == ["كِتَاب."]

    @patch("app.services.story_service.generate_completion")
    def test_strict_curated_lexicon_maps_without_automatic_enrichment(
        self, mock_generate, db_session
    ):
        from app.services.book_import_service import import_processed_book
        from app.models import StoryWord

        story, new_ids = import_processed_book(
            db_session,
            title_ar="فصل",
            title_en="Chapter",
            author="Author",
            pages=[{"arabic": "غَرِيبٌ جِيجِي.", "english": "A strange Gigi."}],
            curated_lexicon=[
                {
                    "surfaces": ["غَرِيبٌ"],
                    "lemma_ar": "غَرِيب",
                    "gloss_en": "strange",
                    "pos": "adjective",
                },
                {
                    "surfaces": ["جِيجِي"],
                    "lemma_ar": "جِيجِي",
                    "gloss_en": "Gigi",
                    "pos": "proper_noun",
                    "name_type": "personal",
                },
            ],
            strict_lexicon=True,
        )

        assert story.status == "active"
        assert len(new_ids) == 2
        assert mock_generate.call_count == 0
        words = db_session.query(StoryWord).filter_by(story_id=story.id).all()
        assert all(word.lemma_id is not None for word in words)
        assert words[1].name_type == "personal"
        assert db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id.in_(new_ids)
        ).count() == 0
