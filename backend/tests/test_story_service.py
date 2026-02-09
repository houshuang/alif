"""Tests for story generation, import, and review service."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models import Lemma, Root, ReviewLog, Story, StoryWord, UserLemmaKnowledge
from app.services.fsrs_service import create_new_card
from app.services.story_service import (
    complete_story,
    generate_story,
    get_stories,
    get_story_detail,
    import_story,
    lookup_word,
    recalculate_readiness,
    skip_story,
    too_difficult_story,
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
        story = import_story(db_session, arabic_text="الولد في البيت", title="Test")
        assert story.source == "imported"
        assert story.status == "active"
        assert story.total_words > 0
        assert story.title_ar == "Test"

    def test_import_counts_known_words(self, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد كبير")
        assert story.known_count >= 1

    def test_import_identifies_unknown_words(self, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الرجل في البيت")
        assert story.unknown_count >= 1

    def test_import_creates_story_words(self, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد يكتب الكتاب")
        words = db_session.query(StoryWord).filter(StoryWord.story_id == story.id).all()
        assert len(words) > 0
        assert all(w.position >= 0 for w in words)

    def test_import_readiness_pct(self, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="في البيت")
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
        story = generate_story(db_session, difficulty="beginner", max_sentences=4)
        assert story.source == "generated"
        assert story.title_en == "The Story"
        assert story.body_ar == "الولد في البيت. الكتاب كبير."
        assert story.total_words > 0

    def test_generate_fails_with_no_words(self, db_session):
        with pytest.raises(ValueError, match="No known"):
            generate_story(db_session)


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
        story = import_story(db_session, arabic_text="الولد كبير")
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
        story = import_story(db_session, arabic_text="الولد في البيت")
        result = complete_story(db_session, story.id, looked_up_lemma_ids=[])
        assert result["status"] == "completed"
        db_session.refresh(story)
        assert story.status == "completed"
        assert story.completed_at is not None

    def test_complete_reviews_words(self, db_session):
        lemmas = _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد في البيت")
        result = complete_story(db_session, story.id, looked_up_lemma_ids=[])
        assert result["words_reviewed"] >= 1
        assert result["good_count"] >= 1

    def test_complete_with_looked_up_words(self, db_session):
        lemmas = _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد في البيت")
        wid = lemmas[2].lemma_id  # ولد
        result = complete_story(db_session, story.id, looked_up_lemma_ids=[wid])
        assert result["again_count"] >= 1

    def test_complete_is_idempotent(self, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد في البيت")

        first = complete_story(db_session, story.id, looked_up_lemma_ids=[])
        logs_after_first = db_session.query(ReviewLog).count()

        second = complete_story(db_session, story.id, looked_up_lemma_ids=[])
        logs_after_second = db_session.query(ReviewLog).count()

        assert first["status"] == "completed"
        assert second.get("duplicate") is True
        assert logs_after_second == logs_after_first

    def test_complete_after_skip_is_noop(self, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد في البيت")
        skip_story(db_session, story.id, looked_up_lemma_ids=[])
        logs_after_skip = db_session.query(ReviewLog).count()

        result = complete_story(db_session, story.id, looked_up_lemma_ids=[])
        logs_after_complete_attempt = db_session.query(ReviewLog).count()

        assert result.get("duplicate") is True
        assert result.get("conflict") is True
        assert result["status"] == "skipped"
        assert logs_after_complete_attempt == logs_after_skip


class TestSkipStory:
    def test_skip_marks_status(self, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد")
        result = skip_story(db_session, story.id)
        assert result["status"] == "skipped"
        db_session.refresh(story)
        assert story.status == "skipped"

    def test_skip_with_looked_up(self, db_session):
        lemmas = _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد")
        result = skip_story(db_session, story.id, looked_up_lemma_ids=[lemmas[2].lemma_id])
        assert result["again_count"] >= 1

    def test_skip_is_idempotent(self, db_session):
        lemmas = _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد")
        looked_up = [lemmas[2].lemma_id]

        first = skip_story(db_session, story.id, looked_up_lemma_ids=looked_up)
        logs_after_first = db_session.query(ReviewLog).count()

        second = skip_story(db_session, story.id, looked_up_lemma_ids=looked_up)
        logs_after_second = db_session.query(ReviewLog).count()

        assert first["status"] == "skipped"
        assert second.get("duplicate") is True
        assert logs_after_second == logs_after_first

    def test_skip_after_complete_is_noop(self, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد")
        complete_story(db_session, story.id, looked_up_lemma_ids=[])
        logs_after_complete = db_session.query(ReviewLog).count()

        result = skip_story(db_session, story.id, looked_up_lemma_ids=[])
        logs_after_skip_attempt = db_session.query(ReviewLog).count()

        assert result.get("duplicate") is True
        assert result.get("conflict") is True
        assert result["status"] == "completed"
        assert logs_after_skip_attempt == logs_after_complete


class TestTooDifficult:
    def test_too_difficult_marks_status(self, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد")
        result = too_difficult_story(db_session, story.id)
        assert result["status"] == "too_difficult"

    def test_too_difficult_is_idempotent(self, db_session):
        lemmas = _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد")
        looked_up = [lemmas[2].lemma_id]

        first = too_difficult_story(db_session, story.id, looked_up_lemma_ids=looked_up)
        logs_after_first = db_session.query(ReviewLog).count()

        second = too_difficult_story(db_session, story.id, looked_up_lemma_ids=looked_up)
        logs_after_second = db_session.query(ReviewLog).count()

        assert first["status"] == "too_difficult"
        assert second.get("duplicate") is True
        assert logs_after_second == logs_after_first


class TestLookupWord:
    def test_lookup_returns_details(self, db_session):
        lemmas = _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد في البيت")
        result = lookup_word(db_session, story.id, lemmas[2].lemma_id, 0)
        assert result["gloss_en"] == "boy"
        assert result["lemma_id"] == lemmas[2].lemma_id

    def test_lookup_not_found(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            lookup_word(db_session, 1, 9999, 0)


class TestRecalculateReadiness:
    def test_recalculate(self, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد في البيت")
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
        story = import_story(db_session, arabic_text="الولد كبير")
        resp = client.get(f"/api/stories/{story.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == story.id
        assert len(data["words"]) > 0

    def test_complete_story(self, client, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد في البيت")
        resp = client.post(f"/api/stories/{story.id}/complete", json={
            "looked_up_lemma_ids": [],
        })
        assert resp.status_code == 200

    def test_skip_story(self, client, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد")
        resp = client.post(f"/api/stories/{story.id}/skip", json={
            "looked_up_lemma_ids": [],
        })
        assert resp.status_code == 200

    def test_lookup_word(self, client, db_session):
        lemmas = _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد في البيت")
        resp = client.post(f"/api/stories/{story.id}/lookup", json={
            "lemma_id": lemmas[2].lemma_id,
            "position": 0,
        })
        assert resp.status_code == 200
        assert resp.json()["gloss_en"] == "boy"

    def test_readiness(self, client, db_session):
        _seed_words(db_session)
        story = import_story(db_session, arabic_text="الولد في البيت")
        resp = client.get(f"/api/stories/{story.id}/readiness")
        assert resp.status_code == 200
        assert "readiness_pct" in resp.json()

    def test_story_not_found(self, client):
        resp = client.get("/api/stories/9999")
        assert resp.status_code == 404
