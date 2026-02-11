"""Tests for sentence-centric session assembly."""

from datetime import datetime, timezone, timedelta

import pytest

from app.models import Lemma, ReviewLog, UserLemmaKnowledge, Sentence, SentenceWord
from app.services.fsrs_service import create_new_card
from app.services.sentence_selector import (
    FRESHNESS_BASELINE,
    WordMeta,
    _difficulty_match_quality,
    _get_intro_candidates,
    _scaffold_freshness,
    build_session,
)


def _make_card(stability_days=30.0, due_offset_hours=-1):
    card = create_new_card()
    card["stability"] = stability_days
    due = datetime.now(timezone.utc) + timedelta(hours=due_offset_hours)
    card["due"] = due.isoformat()
    return card


def _seed_word(db, lemma_id, arabic, english, state="known",
               stability=30.0, due_hours=-1):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        pos="noun",
        gloss_en=english,
    )
    db.add(lemma)
    db.flush()

    knowledge = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        fsrs_card_json=_make_card(stability, due_hours),
        introduced_at=datetime.now(timezone.utc) - timedelta(days=30),
        last_reviewed=datetime.now(timezone.utc) - timedelta(hours=1),
        times_seen=10,
        times_correct=8,
        source="study",
    )
    db.add(knowledge)
    db.flush()
    return lemma, knowledge


def _seed_sentence(db, sentence_id, arabic, english, target_lemma_id, word_surfaces_and_ids):
    sent = Sentence(
        id=sentence_id,
        arabic_text=arabic,
        arabic_diacritized=arabic,
        english_translation=english,
        target_lemma_id=target_lemma_id,
    )
    db.add(sent)
    db.flush()

    for pos, (surface, lid) in enumerate(word_surfaces_and_ids):
        sw = SentenceWord(
            sentence_id=sentence_id,
            position=pos,
            surface_form=surface,
            lemma_id=lid,
        )
        db.add(sw)
    db.flush()
    return sent


class TestDifficultyMatchQuality:
    def test_no_scaffold_words(self):
        assert _difficulty_match_quality(0.5, []) == 1.0

    def test_very_fragile_word_stable_scaffold(self):
        assert _difficulty_match_quality(0.2, [2.0, 5.0]) == 1.0

    def test_very_fragile_word_fragile_scaffold(self):
        assert _difficulty_match_quality(0.2, [0.3, 5.0]) == 0.3

    def test_shaky_word_stronger_scaffold(self):
        assert _difficulty_match_quality(1.5, [3.0, 5.0]) == 1.0

    def test_shaky_word_weaker_scaffold(self):
        assert _difficulty_match_quality(2.0, [0.5, 1.0]) == 0.5

    def test_strong_word_any_scaffold(self):
        assert _difficulty_match_quality(10.0, [1.0, 2.0]) == 1.0


