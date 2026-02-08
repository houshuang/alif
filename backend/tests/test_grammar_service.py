"""Tests for grammar feature tracking service."""

from datetime import datetime, timezone, timedelta

import pytest

from app.models import (
    GrammarFeature,
    UserGrammarExposure,
    Lemma,
    Root,
    UserLemmaKnowledge,
)
from app.services.grammar_service import (
    SEED_FEATURES,
    TIER_FEATURES,
    compute_comfort,
    seed_grammar_features,
    get_all_features,
    get_user_progress,
    get_unlocked_features,
    record_grammar_exposure,
    grammar_pattern_score,
)


class TestComputeComfort:
    def test_zero_for_unseen(self):
        assert compute_comfort(0, 0, None) == 0.0

    def test_positive_for_seen(self):
        now = datetime.now(timezone.utc)
        score = compute_comfort(10, 8, now)
        assert 0 < score <= 1.0

    def test_decays_over_time(self):
        now = datetime.now(timezone.utc)
        recent = compute_comfort(10, 8, now)
        old = compute_comfort(10, 8, now - timedelta(days=60))
        assert recent > old

    def test_higher_accuracy_means_higher_comfort(self):
        now = datetime.now(timezone.utc)
        high = compute_comfort(10, 10, now)
        low = compute_comfort(10, 2, now)
        assert high > low

    def test_capped_at_one(self):
        now = datetime.now(timezone.utc)
        score = compute_comfort(1000, 1000, now)
        assert score <= 1.0

    def test_more_exposure_means_higher_comfort(self):
        now = datetime.now(timezone.utc)
        many = compute_comfort(30, 24, now)
        few = compute_comfort(3, 2, now)
        assert many > few


class TestSeedGrammarFeatures:
    def test_seeds_all_features(self, db_session):
        count = seed_grammar_features(db_session)
        assert count == len(SEED_FEATURES)
        total = db_session.query(GrammarFeature).count()
        assert total == len(SEED_FEATURES)

    def test_idempotent(self, db_session):
        seed_grammar_features(db_session)
        second = seed_grammar_features(db_session)
        assert second == 0
        total = db_session.query(GrammarFeature).count()
        assert total == len(SEED_FEATURES)


class TestGetAllFeatures:
    def test_returns_all_seeded(self, db_session):
        seed_grammar_features(db_session)
        features = get_all_features(db_session)
        assert len(features) == len(SEED_FEATURES)
        keys = {f["feature_key"] for f in features}
        assert "singular" in keys
        assert "form_10" in keys

    def test_sorted_by_sort_order(self, db_session):
        seed_grammar_features(db_session)
        features = get_all_features(db_session)
        orders = [f["sort_order"] for f in features]
        assert orders == sorted(orders)


class TestGetUserProgress:
    def test_all_zeros_when_no_exposure(self, db_session):
        seed_grammar_features(db_session)
        progress = get_user_progress(db_session)
        assert len(progress) == len(SEED_FEATURES)
        assert all(p["times_seen"] == 0 for p in progress)
        assert all(p["comfort_score"] == 0.0 for p in progress)

    def test_reflects_exposure(self, db_session):
        seed_grammar_features(db_session)
        record_grammar_exposure(db_session, "singular", correct=True)
        record_grammar_exposure(db_session, "singular", correct=True)
        record_grammar_exposure(db_session, "singular", correct=False)

        progress = get_user_progress(db_session)
        singular = next(p for p in progress if p["feature_key"] == "singular")
        assert singular["times_seen"] == 3
        assert singular["times_correct"] == 2
        assert singular["comfort_score"] > 0


