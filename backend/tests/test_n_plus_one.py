"""Tests for N+1 query fixes in review, words, and story_service."""

from datetime import datetime, timezone, timedelta

import pytest

from app.models import (
    Lemma, Root, UserLemmaKnowledge, Story, StoryWord,
)
from app.services.fsrs_service import create_new_card
from tests.conftest import count_queries


def _make_card(stability_days=30.0):
    card = create_new_card()
    card["stability"] = stability_days
    card["due"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return card


class TestWordLookupSiblingBatch:
    """Verify word-lookup batches sibling ULK queries instead of N+1."""

    def test_returns_all_siblings_with_correct_states(self, client, db_session):
        root = Root(root="ك.ت.ب", core_meaning_en="writing")
        db_session.add(root)
        db_session.flush()

        # Primary word
        primary = Lemma(lemma_ar="كِتَاب", lemma_ar_bare="كتاب", root_id=root.root_id, pos="noun", gloss_en="book")
        db_session.add(primary)
        db_session.flush()
        db_session.add(UserLemmaKnowledge(lemma_id=primary.lemma_id, knowledge_state="learning", fsrs_card_json=_make_card(), source="study"))

        # 5 siblings with different states
        states = ["learning", "known", "encountered", "acquiring", "new"]
        siblings = []
        for i, state in enumerate(states):
            sib = Lemma(lemma_ar=f"sib_{i}", lemma_ar_bare=f"sib_{i}", root_id=root.root_id, pos="noun", gloss_en=f"sib {i}")
            db_session.add(sib)
            db_session.flush()
            if state != "new":
                db_session.add(UserLemmaKnowledge(lemma_id=sib.lemma_id, knowledge_state=state, fsrs_card_json=_make_card() if state != "encountered" else None, source="study"))
            siblings.append((sib, state))

        db_session.commit()

        resp = client.get(f"/api/review/word-lookup/{primary.lemma_id}")
        assert resp.status_code == 200
        data = resp.json()

        family = data["root_family"]
        assert len(family) == 5

        family_by_id = {f["lemma_id"]: f for f in family}
        for sib, expected_state in siblings:
            entry = family_by_id[sib.lemma_id]
            assert entry["state"] == expected_state if expected_state != "new" else "new"

    def test_sibling_query_count_is_bounded(self, client, db_session):
        """Verify we use batch queries, not one per sibling."""
        root = Root(root="د.ر.س", core_meaning_en="study")
        db_session.add(root)
        db_session.flush()

        primary = Lemma(lemma_ar="دَرْس", lemma_ar_bare="درس", root_id=root.root_id, pos="noun", gloss_en="lesson")
        db_session.add(primary)
        db_session.flush()
        db_session.add(UserLemmaKnowledge(lemma_id=primary.lemma_id, knowledge_state="learning", fsrs_card_json=_make_card(), source="study"))

        for i in range(10):
            sib = Lemma(lemma_ar=f"sib_{i}", lemma_ar_bare=f"sib_{i}", root_id=root.root_id, pos="noun", gloss_en=f"sib {i}")
            db_session.add(sib)
            db_session.flush()
            db_session.add(UserLemmaKnowledge(lemma_id=sib.lemma_id, knowledge_state="learning", fsrs_card_json=_make_card(), source="study"))

        db_session.commit()

        with count_queries(db_session) as counter:
            resp = client.get(f"/api/review/word-lookup/{primary.lemma_id}")

        assert resp.status_code == 200
        assert len(resp.json()["root_family"]) == 10
        # With batch: should be ~4 queries (lemma, siblings, ULKs batch, grammar features)
        # Without batch: would be 4 + 10 = 14
        assert counter["count"] <= 8


class TestProperNamesBatch:
    """Verify proper names endpoint batches story lookups."""

    def test_returns_names_with_story_titles(self, client, db_session):
        story1 = Story(title_ar="قصة ١", title_en="Story 1", body_ar="text", source="imported", status="active")
        story2 = Story(title_ar="قصة ٢", title_en="Story 2", body_ar="text", source="imported", status="active")
        db_session.add_all([story1, story2])
        db_session.flush()

        sw1 = StoryWord(story_id=story1.id, position=0, surface_form="أحمد", name_type="personal", gloss_en="Ahmed")
        sw2 = StoryWord(story_id=story2.id, position=0, surface_form="القاهرة", name_type="place", gloss_en="Cairo")
        sw3 = StoryWord(story_id=story1.id, position=1, surface_form="فاطمة", name_type="personal", gloss_en="Fatima")
        db_session.add_all([sw1, sw2, sw3])
        db_session.commit()

        resp = client.get("/api/words?category=names")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data) == 3
        titles = {d["surface_form"]: d["story_title"] for d in data}
        assert titles["أحمد"] == "Story 1"
        assert titles["القاهرة"] == "Story 2"
        assert titles["فاطمة"] == "Story 1"

    def test_name_query_count_is_bounded(self, client, db_session):
        """Verify we batch-load stories, not one per name."""
        stories = []
        for i in range(5):
            s = Story(title_en=f"Story {i}", body_ar="text", source="imported", status="active")
            db_session.add(s)
            db_session.flush()
            stories.append(s)

        for i, s in enumerate(stories):
            for j in range(3):
                sw = StoryWord(story_id=s.id, position=j, surface_form=f"name_{i}_{j}", name_type="personal", gloss_en=f"Name {i}.{j}")
                db_session.add(sw)
        db_session.commit()

        with count_queries(db_session) as counter:
            resp = client.get("/api/words?category=names")

        assert resp.status_code == 200
        # With batch: 2 queries (story_words + stories batch)
        # Without batch: 1 + N (one per unique name)
        assert counter["count"] <= 4


class TestStoryKnowledgeMapScope:
    """Verify story knowledge map uses scoped queries."""

    def test_get_story_detail_scoped(self, db_session):
        from app.services.story_service import get_story_detail

        # Create words in DB that are NOT part of the story
        other_lemma = Lemma(lemma_ar="أخرى", lemma_ar_bare="اخرى", pos="adj", gloss_en="other")
        db_session.add(other_lemma)
        db_session.flush()
        db_session.add(UserLemmaKnowledge(lemma_id=other_lemma.lemma_id, knowledge_state="known", source="study"))

        # Create story with one word
        story_lemma = Lemma(lemma_ar="كِتَاب", lemma_ar_bare="كتاب", pos="noun", gloss_en="book")
        db_session.add(story_lemma)
        db_session.flush()
        db_session.add(UserLemmaKnowledge(lemma_id=story_lemma.lemma_id, knowledge_state="learning", source="study"))

        story = Story(title_en="Test", body_ar="كتاب", source="imported", status="active")
        db_session.add(story)
        db_session.flush()

        sw = StoryWord(story_id=story.id, position=0, surface_form="كِتَاب", lemma_id=story_lemma.lemma_id)
        db_session.add(sw)
        db_session.commit()

        result = get_story_detail(db_session, story.id)
        assert len(result["words"]) == 1
        assert result["words"][0]["is_known"] is True