class TestScaffoldFreshness:
    def _make_ulk(self, times_seen=0):
        ulk = UserLemmaKnowledge(
            lemma_id=1,
            knowledge_state="known",
            times_seen=times_seen,
            times_correct=times_seen,
        )
        return ulk

    def test_no_scaffold_returns_one(self):
        # All words are due or function words
        words = [
            WordMeta(lemma_id=1, surface_form="كتاب", gloss_en="book",
                     stability=1.0, is_due=True),
            WordMeta(lemma_id=None, surface_form="في", gloss_en=None,
                     stability=None, is_due=False, is_function_word=True),
        ]
        assert _scaffold_freshness(words, {}) == 1.0

    def test_low_exposure_no_penalty(self):
        words = [
            WordMeta(lemma_id=1, surface_form="كتاب", gloss_en="book",
                     stability=1.0, is_due=True),
            WordMeta(lemma_id=2, surface_form="ولد", gloss_en="boy",
                     stability=5.0, is_due=False),
        ]
        knowledge_map = {2: self._make_ulk(times_seen=3)}
        assert _scaffold_freshness(words, knowledge_map) == 1.0

    def test_high_exposure_penalized(self):
        words = [
            WordMeta(lemma_id=1, surface_form="كتاب", gloss_en="book",
                     stability=1.0, is_due=True),
            WordMeta(lemma_id=2, surface_form="جميلة", gloss_en="beautiful",
                     stability=30.0, is_due=False),
        ]
        knowledge_map = {2: self._make_ulk(times_seen=16)}
        result = _scaffold_freshness(words, knowledge_map)
        assert result < 1.0
        # 8/16 = 0.5 (single scaffold word, geo mean = 0.5)
        assert abs(result - 0.5) < 0.01

    def test_extreme_exposure_floored(self):
        words = [
            WordMeta(lemma_id=1, surface_form="كتاب", gloss_en="book",
                     stability=1.0, is_due=True),
            WordMeta(lemma_id=2, surface_form="جميلة", gloss_en="beautiful",
                     stability=30.0, is_due=False),
            WordMeta(lemma_id=3, surface_form="كبيرة", gloss_en="big",
                     stability=30.0, is_due=False),
        ]
        # Both seen 200+ times → penalty per word = 8/200 = 0.04
        # geo mean of 0.04, 0.04 = 0.04, floored to 0.3
        knowledge_map = {
            2: self._make_ulk(times_seen=200),
            3: self._make_ulk(times_seen=200),
        }
        result = _scaffold_freshness(words, knowledge_map)
        assert result == 0.3

    def test_function_words_excluded(self):
        words = [
            WordMeta(lemma_id=1, surface_form="كتاب", gloss_en="book",
                     stability=1.0, is_due=True),
            WordMeta(lemma_id=2, surface_form="في", gloss_en="in",
                     stability=30.0, is_due=False, is_function_word=True),
        ]
        knowledge_map = {2: self._make_ulk(times_seen=100)}
        # Function word excluded → no scaffold → 1.0
        assert _scaffold_freshness(words, knowledge_map) == 1.0

    def test_fresh_scaffold_beats_overexposed(self, db_session):
        """Integration: sentence with fresh scaffolds should score higher."""
        # Due word
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        # Fresh scaffold word (seen 2 times)
        _seed_word(db_session, 2, "ولد", "boy", due_hours=24, stability=5.0)
        db_session.query(UserLemmaKnowledge).filter_by(lemma_id=2).update({"times_seen": 2})
        # Overexposed scaffold word (seen 50 times)
        _seed_word(db_session, 3, "جميلة", "beautiful", due_hours=24, stability=30.0)
        db_session.query(UserLemmaKnowledge).filter_by(lemma_id=3).update({"times_seen": 50})

        # Sentence A: fresh scaffold
        _seed_sentence(db_session, 1, "الكتاب والولد", "the book and boy",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[("الكتاب", 1), ("والولد", 2)])
        # Sentence B: overexposed scaffold
        _seed_sentence(db_session, 2, "الكتاب جميلة", "the book beautiful",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[("الكتاب", 1), ("جميلة", 3)])
        db_session.commit()

        result = build_session(db_session, limit=1)
        assert len(result["items"]) == 1
        # Fresh scaffold sentence should be selected first
        assert result["items"][0]["sentence_id"] == 1


