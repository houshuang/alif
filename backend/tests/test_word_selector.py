from datetime import datetime, timezone, timedelta
import pytest

from app.models import Root, Lemma, UserLemmaKnowledge, GrammarFeature, UserGrammarExposure
from app.services.word_selector import (
    select_next_words,
    introduce_word,
    get_root_family,
    get_sentence_difficulty_params,
    _frequency_score,
    _root_familiarity_score,
    _root_familiarity_score_batch,
    _days_since_introduced,
    _days_since_introduced_batch,
    _grammar_pattern_score_batch,
    _is_noise_lemma,
)
from app.services.grammar_service import grammar_pattern_score


def _create_root(db, root_text, meaning="test"):
    root = Root(root=root_text, core_meaning_en=meaning)
    db.add(root)
    db.flush()
    return root


def _create_lemma(db, arabic, english, root=None, freq=None):
    lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        gloss_en=english,
        root_id=root.root_id if root else None,
        frequency_rank=freq,
        pos="noun",
    )
    db.add(lemma)
    db.flush()
    return lemma


def _mark_known(db, lemma_id, state="known"):
    from app.services.fsrs_service import create_new_card
    k = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        fsrs_card_json=create_new_card(),
        last_reviewed=datetime.now(timezone.utc),
        introduced_at=datetime.now(timezone.utc),
        times_seen=5,
        times_correct=4,
    )
    db.add(k)
    db.flush()
    return k


class TestFrequencyScore:
    def test_high_frequency(self):
        assert _frequency_score(1) > _frequency_score(1000)

    def test_very_high_frequency(self):
        assert _frequency_score(10) > 0.2

    def test_unknown_frequency(self):
        assert _frequency_score(None) == 0.3

    def test_zero_rank(self):
        score = _frequency_score(0)
        assert score > 0


class TestRootFamiliarity:
    def test_no_root(self, db_session):
        score, known, total = _root_familiarity_score(db_session, None)
        assert score == 0.0

    def test_unknown_root(self, db_session):
        root = _create_root(db_session, "ك.ت.ب", "writing")
        _create_lemma(db_session, "كتاب", "book", root)
        _create_lemma(db_session, "مكتبة", "library", root)
        db_session.commit()

        score, known, total = _root_familiarity_score(db_session, root.root_id)
        assert score == 0.0
        assert known == 0

    def test_partially_known_root(self, db_session):
        root = _create_root(db_session, "ك.ت.ب", "writing")
        l1 = _create_lemma(db_session, "كتاب", "book", root)
        _create_lemma(db_session, "مكتبة", "library", root)
        _create_lemma(db_session, "كاتب", "writer", root)
        _mark_known(db_session, l1.lemma_id)
        db_session.commit()

        score, known, total = _root_familiarity_score(db_session, root.root_id)
        assert score > 0
        assert known == 1
        assert total == 3

    def test_fully_known_root_low_score(self, db_session):
        root = _create_root(db_session, "ك.ت.ب", "writing")
        l1 = _create_lemma(db_session, "كتاب", "book", root)
        l2 = _create_lemma(db_session, "مكتبة", "library", root)
        _mark_known(db_session, l1.lemma_id)
        _mark_known(db_session, l2.lemma_id)
        db_session.commit()

        score, known, total = _root_familiarity_score(db_session, root.root_id)
        assert score == 0.1  # fully known = low priority


class TestNoiseFilter:
    def test_alternative_form_filtered(self, db_session):
        l = _create_lemma(db_session, "test", "alternative form of X", freq=10)
        db_session.commit()
        assert _is_noise_lemma(l) is True

    def test_active_participle_filtered(self, db_session):
        l = _create_lemma(db_session, "test", "Active participle of Y", freq=10)
        db_session.commit()
        assert _is_noise_lemma(l) is True

    def test_judeo_arabic_filtered(self, db_session):
        l = _create_lemma(db_session, "test", "Judeo-Arabic spelling of Z", freq=10)
        db_session.commit()
        assert _is_noise_lemma(l) is True

    def test_non_arabic_script_filtered(self, db_session):
        l = Lemma(lemma_ar="גלם", lemma_ar_bare="גלם", gloss_en="test", pos="noun", frequency_rank=10)
        db_session.add(l)
        db_session.flush()
        assert _is_noise_lemma(l) is True

    def test_normal_word_not_filtered(self, db_session):
        l = _create_lemma(db_session, "كتاب", "book", freq=10)
        db_session.commit()
        assert _is_noise_lemma(l) is False

    def test_noise_excluded_from_candidates(self, db_session):
        _create_lemma(db_session, "كتاب", "book", freq=10)
        _create_lemma(db_session, "test", "alternative form of X", freq=5)
        db_session.commit()
        result = select_next_words(db_session, count=5)
        assert len(result) == 1
        assert result[0]["gloss_en"] == "book"


