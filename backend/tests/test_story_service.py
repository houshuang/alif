"""Tests for story generation, import, and review service."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models import Lemma, Root, ReviewLog, Story, StoryWord, UserLemmaKnowledge
import app.services.story_service as story_service_module
from app.services.fsrs_service import create_new_card
from app.services.story_service import (
    _check_story_compliance,
    _get_known_words,
    complete_story,
    generate_story,
    get_stories,
    get_story_detail,
    import_story,
    lookup_word,
    recalculate_readiness,
)


def _seed_words(db):
    """Create a set of test words with knowledge state."""
    root = Root(root="ك.ت.ب", core_meaning_en="writing")
    db.add(root)
    db.flush()

    words = [
        ("كِتَاب", "كتاب", "book", "noun", root.root_id),
        ("يَكْتُب", "يكتب", "writes", "verb", root.root_id),
        ("وَلَد", "ولد", "boy", "noun", None),
        ("بَيْت", "بيت", "house", "noun", None),
        ("كَبِير", "كبير", "big", "adj", None),
    ]

    lemmas = []
    for ar, bare, en, pos, rid in words:
        l = Lemma(
            lemma_ar=ar,
            lemma_ar_bare=bare,
            gloss_en=en,
            pos=pos,
            root_id=rid,
        )
        db.add(l)
        db.flush()
        lemmas.append(l)

    for l in lemmas:
        db.add(UserLemmaKnowledge(
            lemma_id=l.lemma_id,
            knowledge_state="known",
            fsrs_card_json=create_new_card(),
            times_seen=5,
            times_correct=4,
        ))

    db.commit()
    return lemmas


class TestImportStory:
    def test_import_basic(self, db_session):
        lemmas = _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد في البيت", title="Test")
        assert story.source == "imported"
        assert story.status == "active"
        assert story.total_words > 0
        assert story.title_ar == "Test"

    def test_import_counts_known_words(self, db_session):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد كبير")
        assert story.known_count >= 1

    def test_import_identifies_unknown_words(self, db_session):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الرجل في البيت")
        assert story.unknown_count >= 1

    def test_import_creates_story_words(self, db_session):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد يكتب الكتاب")
        words = db_session.query(StoryWord).filter(StoryWord.story_id == story.id).all()
        assert len(words) > 0
        assert all(w.position >= 0 for w in words)

    def test_import_readiness_pct(self, db_session):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="في البيت")
        assert story.readiness_pct > 0


class TestGenerateStory:
    @patch("app.services.story_service.generate_completion")
    def test_generate_with_mocked_llm(self, mock_gen, db_session):
        _seed_words(db_session)
        mock_gen.return_value = {
            "title_ar": "القصة",
            "title_en": "The Story",
            "body_ar": "الولد في البيت. الكتاب كبير.",
            "body_en": "The boy is at home. The book is big.",
            "transliteration": "al-walad fī al-bayt. al-kitāb kabīr.",
        }
        story, _ = generate_story(db_session, difficulty="beginner", max_sentences=4)
        assert story.source == "generated"
        assert story.title_en == "The Story"
        assert story.body_ar == "الولد في البيت. الكتاب كبير."
        assert story.total_words > 0

    def test_generate_fails_with_no_words(self, db_session):
        with pytest.raises(ValueError, match="No known"):
            generate_story(db_session)

    @patch("app.services.story_service.generate_completion")
    def test_correction_loop_fixes_unknown_words(self, mock_gen, db_session):
        """LLM returns story with unknown word, correction replaces it."""
        _seed_words(db_session)
        # First call: story with unknown word "جميل" (not in seed words)
        # Second call: corrected story with only known words
        mock_gen.side_effect = [
            {
                "title_ar": "القصة",
                "title_en": "The Story",
                "body_ar": "الولد جميل في البيت.",
                "body_en": "The boy is beautiful at home.",
                "transliteration": "al-walad jamīl fī al-bayt.",
            },
            {
                "title_ar": "القصة",
                "title_en": "The Story",
                "body_ar": "الولد كبير في البيت.",
                "body_en": "The boy is big at home.",
                "transliteration": "al-walad kabīr fī al-bayt.",
            },
        ]
        story, _ = generate_story(db_session, difficulty="beginner")
        assert story.body_ar == "الولد كبير في البيت."
        assert mock_gen.call_count == 2
        # Second call should be a correction prompt mentioning "جميل"
        correction_prompt = mock_gen.call_args_list[1][1].get("prompt") or mock_gen.call_args_list[1][0][0]
        assert "جميل" in correction_prompt

    def test_box1_words_excluded_from_known(self, db_session):
        """Acquiring words in box 1 should not appear in story vocabulary."""
        lemmas = _seed_words(db_session)
        # Add a box-1 acquiring word
        box1_lemma = Lemma(lemma_ar="جَمِيل", lemma_ar_bare="جميل", gloss_en="beautiful", pos="adj")
        db_session.add(box1_lemma)
        db_session.flush()
        db_session.add(UserLemmaKnowledge(
            lemma_id=box1_lemma.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            times_seen=1,
            times_correct=1,
        ))
        # Add a box-2 acquiring word
        box2_lemma = Lemma(lemma_ar="صَغِير", lemma_ar_bare="صغير", gloss_en="small", pos="adj")
        db_session.add(box2_lemma)
        db_session.flush()
        db_session.add(UserLemmaKnowledge(
            lemma_id=box2_lemma.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=2,
            times_seen=3,
            times_correct=2,
        ))
        db_session.commit()

        known = _get_known_words(db_session)
        known_ids = {w["lemma_id"] for w in known}
        assert box1_lemma.lemma_id not in known_ids
        assert box2_lemma.lemma_id in known_ids
        # Original 5 "known" words should all be present
        for l in lemmas:
            assert l.lemma_id in known_ids

    def test_compliance_checks_against_known_set(self, db_session):
        """Compliance check with known_lemma_ids rejects words not in that set."""
        from app.services.sentence_validator import build_lemma_lookup
        lemmas = _seed_words(db_session)
        # Add a word to DB but NOT to the known set
        extra = Lemma(lemma_ar="جَمِيل", lemma_ar_bare="جميل", gloss_en="beautiful", pos="adj")
        db_session.add(extra)
        db_session.commit()

        all_lemmas = db_session.query(Lemma).all()
        lookup = build_lemma_lookup(all_lemmas)
        known_ids = {l.lemma_id for l in lemmas}  # does NOT include extra

        # Story using "جميل" — exists in DB but not in known set
        pct_all, unknown_all = _check_story_compliance(
            "الولد جميل في البيت", lookup, known_lemma_ids=None
        )
        pct_known, unknown_known = _check_story_compliance(
            "الولد جميل في البيت", lookup, known_lemma_ids=known_ids
        )
        # Without known filter: جميل resolves to a lemma → 100%
        assert "جميل" not in unknown_all
        # With known filter: جميل is not in known set → flagged
        assert "جميل" in unknown_known
        assert pct_known < pct_all


class TestGetStories:
    def test_list_empty(self, db_session):
        result = get_stories(db_session)
        assert result == []

    def test_list_returns_stories(self, db_session):
        _seed_words(db_session)
        import_story(db_session, arabic_text="الولد في البيت")
        result = get_stories(db_session)
        assert len(result) == 1
        assert result[0]["source"] == "imported"

    def test_list_ordered_by_created_at_desc(self, db_session):
        _seed_words(db_session)
        import_story(db_session, arabic_text="الولد", title="First")
        import_story(db_session, arabic_text="البيت", title="Second")
        result = get_stories(db_session)
        assert result[0]["title_ar"] == "Second"
        assert result[1]["title_ar"] == "First"


class TestGetStoryDetail:
    def test_detail_includes_words(self, db_session):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد كبير")
        detail = get_story_detail(db_session, story.id)
        assert "words" in detail
        assert len(detail["words"]) > 0
        assert detail["words"][0]["surface_form"]

    def test_detail_not_found(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            get_story_detail(db_session, 9999)


class TestCompleteStory:
    def test_complete_marks_status(self, db_session):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد في البيت")
        result = complete_story(db_session, story.id, looked_up_lemma_ids=[])
        assert result["status"] == "completed"
        db_session.refresh(story)
        assert story.status == "completed"
        assert story.completed_at is not None

    def test_complete_reviews_words(self, db_session):
        lemmas = _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد في البيت")
        result = complete_story(db_session, story.id, looked_up_lemma_ids=[])
        assert result["words_reviewed"] >= 1
        assert result["good_count"] >= 1

    def test_complete_with_looked_up_words(self, db_session):
        lemmas = _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد في البيت")
        wid = lemmas[2].lemma_id  # ولد
        result = complete_story(db_session, story.id, looked_up_lemma_ids=[wid])
        assert result["again_count"] >= 1

    def test_complete_is_idempotent(self, db_session):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد في البيت")

        first = complete_story(db_session, story.id, looked_up_lemma_ids=[])
        logs_after_first = db_session.query(ReviewLog).count()

        second = complete_story(db_session, story.id, looked_up_lemma_ids=[])
        logs_after_second = db_session.query(ReviewLog).count()

        assert first["status"] == "completed"
        assert second.get("duplicate") is True
        assert logs_after_second == logs_after_first

    def test_complete_retry_after_midway_failure_resumes_without_duplicate_reviews(self, db_session, monkeypatch):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد في البيت")

        real_submit_review = story_service_module.submit_review
        calls = {"count": 0}

        def flaky_submit_review(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("simulated mid-story failure")
            return real_submit_review(*args, **kwargs)

        monkeypatch.setattr(story_service_module, "submit_review", flaky_submit_review)
        with pytest.raises(RuntimeError):
            complete_story(db_session, story.id, looked_up_lemma_ids=[])

        # Rollback the failed transaction so flushed-but-uncommitted data is discarded
        db_session.rollback()

        db_session.refresh(story)
        assert story.status == "active"

        monkeypatch.setattr(story_service_module, "submit_review", real_submit_review)
        result = complete_story(db_session, story.id, looked_up_lemma_ids=[])
        assert result["status"] == "completed"

        db_session.refresh(story)
        assert story.status == "completed"

        review_log_count = (
            db_session.query(ReviewLog)
            .filter(ReviewLog.client_review_id.like(f"story:{story.id}:complete:%"))
            .count()
        )
        # words_reviewed includes encountered words (no ReviewLog), so compare
        # against good_count + again_count which tracks actual FSRS reviews
        assert review_log_count == result["good_count"] + result["again_count"]


class TestLookupWord:
    def test_lookup_returns_details(self, db_session):
        lemmas = _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد في البيت")
        result = lookup_word(db_session, story.id, lemmas[2].lemma_id, 0)
        assert result["gloss_en"] == "boy"
        assert result["lemma_id"] == lemmas[2].lemma_id

    def test_lookup_not_found(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            lookup_word(db_session, 1, 9999, 0)


class TestRecalculateReadiness:
    def test_recalculate(self, db_session):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد في البيت")
        result = recalculate_readiness(db_session, story.id)
        assert "readiness_pct" in result
        assert "unknown_count" in result
        assert isinstance(result["unknown_words"], list)


class TestStoryAPI:
    def test_list_stories(self, client, db_session):
        _seed_words(db_session)
        import_story(db_session, arabic_text="الولد")
        resp = client.get("/api/stories")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_import_story(self, client, db_session):
        _seed_words(db_session)
        resp = client.post("/api/stories/import", json={
            "arabic_text": "الولد في البيت",
            "title": "Test Import",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "imported"
        assert data["title_ar"] == "Test Import"
        assert "words" in data

    def test_get_story_detail(self, client, db_session):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد كبير")
        resp = client.get(f"/api/stories/{story.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == story.id
        assert len(data["words"]) > 0

    def test_complete_story(self, client, db_session):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد في البيت")
        resp = client.post(f"/api/stories/{story.id}/complete", json={
            "looked_up_lemma_ids": [],
        })
        assert resp.status_code == 200

    def test_lookup_word(self, client, db_session):
        lemmas = _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد في البيت")
        resp = client.post(f"/api/stories/{story.id}/lookup", json={
            "lemma_id": lemmas[2].lemma_id,
            "position": 0,
        })
        assert resp.status_code == 200
        assert resp.json()["gloss_en"] == "boy"

    def test_readiness(self, client, db_session):
        _seed_words(db_session)
        story, _ = import_story(db_session, arabic_text="الولد في البيت")
        resp = client.get(f"/api/stories/{story.id}/readiness")
        assert resp.status_code == 200
        assert "readiness_pct" in resp.json()

    def test_story_not_found(self, client):
        resp = client.get("/api/stories/9999")
        assert resp.status_code == 404