class TestGreedySetCover:
    def test_single_sentence_covers_word(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_word(db_session, 2, "ولد", "boy", due_hours=24)
        _seed_sentence(db_session, 1, "الولد قرأ الكتاب", "The boy read the book",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[("الولد", 2), ("قرأ", None), ("الكتاب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        assert result["total_due_words"] == 1
        assert result["covered_due_words"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["sentence_id"] == 1
        assert result["items"][0]["primary_lemma_id"] == 1

    def test_prefers_sentence_covering_more_due_words(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_word(db_session, 2, "ولد", "boy", due_hours=-1)
        _seed_word(db_session, 3, "قلم", "pen", due_hours=-1)

        # Sentence 1 covers 2 due words
        _seed_sentence(db_session, 1, "كتاب ولد", "book boy",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[("كتاب", 1), ("ولد", 2)])
        # Sentence 2 covers 1 due word
        _seed_sentence(db_session, 2, "قلم", "pen",
                       target_lemma_id=3,
                       word_surfaces_and_ids=[("قلم", 3)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        assert result["covered_due_words"] == 3
        assert len(result["items"]) == 2

    def test_skips_recently_shown_sentences(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)

        sent = _seed_sentence(db_session, 1, "الكتاب", "the book",
                              target_lemma_id=1,
                              word_surfaces_and_ids=[("الكتاب", 1)])
        sent.last_reading_shown_at = datetime.now(timezone.utc) - timedelta(days=2)
        db_session.commit()

        result = build_session(db_session, limit=10)
        # Sentence skipped (shown 2 days ago < 7 day cooldown), falls back to word-only
        assert len(result["items"]) == 1
        assert result["items"][0]["sentence_id"] is None

    def test_no_due_words_returns_empty(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=24)
        db_session.commit()

        result = build_session(db_session)
        assert result["items"] == []
        assert result["total_due_words"] == 0

    def test_fallback_word_only_items(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        # No sentences exist
        db_session.commit()

        result = build_session(db_session, limit=10)
        assert result["total_due_words"] == 1
        assert result["covered_due_words"] == 1
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["sentence_id"] is None
        assert item["primary_lemma_id"] == 1
        assert item["arabic_text"] == "كتاب"
        assert len(item["words"]) == 1
        assert item["words"][0]["is_due"] is True


class TestSessionOrdering:
    def test_easy_sentences_at_bookends(self, db_session):
        # Word 1: high stability (easy)
        _seed_word(db_session, 1, "كتاب", "book", stability=30.0, due_hours=-1)
        # Word 2: low stability (hard)
        _seed_word(db_session, 2, "ولد", "boy", stability=0.5, due_hours=-1)
        # Word 3: medium stability
        _seed_word(db_session, 3, "قلم", "pen", stability=5.0, due_hours=-1)
        # Word 4: high stability (easy)
        _seed_word(db_session, 4, "بيت", "house", stability=25.0, due_hours=-1)

        _seed_sentence(db_session, 1, "كتاب", "book", 1, [("كتاب", 1)])
        _seed_sentence(db_session, 2, "ولد", "boy", 2, [("ولد", 2)])
        _seed_sentence(db_session, 3, "قلم", "pen", 3, [("قلم", 3)])
        _seed_sentence(db_session, 4, "بيت", "house", 4, [("بيت", 4)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        items = result["items"]
        assert len(items) == 4

        # First item should be easy (high stability)
        first_lid = items[0]["primary_lemma_id"]
        last_lid = items[-1]["primary_lemma_id"]
        # The hardest word (lid=2, stability=0.5) should NOT be first or last
        assert first_lid != 2
        assert last_lid != 2


class TestWordMetadata:
    def test_word_metas_include_surface_form(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_sentence(db_session, 1, "في الكتاب", "in the book",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[("في", None), ("الكتاب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        words = result["items"][0]["words"]
        assert len(words) == 2
        assert words[0]["surface_form"] == "في"
        assert words[0]["is_function_word"] is True
        assert words[1]["surface_form"] == "الكتاب"
        assert words[1]["is_due"] is True

    def test_backfills_function_word_lemma_when_available(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_word(db_session, 2, "في", "in", due_hours=24)
        _seed_sentence(db_session, 1, "في الكتاب", "in the book",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[("في", None), ("الكتاب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        words = result["items"][0]["words"]
        assert words[0]["surface_form"] == "في"
        assert words[0]["is_function_word"] is True
        assert words[0]["lemma_id"] == 2
        sw = (
            db_session.query(SentenceWord)
            .filter(SentenceWord.sentence_id == 1, SentenceWord.position == 0)
            .first()
        )
        assert sw is not None
        assert sw.lemma_id == 2

    def test_session_id_generated(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_sentence(db_session, 1, "الكتاب", "the book", 1, [("الكتاب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        assert "session_id" in result
        assert len(result["session_id"]) == 36  # full UUID format


class TestIntroCandidates:
    def test_returns_intro_candidates_key(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_sentence(db_session, 1, "الكتاب", "the book", 1, [("الكتاب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        assert "intro_candidates" in result

    def test_no_intros_with_few_items(self, db_session):
        """Don't suggest intros if session has fewer than MIN_ITEMS_FOR_INTRO items."""
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_sentence(db_session, 1, "الكتاب", "the book", 1, [("الكتاب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        # Only 1 item, below threshold
        assert result["intro_candidates"] == []

    def test_intros_when_enough_items_and_candidates(self, db_session):
        """Suggests intros when session has enough items and there are unlearned words."""
        # Create 5 due words with sentences
        for i in range(1, 6):
            _seed_word(db_session, i, f"word{i}", f"meaning{i}", due_hours=-1)
            _seed_sentence(db_session, i, f"word{i}", f"meaning{i}", i,
                          [(f"word{i}", i)])

        # Create unlearned candidate words (no UserLemmaKnowledge)
        for i in range(10, 13):
            lemma = Lemma(lemma_id=i, lemma_ar=f"new{i}", lemma_ar_bare=f"new{i}",
                         pos="noun", gloss_en=f"newmeaning{i}", frequency_rank=i)
            db_session.add(lemma)

        # Add some review history with good accuracy
        now = datetime.now(timezone.utc)
        for j in range(10):
            db_session.add(ReviewLog(
                lemma_id=1, rating=3, reviewed_at=now,
                review_mode="reading",
            ))

        db_session.commit()

        result = build_session(db_session, limit=10)
        assert len(result["items"]) >= 4
        # Should have intro candidates
        assert len(result["intro_candidates"]) <= 2

    def test_no_intros_with_low_accuracy(self, db_session):
        """Don't suggest intros if recent accuracy is below threshold."""
        for i in range(1, 6):
            _seed_word(db_session, i, f"word{i}", f"meaning{i}", due_hours=-1)
            _seed_sentence(db_session, i, f"word{i}", f"meaning{i}", i,
                          [(f"word{i}", i)])

        # Create unlearned candidate
        lemma = Lemma(lemma_id=10, lemma_ar="new10", lemma_ar_bare="new10",
                     pos="noun", gloss_en="newmeaning", frequency_rank=10)
        db_session.add(lemma)

        # Add review history with low accuracy (all rating=1)
        now = datetime.now(timezone.utc)
        for j in range(10):
            db_session.add(ReviewLog(
                lemma_id=1, rating=1, reviewed_at=now,
                review_mode="reading",
            ))

        db_session.commit()

        result = build_session(db_session, limit=10)
        assert result["intro_candidates"] == []