class TestSelectNextWords:
    def test_empty_db(self, db_session):
        result = select_next_words(db_session)
        assert result == []

    def test_selects_unlearned_words(self, db_session):
        root = _create_root(db_session, "ك.ت.ب")
        l1 = _create_lemma(db_session, "كتاب", "book", root, freq=100)
        l2 = _create_lemma(db_session, "مكتبة", "library", root, freq=500)
        l3 = _create_lemma(db_session, "كاتب", "writer", root, freq=300)
        db_session.commit()

        result = select_next_words(db_session, count=3)
        assert len(result) == 3
        ids = [w["lemma_id"] for w in result]
        assert l1.lemma_id in ids  # highest frequency

    def test_excludes_already_known(self, db_session):
        root = _create_root(db_session, "ك.ت.ب")
        l1 = _create_lemma(db_session, "كتاب", "book", root, freq=100)
        l2 = _create_lemma(db_session, "مكتبة", "library", root, freq=500)
        _mark_known(db_session, l1.lemma_id)
        db_session.commit()

        result = select_next_words(db_session, count=3)
        assert len(result) == 1
        assert result[0]["lemma_id"] == l2.lemma_id

    def test_frequency_ordering(self, db_session):
        l1 = _create_lemma(db_session, "بيت", "house", freq=10)
        l2 = _create_lemma(db_session, "سيارة", "car", freq=5000)
        l3 = _create_lemma(db_session, "قلم", "pen", freq=100)
        db_session.commit()

        result = select_next_words(db_session, count=3)
        assert result[0]["lemma_id"] == l1.lemma_id  # freq 10 > freq 100 > freq 5000

    def test_root_familiarity_boosts_score(self, db_session):
        root = _create_root(db_session, "ك.ت.ب")
        l1 = _create_lemma(db_session, "كتاب", "book", root, freq=100)
        l2 = _create_lemma(db_session, "مكتبة", "library", root, freq=5000)
        _mark_known(db_session, l1.lemma_id)

        no_root = _create_lemma(db_session, "بيت", "house", freq=5000)
        db_session.commit()

        result = select_next_words(db_session, count=2)
        # مكتبة should rank higher than بيت despite same freq, because root is known
        ids = [w["lemma_id"] for w in result]
        assert ids[0] == l2.lemma_id

    def test_exclude_ids(self, db_session):
        l1 = _create_lemma(db_session, "بيت", "house", freq=10)
        l2 = _create_lemma(db_session, "قلم", "pen", freq=20)
        db_session.commit()

        result = select_next_words(db_session, count=2, exclude_lemma_ids=[l1.lemma_id])
        assert len(result) == 1
        assert result[0]["lemma_id"] == l2.lemma_id


class TestIntroduceWord:
    def test_basic_introduction(self, db_session):
        lemma = _create_lemma(db_session, "كتاب", "book", freq=100)
        db_session.commit()

        result = introduce_word(db_session, lemma.lemma_id)
        assert result["state"] == "acquiring"
        assert result["already_known"] is False
        assert result["lemma_ar"] == "كتاب"

        knowledge = (
            db_session.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == lemma.lemma_id)
            .first()
        )
        assert knowledge is not None
        assert knowledge.knowledge_state == "acquiring"
        assert knowledge.acquisition_box == 1
        assert knowledge.introduced_at is not None

    def test_already_known(self, db_session):
        lemma = _create_lemma(db_session, "كتاب", "book", freq=100)
        _mark_known(db_session, lemma.lemma_id)
        db_session.commit()

        result = introduce_word(db_session, lemma.lemma_id)
        assert result["already_known"] is True

    def test_with_root_family(self, db_session):
        root = _create_root(db_session, "ك.ت.ب", "writing")
        l1 = _create_lemma(db_session, "كتاب", "book", root, freq=100)
        l2 = _create_lemma(db_session, "مكتبة", "library", root, freq=500)
        _mark_known(db_session, l1.lemma_id)
        db_session.commit()

        result = introduce_word(db_session, l2.lemma_id)
        assert result["root"] == "ك.ت.ب"
        assert result["root_meaning"] == "writing"
        assert len(result["root_family"]) == 2

    def test_not_found(self, db_session):
        with pytest.raises(ValueError):
            introduce_word(db_session, 99999)


