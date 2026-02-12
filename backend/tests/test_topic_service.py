"""Tests for topical learning cycle management."""

import pytest
from datetime import datetime, timezone

from app.models import Lemma, LearnerSettings, UserLemmaKnowledge
from app.services.topic_service import (
    DOMAINS,
    MAX_TOPIC_BATCH,
    MIN_TOPIC_WORDS,
    ensure_active_topic,
    get_available_topics,
    get_settings,
    record_introduction,
    select_best_topic,
    set_topic,
)


def _make_lemma(db, lemma_id, domain, bare="word"):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=bare,
        lemma_ar_bare=bare,
        pos="noun",
        thematic_domain=domain,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _make_encountered(db, lemma_id):
    ulk = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state="encountered",
        times_seen=0,
        times_correct=0,
        total_encounters=1,
    )
    db.add(ulk)
    db.flush()
    return ulk


class TestGetSettings:
    def test_creates_singleton(self, db_session):
        s = get_settings(db_session)
        assert s.id == 1
        assert s.active_topic is None

    def test_returns_existing(self, db_session):
        s1 = get_settings(db_session)
        s1.active_topic = "food"
        db_session.flush()

        s2 = get_settings(db_session)
        assert s2.active_topic == "food"


class TestGetAvailableTopics:
    def test_empty_db(self, db_session):
        topics = get_available_topics(db_session)
        assert len(topics) == len(DOMAINS)
        assert all(t["available_words"] == 0 for t in topics)

    def test_counts_available(self, db_session):
        for i in range(10):
            _make_lemma(db_session, 100 + i, "food", f"food_{i}")
        for i in range(3):
            _make_lemma(db_session, 200 + i, "school", f"school_{i}")

        topics = get_available_topics(db_session)
        food = next(t for t in topics if t["domain"] == "food")
        school = next(t for t in topics if t["domain"] == "school")

        assert food["available_words"] == 10
        assert food["eligible"] is True
        assert school["available_words"] == 3
        assert school["eligible"] is False

    def test_excludes_introduced(self, db_session):
        for i in range(8):
            _make_lemma(db_session, 100 + i, "food", f"food_{i}")
        # Mark 3 as acquiring (introduced)
        for i in range(3):
            ulk = UserLemmaKnowledge(
                lemma_id=100 + i,
                knowledge_state="acquiring",
                times_seen=0, times_correct=0, total_encounters=0,
            )
            db_session.add(ulk)
        db_session.flush()

        topics = get_available_topics(db_session)
        food = next(t for t in topics if t["domain"] == "food")
        assert food["available_words"] == 5  # 8 - 3
        assert food["learned_words"] == 3

    def test_encountered_counts_as_available(self, db_session):
        for i in range(6):
            _make_lemma(db_session, 100 + i, "food", f"food_{i}")
            _make_encountered(db_session, 100 + i)

        topics = get_available_topics(db_session)
        food = next(t for t in topics if t["domain"] == "food")
        assert food["available_words"] == 6
        assert food["eligible"] is True

    def test_sorted_by_available(self, db_session):
        for i in range(20):
            _make_lemma(db_session, 100 + i, "food", f"food_{i}")
        for i in range(10):
            _make_lemma(db_session, 200 + i, "school", f"school_{i}")
        for i in range(2):
            _make_lemma(db_session, 300 + i, "travel", f"travel_{i}")

        topics = get_available_topics(db_session)
        eligible = [t for t in topics if t["eligible"]]
        assert eligible[0]["domain"] == "food"
        assert eligible[1]["domain"] == "school"


class TestSelectBestTopic:
    def test_picks_highest_available(self, db_session):
        for i in range(20):
            _make_lemma(db_session, 100 + i, "nature", f"nature_{i}")
        for i in range(10):
            _make_lemma(db_session, 200 + i, "food", f"food_{i}")

        result = select_best_topic(db_session)
        assert result == "nature"

    def test_excludes_current(self, db_session):
        for i in range(20):
            _make_lemma(db_session, 100 + i, "nature", f"nature_{i}")
        for i in range(10):
            _make_lemma(db_session, 200 + i, "food", f"food_{i}")

        settings = get_settings(db_session)
        settings.active_topic = "nature"
        db_session.flush()

        result = select_best_topic(db_session, exclude_current=True)
        assert result == "food"

    def test_returns_none_when_no_eligible(self, db_session):
        for i in range(2):
            _make_lemma(db_session, 100 + i, "food", f"food_{i}")

        result = select_best_topic(db_session)
        assert result is None


