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
        assert [token["reader_gloss_eligible"] for token in detail["tokens"]] == [
            True, True, False,
        ]
        assert db_session.query(UserLemmaKnowledge).count() == 0

    def test_guided_passage_leaves_unintroduced_vocabulary_entirely_inert(self, db_session):
        story, *_ = self._book(db_session)
        from app.services.story_service import complete_book_page

        result = complete_book_page(
            db_session,
            story.id,
            1,
            reader_policy="guided",
            sentence_indices=[0],
            client_review_id="guided-inert",
        )

        assert result["guided_inert"] == 3
        assert result["guided_started"] == 0
        assert result["box2_floor"] == 0
        assert db_session.query(UserLemmaKnowledge).count() == 0
        assert db_session.query(ReviewLog).count() == 0

    def test_guided_toggle_starts_box_one_without_fabricated_miss(self, db_session):
        story, understood, *_ = self._book(db_session)
        from app.services.story_service import complete_book_page

        result = complete_book_page(
            db_session,
            story.id,
            1,
            reader_policy="guided",
            sentence_indices=[0],
            learn_token_positions=[0],
            client_review_id="guided-learn",
        )

        state = db_session.query(UserLemmaKnowledge).filter_by(
            lemma_id=understood.lemma_id
        ).one()
        assert state.knowledge_state == "acquiring"
        assert state.acquisition_box == 1
        assert state.acquisition_next_due <= datetime.now(timezone.utc).replace(tzinfo=None)
        assert result["guided_started"] == 1
        assert result["guided_inert"] == 2
        assert db_session.query(ReviewLog).filter_by(
            lemma_id=understood.lemma_id
        ).count() == 0

    def test_clean_revisit_activates_only_previously_guided_inert_words(self, db_session):
        story, understood, looked_up, person = self._book(db_session)
        from app.services.story_service import complete_book_page

        complete_book_page(
            db_session,
            story.id,
            1,
            reader_policy="guided",
            sentence_indices=[0],
            client_review_id="guided-first",
        )
        result = complete_book_page(
            db_session,
            story.id,
            1,
            reader_policy="clean",
            sentence_indices=[0],
            client_review_id="clean-revisit",
        )

        assert result["guided_inert"] == 0
        assert result["box2_floor"] == 3
        assert {
            row.lemma_id: row.acquisition_box
            for row in db_session.query(UserLemmaKnowledge).all()
        } == {
            understood.lemma_id: 2,
            looked_up.lemma_id: 2,
            person.lemma_id: 2,
        }

    def test_stale_unmapped_word_resolves_existing_lemma_without_writing(self, db_session):
        existing = _create_lemma(
            db_session, "كِتَاب", "كتاب", "book", root_str="ك.ت.ب"
        )
        story = Story(
            title_ar="فصل",
            body_ar="كِتَاب.",
            source="book_ocr",
            status="active",
            page_count=1,
        )
        db_session.add(story)
        db_session.flush()
        word = StoryWord(
            story_id=story.id,
            position=0,
            page_number=1,
            sentence_index=0,
            surface_form="كِتَاب.",
            lemma_id=None,
        )
        db_session.add(word)
        db_session.commit()

        from app.services.story_service import complete_book_page, get_book_page_detail

        detail = get_book_page_detail(db_session, story.id, 1)

        assert detail["tokens"][0]["lemma_id"] == existing.lemma_id
        assert detail["tokens"][0]["has_full_entry"] is True
        assert db_session.query(StoryWord).filter_by(id=word.id).one().lemma_id is None
        assert db_session.query(UserLemmaKnowledge).count() == 0

        with pytest.raises(ValueError, match="existing lemma"):
            complete_book_page(
                db_session,
                story.id,
                1,
                sentence_indices=[0],
                passage_token_positions=[0],
                dont_learn_token_positions=[0],
                client_review_id="stale-existing-opt-out",
            )

        assert db_session.query(StoryWord).filter_by(id=word.id).one().lemma_id is None
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
        assert known_state.knowledge_state == "acquiring"
        assert known_state.acquisition_box == 2
        assert known_state.fsrs_card_json is None
        assert lookup_state.knowledge_state == "acquiring"
        assert lookup_state.acquisition_box == 1
        assert db_session.query(UserLemmaKnowledge).filter_by(
            lemma_id=person.lemma_id
        ).one().acquisition_box == 1
        assert result["newly_known"] == 1
        assert result["box2_floor"] == 1
        assert result["reviewed_again"] == 2

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
            client_review_id=f"book:{story.id}:page:1:revisit",
        )
        db_session.refresh(known_state)
        assert update["duplicate"] is False
        assert update["reviewed_again"] == 3  # two original misses + one newly discovered gap
        assert known_state.knowledge_state == "acquiring"
        assert known_state.acquisition_box == 1

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

    def test_only_displayed_passage_changes_state(self, db_session):
        first = _create_lemma(db_session, "أَوَّل", "اول", "first", root_str="أ.و.ل")
        later = _create_lemma(db_session, "لَاحِق", "لاحق", "later", root_str="ل.ح.ق")
        story = Story(title_ar="فصل", body_ar="أَوَّل. لَاحِق.", source="book_ocr", status="active", page_count=1)
        db_session.add(story)
        db_session.flush()
        db_session.add_all([
            StoryWord(story_id=story.id, position=0, page_number=1, sentence_index=10,
                      surface_form="أَوَّل.", lemma_id=first.lemma_id),
            StoryWord(story_id=story.id, position=1, page_number=1, sentence_index=11,
                      surface_form="لَاحِق.", lemma_id=later.lemma_id),
        ])
        db_session.commit()

        from app.services.story_service import complete_book_page
        complete_book_page(
            db_session, story.id, 1, sentence_indices=[10], passage_token_positions=[0],
            client_review_id="passage-only-10",
        )

        assert db_session.query(UserLemmaKnowledge).filter_by(
            lemma_id=first.lemma_id
        ).one().acquisition_box == 2
        assert db_session.query(UserLemmaKnowledge).filter_by(
            lemma_id=later.lemma_id
        ).first() is None

        with pytest.raises(ValueError, match="displayed reader units"):
            complete_book_page(
                db_session, story.id, 1,
                passage_token_positions=[0, 999],
                client_review_id="forged-reader-range",
            )

    def test_long_literary_sentence_splits_only_at_preserved_punctuation(self):
        from app.services.story_service import (
            _book_reader_token_units,
            _leading_english_heading_count,
            _split_english_reader_segments,
        )
        tokens = [
            {
                "position": index,
                "sentence_index": 7,
                "surface_form": f"كلمة{('،' if index in (29, 59, 89) else '')}",
            }
            for index in range(100)
        ]

        units = _book_reader_token_units(tokens)

        assert len(units) == 3
        assert [unit[-1]["position"] for unit in units[:-1]] == [29, 59]
        assert [token["position"] for unit in units for token in unit] == list(range(100))

        english = _split_english_reader_segments(
            "CHAPTER FIVE\nSTORIES FOR MANY AND STORIES FOR ONE\n"
            "Little by little, Momo became indispensable to Gigi."
        )
        assert _leading_english_heading_count(english, maximum=3) == 2

    def test_existing_box_three_lookup_is_normal_review_miss(self, db_session, monkeypatch):
        story, _understood, looked_up, _person = self._book(db_session)
        looked_up.memory_hooks_json = {"approved": True}
        db_session.add(UserLemmaKnowledge(
            lemma_id=looked_up.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=3,
            acquisition_started_at=datetime.now(timezone.utc),
            acquisition_next_due=datetime.now(timezone.utc),
            source="study",
            times_seen=3,
            times_correct=3,
        ))
        db_session.commit()
        monkeypatch.setattr(
            "app.services.memory_hooks.regenerate_memory_hooks_premium",
            lambda *_args, **_kwargs: None,
        )

        from app.services.story_service import complete_book_page
        complete_book_page(
            db_session, story.id, 1,
            reader_policy="guided",
            unknown_lemma_ids=[looked_up.lemma_id],
            sentence_indices=[0],
            client_review_id="book-box3-miss",
        )

        ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=looked_up.lemma_id).one()
        log = db_session.query(ReviewLog).filter_by(
            client_review_id=f"book-box3-miss:again:{looked_up.lemma_id}"
        ).one()
        assert ulk.acquisition_box == 1
        assert log.rating == 1
        assert log.fsrs_log_json["acquisition_box_before"] == 3
        assert log.fsrs_log_json["acquisition_box_after"] == 1

    def test_existing_fsrs_lookup_is_normal_fsrs_miss(self, db_session, monkeypatch):
        story, _understood, looked_up, _person = self._book(db_session)
        from app.services.fsrs_service import create_new_card
        looked_up.memory_hooks_json = {"approved": True}
        db_session.add(UserLemmaKnowledge(
            lemma_id=looked_up.lemma_id,
            knowledge_state="known",
            fsrs_card_json=create_new_card(),
            source="study",
            times_seen=8,
            times_correct=7,
        ))
        db_session.commit()
        monkeypatch.setattr(
            "app.services.memory_hooks.regenerate_memory_hooks_premium",
            lambda *_args, **_kwargs: None,
        )

        from app.services.story_service import complete_book_page
        complete_book_page(
            db_session, story.id, 1,
            unknown_lemma_ids=[looked_up.lemma_id],
            sentence_indices=[0],
            client_review_id="book-fsrs-miss",
        )

        log = db_session.query(ReviewLog).filter_by(
            client_review_id=f"book-fsrs-miss:again:{looked_up.lemma_id}"
        ).one()
        assert log.rating == 1
        assert log.is_acquisition is False
        assert log.fsrs_log_json["fsrs_rating_applied"] == 1
        assert log.fsrs_log_json["pre_knowledge_state"] == "known"

    def test_dont_learn_unmapped_never_imports_or_creates_knowledge(self, db_session, monkeypatch):
        story = Story(title_ar="فصل", body_ar="زَرْدَق.", source="book_ocr", status="active", page_count=1)
        db_session.add(story)
        db_session.flush()
        db_session.add(StoryWord(
            story_id=story.id, position=0, page_number=1, sentence_index=0,
            surface_form="زَرْدَق.", lemma_id=None,
        ))
        db_session.commit()
        importer = MagicMock(return_value=[])
        monkeypatch.setattr("app.services.story_service._import_unknown_words", importer)

        from app.services.story_service import complete_book_page
        result = complete_book_page(
            db_session, story.id, 1,
            sentence_indices=[0],
            dont_learn_token_positions=[0],
            client_review_id="book-opt-out",
        )

        importer.assert_not_called()
        assert result["dont_learn"] == 1
        assert db_session.query(Lemma).filter_by(lemma_ar_bare="زردق").first() is None
        assert db_session.query(UserLemmaKnowledge).count() == 0

    def test_guided_unmapped_word_is_inert_without_running_import(self, db_session, monkeypatch):
        story = Story(title_ar="فصل", body_ar="زَرْدَق.", source="book_ocr", status="active", page_count=1)
        db_session.add(story)
        db_session.flush()
        db_session.add(StoryWord(
            story_id=story.id, position=0, page_number=1, sentence_index=0,
            surface_form="زَرْدَق.", gloss_en="a zardaq", lemma_id=None,
        ))
        db_session.commit()
        importer = MagicMock(return_value=[])
        monkeypatch.setattr("app.services.story_service._import_unknown_words", importer)

        from app.services.story_service import complete_book_page
        result = complete_book_page(
            db_session, story.id, 1,
            reader_policy="guided",
            sentence_indices=[0],
            client_review_id="guided-unmapped-inert",
        )

        importer.assert_not_called()
        assert result["guided_inert"] == 1
        assert db_session.query(Lemma).filter_by(lemma_ar_bare="زردق").first() is None
        assert db_session.query(UserLemmaKnowledge).count() == 0

    @pytest.mark.parametrize("reader_policy, mark_unknown, learn_explicitly, expected_box, expected_reviews", [
        ("clean", False, False, 2, 0),
        ("clean", True, False, 1, 0),
        ("guided", False, True, 1, 0),
    ])
    def test_new_reader_word_uses_full_inline_import_before_admission(
        self, db_session, monkeypatch, reader_policy, mark_unknown,
        learn_explicitly, expected_box, expected_reviews
    ):
        story = Story(title_ar="فصل", body_ar="زَرْدَق.", source="book_ocr", status="active", page_count=1)
        db_session.add(story)
        db_session.flush()
        word = StoryWord(
            story_id=story.id, position=0, page_number=1, sentence_index=0,
            surface_form="زَرْدَق.", lemma_id=None,
        )
        db_session.add(word)
        db_session.commit()

        calls = []
        def fully_imported(db, imported_story, _lookup, **kwargs):
            calls.append(kwargs)
            lemma = Lemma(
                lemma_ar="زَرْدَقَ", lemma_ar_bare="زردق", gloss_en="to zardaq",
                pos="verb", source="story_import", source_story_id=imported_story.id,
                gates_completed_at=datetime.now(timezone.utc),
                forms_json={"past_3ms": "زَرْدَقَ"},
                etymology_json={"summary": "test"},
            )
            db.add(lemma)
            db.flush()
            db.query(StoryWord).filter_by(id=word.id).one().lemma_id = lemma.lemma_id
            db.commit()
            return [lemma.lemma_id]
        monkeypatch.setattr("app.services.story_service._import_unknown_words", fully_imported)

        from app.services.story_service import complete_book_page
        complete_book_page(
            db_session, story.id, 1,
            reader_policy=reader_policy,
            sentence_indices=[0],
            unknown_token_positions=[0] if mark_unknown else [],
            learn_token_positions=[0] if learn_explicitly else [],
            client_review_id=f"book-new-{reader_policy}-{mark_unknown}-{learn_explicitly}",
        )

        assert calls == [{
            "target_positions": {0},
            "background_enrich": False,
            "include_proper_names_as_lemmas": True,
        }]
        lemma = db_session.query(Lemma).filter_by(lemma_ar_bare="زردق").one()
        assert lemma.gates_completed_at is not None
        assert lemma.forms_json
        assert lemma.etymology_json
        ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).one()
        assert ulk.acquisition_box == expected_box
        assert db_session.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).count() == expected_reviews

    def test_dont_learn_is_rejected_for_existing_lemma(self, db_session):
        story, understood, *_ = self._book(db_session)
        from app.services.story_service import complete_book_page

        with pytest.raises(ValueError, match="existing lemma"):
            complete_book_page(
                db_session, story.id, 1,
                sentence_indices=[0],
                dont_learn_token_positions=[0],
                client_review_id="invalid-existing-opt-out",
            )

    def test_guided_policy_rejects_opt_out_and_learning_toggle_for_introduced_word(self, db_session):
        story, understood, *_ = self._book(db_session)
        db_session.add(UserLemmaKnowledge(
            lemma_id=understood.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_started_at=datetime.now(timezone.utc),
            acquisition_next_due=datetime.now(timezone.utc),
            source="study",
        ))
        db_session.commit()
        from app.services.story_service import complete_book_page

        with pytest.raises(ValueError, match="already untracked"):
            complete_book_page(
                db_session, story.id, 1,
                reader_policy="guided",
                sentence_indices=[0],
                dont_learn_token_positions=[1],
                client_review_id="guided-invalid-optout",
            )
        with pytest.raises(ValueError, match="inline-glossed"):
            complete_book_page(
                db_session, story.id, 1,
                reader_policy="guided",
                sentence_indices=[0],
                learn_token_positions=[0],
                client_review_id="guided-invalid-learn",
            )


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
        assert page["passages"] == [{
            "sentence_index": 1,
            "sentence_indices": [1],
            "arabic_text": "كِتَاب.",
            "english_translation": "The book.",
            "token_positions": [2],
        }]

    @patch("app.services.lemma_quality.run_quality_gates")
    @patch("app.services.story_service.generate_completion")
    def test_strict_curated_lexicon_maps_then_runs_shared_full_enrichment(
        self, mock_generate, mock_quality_gates, db_session
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
        mock_quality_gates.assert_called_once_with(
            db_session,
            new_ids,
            background_enrich=False,
        )
        words = db_session.query(StoryWord).filter_by(story_id=story.id).all()
        assert all(word.lemma_id is not None for word in words)
        assert words[1].name_type == "personal"
        assert db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id.in_(new_ids)
        ).count() == 0