class TestGetUnlockedFeatures:
    def test_tier_0_always_unlocked(self, db_session):
        seed_grammar_features(db_session)
        result = get_unlocked_features(db_session)
        assert result["current_tier"] == 0
        for key in TIER_FEATURES[0]:
            assert key in result["unlocked_features"]

    def test_tier_1_requires_words(self, db_session):
        seed_grammar_features(db_session)

        # Add 10 known words
        root = Root(root="ك.ت.ب", core_meaning_en="writing")
        db_session.add(root)
        db_session.flush()
        for i in range(10):
            lemma = Lemma(
                lemma_ar=f"word{i}", lemma_ar_bare=f"word{i}",
                root_id=root.root_id, pos="noun", gloss_en=f"word {i}",
            )
            db_session.add(lemma)
            db_session.flush()
            db_session.add(UserLemmaKnowledge(
                lemma_id=lemma.lemma_id,
                knowledge_state="learning",
            ))
        db_session.commit()

        result = get_unlocked_features(db_session)
        assert result["current_tier"] >= 1
        assert "feminine" in result["unlocked_features"]

    def test_tier_2_requires_comfort(self, db_session):
        seed_grammar_features(db_session)

        # Add enough words for tier 1
        root = Root(root="ك.ت.ب", core_meaning_en="writing")
        db_session.add(root)
        db_session.flush()
        for i in range(10):
            lemma = Lemma(
                lemma_ar=f"w{i}", lemma_ar_bare=f"w{i}",
                root_id=root.root_id, pos="noun", gloss_en=f"w {i}",
            )
            db_session.add(lemma)
            db_session.flush()
            db_session.add(UserLemmaKnowledge(
                lemma_id=lemma.lemma_id, knowledge_state="learning",
            ))
        db_session.commit()

        # Without tier 1 comfort, tier 2 stays locked
        result = get_unlocked_features(db_session)
        assert "plural_sound" not in result["unlocked_features"]


class TestRecordGrammarExposure:
    def test_creates_new_record(self, db_session):
        seed_grammar_features(db_session)
        record_grammar_exposure(db_session, "past", correct=True)

        feature = db_session.query(GrammarFeature).filter_by(feature_key="past").first()
        exp = db_session.query(UserGrammarExposure).filter_by(feature_id=feature.feature_id).first()
        assert exp is not None
        assert exp.times_seen == 1
        assert exp.times_correct == 1

    def test_increments_existing(self, db_session):
        seed_grammar_features(db_session)
        record_grammar_exposure(db_session, "past", correct=True)
        record_grammar_exposure(db_session, "past", correct=False)

        feature = db_session.query(GrammarFeature).filter_by(feature_key="past").first()
        exp = db_session.query(UserGrammarExposure).filter_by(feature_id=feature.feature_id).first()
        assert exp.times_seen == 2
        assert exp.times_correct == 1

    def test_ignores_unknown_feature(self, db_session):
        seed_grammar_features(db_session)
        record_grammar_exposure(db_session, "nonexistent_feature", correct=True)
        assert db_session.query(UserGrammarExposure).count() == 0


class TestGrammarPatternScore:
    def test_base_score_for_none(self, db_session):
        seed_grammar_features(db_session)
        assert grammar_pattern_score(db_session, None) == 0.1

    def test_base_score_for_empty(self, db_session):
        seed_grammar_features(db_session)
        assert grammar_pattern_score(db_session, []) == 0.1

    def test_high_score_for_unseen_unlocked(self, db_session):
        seed_grammar_features(db_session)
        score = grammar_pattern_score(db_session, ["singular", "present"])
        assert score > 0.5

    def test_lower_score_after_practice(self, db_session):
        seed_grammar_features(db_session)
        before = grammar_pattern_score(db_session, ["singular"])

        for _ in range(15):
            record_grammar_exposure(db_session, "singular", correct=True)

        after = grammar_pattern_score(db_session, ["singular"])
        assert after < before


class TestGrammarAPI:
    def test_get_features(self, client):
        resp = client.get("/api/grammar/features")
        assert resp.status_code == 200
        data = resp.json()
        assert "features" in data
        assert len(data["features"]) == len(SEED_FEATURES)

    def test_get_progress(self, client):
        resp = client.get("/api/grammar/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert "progress" in data
        assert len(data["progress"]) == len(SEED_FEATURES)

    def test_get_unlocked(self, client):
        resp = client.get("/api/grammar/unlocked")
        assert resp.status_code == 200
        data = resp.json()
        assert "current_tier" in data
        assert data["current_tier"] == 0
        assert "singular" in data["unlocked_features"]