class TestEnsureActiveTopic:
    def test_auto_selects_when_none(self, db_session):
        for i in range(10):
            _make_lemma(db_session, 100 + i, "food", f"food_{i}")

        topic = ensure_active_topic(db_session)
        assert topic == "food"

        settings = get_settings(db_session)
        assert settings.active_topic == "food"
        assert settings.topic_started_at is not None

    def test_keeps_existing(self, db_session):
        for i in range(10):
            _make_lemma(db_session, 100 + i, "food", f"food_{i}")
        for i in range(20):
            _make_lemma(db_session, 200 + i, "school", f"school_{i}")

        settings = get_settings(db_session)
        settings.active_topic = "food"
        settings.words_introduced_in_topic = 3
        settings.topic_started_at = datetime.now(timezone.utc)
        db_session.flush()

        topic = ensure_active_topic(db_session)
        assert topic == "food"

    def test_advances_when_batch_exhausted(self, db_session):
        for i in range(10):
            _make_lemma(db_session, 100 + i, "food", f"food_{i}")
        for i in range(20):
            _make_lemma(db_session, 200 + i, "school", f"school_{i}")

        settings = get_settings(db_session)
        settings.active_topic = "food"
        settings.words_introduced_in_topic = MAX_TOPIC_BATCH
        settings.topic_started_at = datetime.now(timezone.utc)
        db_session.flush()

        topic = ensure_active_topic(db_session)
        assert topic == "school"

    def test_advances_when_topic_depleted(self, db_session):
        for i in range(6):
            _make_lemma(db_session, 100 + i, "food", f"food_{i}")
            # Mark all as acquiring
            db_session.add(UserLemmaKnowledge(
                lemma_id=100 + i, knowledge_state="acquiring",
                times_seen=0, times_correct=0, total_encounters=0,
            ))
        for i in range(10):
            _make_lemma(db_session, 200 + i, "school", f"school_{i}")
        db_session.flush()

        settings = get_settings(db_session)
        settings.active_topic = "food"
        settings.words_introduced_in_topic = 3
        settings.topic_started_at = datetime.now(timezone.utc)
        db_session.flush()

        topic = ensure_active_topic(db_session)
        assert topic == "school"

    def test_archives_history(self, db_session):
        for i in range(10):
            _make_lemma(db_session, 100 + i, "food", f"food_{i}")
        for i in range(20):
            _make_lemma(db_session, 200 + i, "school", f"school_{i}")

        settings = get_settings(db_session)
        settings.active_topic = "food"
        settings.words_introduced_in_topic = MAX_TOPIC_BATCH
        settings.topic_started_at = datetime.now(timezone.utc)
        db_session.flush()

        ensure_active_topic(db_session)

        settings = get_settings(db_session)
        assert settings.active_topic == "school"
        assert len(settings.topic_history_json) == 1
        assert settings.topic_history_json[0]["topic"] == "food"


class TestRecordIntroduction:
    def test_increments(self, db_session):
        settings = get_settings(db_session)
        settings.words_introduced_in_topic = 5
        db_session.flush()

        record_introduction(db_session, count=2)

        settings = get_settings(db_session)
        assert settings.words_introduced_in_topic == 7


class TestSetTopic:
    def test_sets_topic(self, db_session):
        settings = set_topic(db_session, "food")
        assert settings.active_topic == "food"
        assert settings.words_introduced_in_topic == 0

    def test_invalid_domain_raises(self, db_session):
        with pytest.raises(ValueError):
            set_topic(db_session, "nonexistent")

    def test_archives_old_topic(self, db_session):
        for i in range(10):
            _make_lemma(db_session, 100 + i, "food", f"food_{i}")

        settings = get_settings(db_session)
        settings.active_topic = "school"
        settings.words_introduced_in_topic = 5
        settings.topic_started_at = datetime.now(timezone.utc)
        db_session.flush()

        set_topic(db_session, "food")

        settings = get_settings(db_session)
        assert settings.active_topic == "food"
        assert settings.topic_history_json is not None
        assert settings.topic_history_json[-1]["topic"] == "school"