class TestGetRootFamily:
    def test_returns_all_siblings(self, db_session):
        root = _create_root(db_session, "ك.ت.ب", "writing")
        _create_lemma(db_session, "كتاب", "book", root, freq=100)
        _create_lemma(db_session, "مكتبة", "library", root, freq=500)
        _create_lemma(db_session, "كاتب", "writer", root, freq=300)
        db_session.commit()

        family = get_root_family(db_session, root.root_id)
        assert len(family) == 3


class TestSentenceDifficultyParams:
    def test_brand_new_word(self, db_session):
        lemma = _create_lemma(db_session, "كتاب", "book", freq=100)
        db_session.commit()

        params = get_sentence_difficulty_params(db_session, lemma.lemma_id)
        assert params["max_words"] == 7
        assert params["use_only_top_known"] is True

    def test_just_introduced(self, db_session):
        lemma = _create_lemma(db_session, "كتاب", "book", freq=100)
        db_session.flush()
        k = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="learning",
            fsrs_card_json={},
            introduced_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            times_seen=2,
        )
        db_session.add(k)
        db_session.commit()

        params = get_sentence_difficulty_params(db_session, lemma.lemma_id)
        assert params["max_words"] == 7
        assert "simple" in params["difficulty_hint"]

    def test_same_day(self, db_session):
        lemma = _create_lemma(db_session, "كتاب", "book", freq=100)
        db_session.flush()
        k = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="learning",
            fsrs_card_json={},
            introduced_at=datetime.now(timezone.utc) - timedelta(hours=6),
            times_seen=4,
        )
        db_session.add(k)
        db_session.commit()

        params = get_sentence_difficulty_params(db_session, lemma.lemma_id)
        assert params["max_words"] == 9

    def test_first_week(self, db_session):
        lemma = _create_lemma(db_session, "كتاب", "book", freq=100)
        db_session.flush()
        k = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="learning",
            fsrs_card_json={},
            introduced_at=datetime.now(timezone.utc) - timedelta(days=3),
            times_seen=7,
        )
        db_session.add(k)
        db_session.commit()

        params = get_sentence_difficulty_params(db_session, lemma.lemma_id)
        assert params["max_words"] == 11

    def test_well_known(self, db_session):
        lemma = _create_lemma(db_session, "كتاب", "book", freq=100)
        db_session.flush()
        k = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="known",
            fsrs_card_json={},
            introduced_at=datetime.now(timezone.utc) - timedelta(days=30),
            times_seen=20,
        )
        db_session.add(k)
        db_session.commit()

        params = get_sentence_difficulty_params(db_session, lemma.lemma_id)
        assert params["max_words"] == 14


class TestLearnAPI:
    def test_next_words_endpoint(self, client, db_session):
        _create_lemma(db_session, "بيت", "house", freq=10)
        _create_lemma(db_session, "قلم", "pen", freq=20)
        db_session.commit()

        resp = client.get("/api/learn/next-words?count=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert len(data["words"]) == 2

    def test_next_words_includes_grammar_details(self, client, db_session):
        lemma = _create_lemma(db_session, "يَكْتُبُ", "he writes", freq=10)
        lemma.grammar_features_json = ["present"]
        db_session.commit()

        resp = client.get("/api/learn/next-words?count=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        word = data["words"][0]
        assert "grammar_details" in word
        assert any(g["feature_key"] == "present" for g in word["grammar_details"])

    def test_introduce_endpoint(self, client, db_session):
        lemma = _create_lemma(db_session, "كتاب", "book", freq=100)
        db_session.commit()

        resp = client.post(
            "/api/learn/introduce",
            json={"lemma_id": lemma.lemma_id},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "acquiring"
        assert data["already_known"] is False

    def test_introduce_batch_endpoint(self, client, db_session):
        l1 = _create_lemma(db_session, "بيت", "house", freq=10)
        l2 = _create_lemma(db_session, "قلم", "pen", freq=20)
        db_session.commit()

        resp = client.post(
            "/api/learn/introduce-batch",
            json={"lemma_ids": [l1.lemma_id, l2.lemma_id]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

    def test_quiz_result_endpoint(self, client, db_session):
        lemma = _create_lemma(db_session, "كتاب", "book", freq=100)
        _mark_known(db_session, lemma.lemma_id, state="learning")
        db_session.commit()

        # Got it → rating 3
        resp = client.post(
            "/api/learn/quiz-result",
            json={"lemma_id": lemma.lemma_id, "got_it": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lemma_id"] == lemma.lemma_id
        assert "new_state" in data
        assert "next_due" in data

    def test_quiz_result_missed(self, client, db_session):
        lemma = _create_lemma(db_session, "بيت", "house", freq=50)
        _mark_known(db_session, lemma.lemma_id, state="learning")
        db_session.commit()

        # Missed → rating 1
        resp = client.post(
            "/api/learn/quiz-result",
            json={"lemma_id": lemma.lemma_id, "got_it": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lemma_id"] == lemma.lemma_id

    def test_quiz_result_no_card(self, client, db_session):
        lemma = _create_lemma(db_session, "قلم", "pen", freq=200)
        db_session.commit()

        resp = client.post(
            "/api/learn/quiz-result",
            json={"lemma_id": lemma.lemma_id, "got_it": True},
        )
        assert resp.status_code == 404

    def test_sentence_params_endpoint(self, client, db_session):
        lemma = _create_lemma(db_session, "كتاب", "book", freq=100)
        db_session.commit()

        resp = client.get(f"/api/learn/sentence-params/{lemma.lemma_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "max_words" in data
        assert "difficulty_hint" in data


def _create_grammar_feature(db, key, category="test"):
    feat = GrammarFeature(
        feature_key=key, label_en=key, label_ar=key,
        category=category, sort_order=1, form_change_type="structural",
    )
    db.add(feat)
    db.flush()
    return feat


def _create_grammar_exposure(db, feature_id, times_seen=5, times_correct=3):
    exp = UserGrammarExposure(
        feature_id=feature_id,
        times_seen=times_seen,
        times_correct=times_correct,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(exp)
    db.flush()
    return exp


class TestBatchScoringMatchesPerItem:
    """Verify batch helpers produce the same results as per-item DB functions."""

    def test_root_familiarity_matches(self, db_session):
        from sqlalchemy import func as sa_func
        root1 = _create_root(db_session, "ك.ت.ب", "writing")
        root2 = _create_root(db_session, "د.ر.س", "studying")
        root3 = _create_root(db_session, "ع.ل.م", "knowledge")

        l1 = _create_lemma(db_session, "كتاب", "book", root1)
        l2 = _create_lemma(db_session, "مكتبة", "library", root1)
        l3 = _create_lemma(db_session, "كاتب", "writer", root1)
        _mark_known(db_session, l1.lemma_id)

        l4 = _create_lemma(db_session, "درس", "lesson", root2)
        l5 = _create_lemma(db_session, "مدرسة", "school", root2)

        l6 = _create_lemma(db_session, "عالم", "scholar", root3)
        l7 = _create_lemma(db_session, "علم", "knowledge", root3)
        _mark_known(db_session, l6.lemma_id)
        _mark_known(db_session, l7.lemma_id)
        db_session.commit()

        root_ids = {root1.root_id, root2.root_id, root3.root_id}

        root_total_counts = dict(
            db_session.query(Lemma.root_id, sa_func.count(Lemma.lemma_id))
            .filter(Lemma.root_id.in_(root_ids))
            .group_by(Lemma.root_id)
            .all()
        )
        root_known_counts = dict(
            db_session.query(Lemma.root_id, sa_func.count(UserLemmaKnowledge.id))
            .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
            .filter(
                Lemma.root_id.in_(root_ids),
                UserLemmaKnowledge.knowledge_state.in_(["known", "learning", "acquiring", "lapsed"]),
            )
            .group_by(Lemma.root_id)
            .all()
        )

        for rid in root_ids:
            per_item = _root_familiarity_score(db_session, rid)
            batch = _root_familiarity_score_batch(rid, root_total_counts, root_known_counts)
            assert abs(per_item[0] - batch[0]) < 1e-9, f"root {rid}: {per_item} vs {batch}"
            assert per_item[1] == batch[1]
            assert per_item[2] == batch[2]

    def test_days_since_introduced_matches(self, db_session):
        from sqlalchemy import func as sa_func
        root = _create_root(db_session, "ك.ت.ب", "writing")
        l1 = _create_lemma(db_session, "كتاب", "book", root)
        _mark_known(db_session, l1.lemma_id)
        db_session.commit()

        root_ids = {root.root_id}
        root_latest_intro = dict(
            db_session.query(Lemma.root_id, sa_func.max(UserLemmaKnowledge.introduced_at))
            .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
            .filter(Lemma.root_id.in_(root_ids))
            .group_by(Lemma.root_id)
            .all()
        )

        now = datetime.now(timezone.utc)
        per_item = _days_since_introduced(db_session, root.root_id)
        batch = _days_since_introduced_batch(root.root_id, root_latest_intro, now)
        assert abs(per_item - batch) < 0.01  # within ~15 minutes tolerance

    def test_grammar_score_matches(self, db_session):
        feat1 = _create_grammar_feature(db_session, "present", "verb_tense")
        feat2 = _create_grammar_feature(db_session, "past", "verb_tense")
        _create_grammar_exposure(db_session, feat1.feature_id, times_seen=10, times_correct=7)
        db_session.commit()

        features = ["present", "past"]

        per_item = grammar_pattern_score(db_session, features)

        from app.services.grammar_service import get_unlocked_features
        unlocked_info = get_unlocked_features(db_session)
        unlocked_set = set(unlocked_info["unlocked_features"])

        rows = (
            db_session.query(GrammarFeature.feature_key, UserGrammarExposure)
            .join(UserGrammarExposure, UserGrammarExposure.feature_id == GrammarFeature.feature_id)
            .filter(GrammarFeature.feature_key.in_(features))
            .all()
        )
        exposure_map = {key: exp for key, exp in rows}

        batch = _grammar_pattern_score_batch(features, unlocked_set, exposure_map)
        assert abs(per_item - batch) < 1e-9


class TestBatchScoringEdgeCases:
    """Test batch helpers with None/missing values."""

    def test_no_root(self):
        score, known, total = _root_familiarity_score_batch(None, {}, {})
        assert score == 0.0
        assert known == 0
        assert total == 0

    def test_days_since_no_root(self):
        now = datetime.now(timezone.utc)
        days = _days_since_introduced_batch(None, {}, now)
        assert days == 999.0

    def test_days_since_unknown_root(self):
        now = datetime.now(timezone.utc)
        days = _days_since_introduced_batch(42, {}, now)
        assert days == 999.0

    def test_grammar_no_features(self):
        score = _grammar_pattern_score_batch(None, set(), {})
        assert score == 0.1

    def test_grammar_empty_list(self):
        score = _grammar_pattern_score_batch([], set(), {})
        assert score == 0.1

    def test_grammar_no_unlocked(self):
        score = _grammar_pattern_score_batch(["present"], set(), {})
        assert score == 0.1

    def test_grammar_unlocked_no_exposure(self):
        score = _grammar_pattern_score_batch(["present"], {"present"}, {})
        assert score == 1.0


class TestBatchQueryCount:
    """Verify batch queries reduce total query count."""

    def test_query_count_scales_flat(self, db_session):
        from tests.conftest import count_queries

        # Arabic words so they pass the noise filter
        arabic_words = [
            "كتاب", "مكتبة", "كاتب", "درس", "مدرسة",
            "عالم", "علم", "بيت", "قلم", "سيارة",
            "ولد", "بنت", "رجل", "مرأة", "طفل",
            "شمس", "قمر", "ماء", "نار", "هواء",
            "يوم", "ليل", "صباح", "مساء", "وقت",
        ]

        roots = []
        for i in range(5):
            r = _create_root(db_session, f"ر.{i}.ت", f"meaning_{i}")
            roots.append(r)

        for i in range(25):
            root = roots[i % len(roots)]
            l = _create_lemma(db_session, arabic_words[i], f"gloss_{i}", root, freq=100 + i)
            if i < 5:
                _mark_known(db_session, l.lemma_id)
        db_session.commit()

        with count_queries(db_session) as counter:
            result = select_next_words(db_session, count=5)

        # With 20 candidates, old code would run 3-4 queries each = 60-80.
        # Batch code should be well under 20 total queries.
        assert counter["count"] < 20, f"Expected <20 queries, got {counter['count']}"
        assert len(result) == 5
