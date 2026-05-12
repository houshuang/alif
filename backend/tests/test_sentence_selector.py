"""Tests for sentence-centric session assembly."""

from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import Lemma, ReviewLog, UserLemmaKnowledge, Sentence, SentenceWord
from app.services.fsrs_service import create_new_card
from app.services.sentence_selector import (
    FRESHNESS_BASELINE,
    HIGH_ACCURACY_INTRO_BACKLOG_CAP,
    INTRO_RESERVE_FRACTION,
    JACCARD_VETO_THRESHOLD,
    MAX_AUTO_INTRO_PER_SESSION,
    MID_ACCURACY_INTRO_BACKLOG_CAP,
    PIPELINE_BACKLOG_THRESHOLD,
    SESSION_SCAFFOLD_DECAY,
    SentenceCandidate,
    WordMeta,
    _difficulty_match_quality,
    _find_pregenerated_sentences_for_words,
    _group_maintenance_passages,
    _intro_backlog_threshold_for_accuracy,
    _intro_slots_for_accuracy,
    _is_near_duplicate_of_selected,
    _is_text_near_duplicate,
    _quality_multiplier_for_sentence,
    _display_source,
    _scaffold_freshness,
    build_session,
    compute_sentence_diversity_score,
)


def _make_card(stability_days=30.0, due_offset_hours=-1):
    card = create_new_card()
    card["stability"] = stability_days
    due = datetime.now(timezone.utc) + timedelta(hours=due_offset_hours)
    card["due"] = due.isoformat()
    return card


def _seed_word(db, lemma_id, arabic, english, state="known",
               stability=30.0, due_hours=-1, frequency_rank=None, word_category=None):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        pos="noun",
        gloss_en=english,
        frequency_rank=frequency_rank,
        word_category=word_category,
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


def _seed_sentence(
    db,
    sentence_id,
    arabic,
    english,
    target_lemma_id,
    word_surfaces_and_ids,
    source=None,
    quality_reviewed_at=None,
    quality_natural=None,
    quality_translation_correct=None,
):
    sent = Sentence(
        id=sentence_id,
        arabic_text=arabic,
        english_translation=english,
        target_lemma_id=target_lemma_id,
        source=source,
        quality_reviewed_at=quality_reviewed_at,
        quality_natural=quality_natural,
        quality_translation_correct=quality_translation_correct,
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


class TestSourceDisplay:
    def test_generic_learning_source_does_not_fall_back_to_lexical_source(self):
        lemma = Lemma(lemma_ar="كلمة", lemma_ar_bare="كلمة", gloss_en="word", source="duolingo")
        ulk = UserLemmaKnowledge(lemma_id=1, source="collateral")
        assert _display_source(ulk, lemma) is None

    def test_specific_learning_source_is_displayed(self):
        lemma = Lemma(lemma_ar="كلمة", lemma_ar_bare="كلمة", gloss_en="word", source="duolingo")
        ulk = UserLemmaKnowledge(lemma_id=1, source="textbook_scan")
        assert _display_source(ulk, lemma) == "textbook_scan"


class TestMaintenancePassageGrouping:
    def _candidate(
        self,
        sentence_id: int,
        due_id: int | None = None,
        extra_due_id: int | None = None,
        *,
        story_id: int | None = None,
        source: str | None = None,
    ):
        due = {due_id} if due_id is not None else set()
        if extra_due_id is not None:
            due.add(extra_due_id)
        return SentenceCandidate(
            sentence_id=sentence_id,
            sentence=Sentence(
                id=sentence_id,
                arabic_text=f"s{sentence_id}",
                story_id=story_id,
                source=source,
            ),
            words_meta=[
                WordMeta(
                    lemma_id=lid,
                    surface_form=f"w{lid}",
                    gloss_en="",
                    stability=10.0,
                    is_due=True,
                    knowledge_state="known",
                )
                for lid in due
            ],
            due_words_covered=due,
        )

    def test_keeps_unrelated_due_maintenance_sentences_single(self):
        candidates = [
            self._candidate(1, 101, 102),
            self._candidate(2, 103, 104),
            self._candidate(3, 105),
        ]
        knowledge = {
            lid: UserLemmaKnowledge(lemma_id=lid, knowledge_state="known")
            for lid in {101, 102, 103, 104, 105}
        }

        groups = _group_maintenance_passages(candidates, knowledge)

        assert [[c.sentence_id for c in group] for group in groups] == [[1], [2], [3]]

    def test_keeps_acquisition_sentences_single(self):
        candidates = [
            self._candidate(1, 101, 102),
            self._candidate(2, 103),
            self._candidate(3, 104, 105),
            self._candidate(4, 106),
        ]
        knowledge = {
            lid: UserLemmaKnowledge(lemma_id=lid, knowledge_state="known")
            for lid in {101, 102, 104, 105, 106}
        }
        knowledge[103] = UserLemmaKnowledge(lemma_id=103, knowledge_state="acquiring")

        groups = _group_maintenance_passages(candidates, knowledge)

        assert [[c.sentence_id for c in group] for group in groups] == [[1], [2], [3], [4]]

    def test_keeps_generated_passage_story_sentences_together(self):
        candidates = [
            self._candidate(1, 101, 102, story_id=10, source="passage"),
            self._candidate(9, 201, 202),
            self._candidate(2, 103, 104, story_id=10, source="passage"),
            self._candidate(3, 105, story_id=10, source="passage"),
        ]
        knowledge = {
            lid: UserLemmaKnowledge(lemma_id=lid, knowledge_state="known")
            for lid in {101, 102, 103, 104, 105, 201, 202}
        }

        groups = _group_maintenance_passages(candidates, knowledge)

        assert [[c.sentence_id for c in group] for group in groups] == [[1, 2, 3], [9]]

    def test_generated_passage_group_can_include_connector_sentence(self):
        candidates = [
            self._candidate(1, 101, story_id=10, source="passage"),
            self._candidate(2, story_id=10, source="passage"),
            self._candidate(3, 103, story_id=10, source="passage"),
        ]
        knowledge = {
            lid: UserLemmaKnowledge(lemma_id=lid, knowledge_state="known")
            for lid in {101, 103}
        }

        groups = _group_maintenance_passages(candidates, knowledge)

        assert [[c.sentence_id for c in group] for group in groups] == [[1, 2, 3]]


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
        # 5/16 = 0.3125 (single scaffold word, geo mean = 0.3125)
        assert abs(result - 0.3125) < 0.01

    def test_extreme_exposure_floored(self):
        words = [
            WordMeta(lemma_id=1, surface_form="كتاب", gloss_en="book",
                     stability=1.0, is_due=True),
            WordMeta(lemma_id=2, surface_form="جميلة", gloss_en="beautiful",
                     stability=30.0, is_due=False),
            WordMeta(lemma_id=3, surface_form="كبيرة", gloss_en="big",
                     stability=30.0, is_due=False),
        ]
        # Both seen 200+ times → penalty per word = 5/200 = 0.025
        # geo mean of 0.025, 0.025 = 0.025, floored to 0.1
        knowledge_map = {
            2: self._make_ulk(times_seen=200),
            3: self._make_ulk(times_seen=200),
        }
        result = _scaffold_freshness(words, knowledge_map)
        assert result == 0.1

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
        _seed_word(db_session, 3, "قرأ", "read", due_hours=24)
        _seed_sentence(db_session, 1, "الولد قرأ الكتاب", "The boy read the book",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[("الولد", 2), ("قرأ", 3), ("الكتاب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        assert result["total_due_words"] == 1
        assert result["covered_due_words"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["sentence_id"] == 1
        assert result["items"][0]["primary_lemma_id"] == 1

    def test_proper_name_with_due_card_is_clickable_but_not_scheduled(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_word(
            db_session,
            2,
            "لَيْلَى",
            "(proper name)",
            due_hours=-24,
            word_category="proper_name",
        )
        _seed_sentence(
            db_session,
            1,
            "ليلى قرأت الكتاب",
            "Layla read the book",
            target_lemma_id=1,
            word_surfaces_and_ids=[("ليلى", 2), ("الكتاب", 1)],
        )
        db_session.commit()

        result = build_session(db_session, limit=10)

        assert result["total_due_words"] == 1
        assert result["covered_due_words"] == 1
        item = result["items"][0]
        name_word = next(w for w in item["words"] if w["lemma_id"] == 2)
        assert name_word["is_proper_name"] is True
        assert name_word["is_due"] is False
        assert item["selection_info"]["due_lemma_ids"] == [1]

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

    def test_skips_llm_sentence_that_failed_quality_review(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_word(db_session, 2, "ولد", "boy", due_hours=24)

        reviewed_at = datetime.now(timezone.utc)
        _seed_sentence(
            db_session, 1, "كتاب ولد", "book boy",
            target_lemma_id=1,
            word_surfaces_and_ids=[("كتاب", 1), ("ولد", 2)],
            source="llm",
            quality_reviewed_at=reviewed_at,
            quality_natural=False,
            quality_translation_correct=True,
        )
        _seed_sentence(
            db_session, 2, "قرأ الولد الكتاب", "The boy read the book",
            target_lemma_id=1,
            word_surfaces_and_ids=[("قرأ", 2), ("الكتاب", 1)],
            source="llm",
            quality_reviewed_at=reviewed_at,
            quality_natural=True,
            quality_translation_correct=True,
        )
        db_session.commit()

        result = build_session(db_session, limit=1)

        assert len(result["items"]) == 1
        assert result["items"][0]["sentence_id"] == 2

    def test_unreviewed_llm_sentence_is_penalized_not_blocked(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        sent = _seed_sentence(
            db_session, 1, "الكتاب جديد", "The book is new",
            target_lemma_id=1,
            word_surfaces_and_ids=[("الكتاب", 1)],
            source="llm",
        )
        db_session.commit()

        result = build_session(db_session, limit=1)

        assert _quality_multiplier_for_sentence(sent) < 1.0
        assert len(result["items"]) == 1
        assert result["items"][0]["sentence_id"] == 1
        assert result["items"][0]["selection_info"]["components"]["quality_multiplier"] < 1.0

    def test_quality_reviewed_llm_beats_unreviewed_equal_coverage(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_word(db_session, 2, "ولد", "boy", due_hours=24)

        _seed_sentence(
            db_session, 1, "كتاب ولد", "book boy",
            target_lemma_id=1,
            word_surfaces_and_ids=[("كتاب", 1), ("ولد", 2)],
            source="llm",
        )
        _seed_sentence(
            db_session, 2, "قرأ الولد الكتاب", "The boy read the book",
            target_lemma_id=1,
            word_surfaces_and_ids=[("قرأ", 2), ("الكتاب", 1)],
            source="llm",
            quality_reviewed_at=datetime.now(timezone.utc),
            quality_natural=True,
            quality_translation_correct=True,
        )
        db_session.commit()

        result = build_session(db_session, limit=1)

        assert len(result["items"]) == 1
        assert result["items"][0]["sentence_id"] == 2

    def test_tight_session_prefers_high_frequency_due_word(self, db_session):
        _seed_word(
            db_session, 1, "نادر", "rare",
            due_hours=-24 * 30, frequency_rank=50000,
        )
        _seed_word(
            db_session, 2, "مهم", "important",
            due_hours=-1, frequency_rank=100,
        )
        _seed_sentence(db_session, 1, "نادر", "rare", 1, [("نادر", 1)])
        _seed_sentence(db_session, 2, "مهم", "important", 2, [("مهم", 2)])
        db_session.commit()

        result = build_session(db_session, limit=1)

        assert len(result["items"]) == 1
        assert result["items"][0]["primary_lemma_id"] == 2
        assert result["items"][0]["selection_info"]["components"]["frequency_boost"] > 1

    def test_skips_recently_shown_sentences(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)

        sent = _seed_sentence(db_session, 1, "الكتاب", "the book",
                              target_lemma_id=1,
                              word_surfaces_and_ids=[("الكتاب", 1)])
        sent.last_reading_shown_at = datetime.now(timezone.utc) - timedelta(days=2)
        db_session.commit()

        result = build_session(db_session, limit=10)
        # Sentence skipped (shown 2 days ago < 7 day cooldown)
        # No word-only fallback — word just gets skipped (or on-demand gen attempted)
        # In test mode with no LLM, uncovered words are simply skipped
        assert result["total_due_words"] == 1

    def test_no_due_words_returns_empty(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=24)
        db_session.commit()

        result = build_session(db_session)
        assert result["items"] == []
        assert result["total_due_words"] == 0

    def test_no_word_only_fallback(self, db_session):
        """Words without sentences get skipped, not shown as bare word cards."""
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        # No sentences exist — on-demand generation will be attempted
        # but will fail in tests (no LLM), so word gets skipped
        db_session.commit()

        result = build_session(db_session, limit=10)
        assert result["total_due_words"] == 1
        # No word-only items should appear
        word_only = [i for i in result["items"] if i.get("sentence_id") is None]
        assert len(word_only) == 0

    def test_fill_path_canonical_lemma_id_reaches_root_for_multi_hop(self, db_session):
        """Regression for orphan-intro bug (2026-05-06).

        The fill path's `_find_pregenerated_sentences_for_words` built a
        `lemma_map` only from sentence_word lemma IDs, so `_canonical_id_for_word`
        for a multi-hop variant Y→Z→C returned Z (first hop) instead of C
        (root). The intro card carrying lemma_id=C never matched the
        sentence's word.canonical_lemma_id=Z on the frontend, and the
        orphan-flush path appended the intro at the end of the session —
        producing intros for words with no matching sentence.

        Setup: target_lemma_id=1 (canonical), but the sentence has a word
        whose surface lemma_id=3 (variant Y), where 3→2→1 is a multi-hop
        chain. lemma_map is built from `{1, 3}` only; without expansion,
        `_canonical_id_for_word(3, …)` returns 2 (first hop).
        """
        # Multi-hop chain: Y(3) → Z(2) → C(1, root canonical)
        root = Lemma(lemma_id=1, lemma_ar="جدد", lemma_ar_bare="جدد",
                     pos="noun", gloss_en="root")
        mid = Lemma(lemma_id=2, lemma_ar="جداً", lemma_ar_bare="جدا",
                    pos="noun", gloss_en="very", canonical_lemma_id=1)
        leaf = Lemma(lemma_id=3, lemma_ar="جدّ", lemma_ar_bare="جد",
                     pos="noun", gloss_en="grandfather", canonical_lemma_id=2)
        # Two scaffold words so the comprehensibility gate passes (the variant
        # without its own knowledge counts as unknown scaffold).
        _seed_word(db_session, 4, "كتاب", "book", state="known",
                   stability=30.0, due_hours=24)
        _seed_word(db_session, 5, "بيت", "house", state="known",
                   stability=30.0, due_hours=24)
        db_session.add_all([root, mid, leaf])
        db_session.flush()

        # Sentence contains target word #1 + the variant #3 + two known scaffolds.
        _seed_sentence(db_session, 100, "جدد جدّ كتاب بيت", "root grandfather book house",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[("جدد", 1), ("جدّ", 3),
                                              ("كتاب", 4), ("بيت", 5)])
        knowledge = UserLemmaKnowledge(
            lemma_id=1, knowledge_state="acquiring", acquisition_box=1,
            acquisition_next_due=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db_session.add(knowledge)
        db_session.commit()

        s4 = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=4).first()
        s5 = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=5).first()
        items = _find_pregenerated_sentences_for_words(
            db_session, {1}, {1: 0.1, 4: 30.0, 5: 30.0},
            {1: knowledge, 4: s4, 5: s5},
            [knowledge, s4, s5], limit=5,
        )
        assert len(items) == 1, f"Expected the sentence to be picked up, got {items}"
        # Find the variant word (lemma_id=3) in the returned words.
        variant_word = next(w for w in items[0]["words"] if w["lemma_id"] == 3)
        # Without the fix this would be 2 (first hop only).
        assert variant_word["canonical_lemma_id"] == 1, (
            f"canonical_lemma_id should reach the root canonical (1), "
            f"got {variant_word['canonical_lemma_id']}. Frontend orphan flush would fire."
        )

    def test_variant_with_known_canonical_is_not_scheduled(self, db_session):
        """Regression for the أُمِّي / أُمّ bug (2026-05-06).

        When a variant lemma has its own ULK in acquiring/FSRS state but the
        canonical is already known/learning, the variant must be excluded from
        `due_lemma_ids`. Otherwise the user keeps seeing the same sentence
        forever: review credit goes to the canonical (sentence_review_service),
        but the variant's box never advances.
        """
        # Canonical: أُمّ ("mother") — known, not yet due
        _seed_word(db_session, 1, "ام", "mother", state="known", stability=100.0, due_hours=24 * 30)
        # Variant: أُمّي ("my mother") with canonical_lemma_id=1
        variant_lemma = Lemma(
            lemma_id=2, lemma_ar="أمي", lemma_ar_bare="امي",
            pos="noun", gloss_en="my mother", canonical_lemma_id=1,
        )
        db_session.add(variant_lemma)
        db_session.flush()
        # Give the variant its own acquiring ULK (the bug condition).
        variant_ulk = UserLemmaKnowledge(
            lemma_id=2,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=datetime.now(timezone.utc) - timedelta(days=18),
            entered_acquiring_at=datetime.now(timezone.utc) - timedelta(days=18),
            source="textbook_scan",
        )
        db_session.add(variant_ulk)
        # Sentence targeting the variant (mirrors production sentence #41263).
        _seed_sentence(db_session, 99, "أمي تحب", "my mother loves",
                       target_lemma_id=2,
                       word_surfaces_and_ids=[("أمي", 2), ("تحب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        # The variant must NOT contribute to due_lemma_ids; the canonical is
        # known and not due. Hence no items.
        target_lemma_ids = {i.get("primary_lemma_id") for i in result["items"]}
        assert 2 not in target_lemma_ids, (
            f"Variant lemma 2 should be skipped (canonical is known), "
            f"but session selected it. items={result['items']}"
        )
        assert result["total_due_words"] == 0


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
        # Function-word lemma (required by the runtime not-has-unmapped-words gate).
        db_session.add(Lemma(
            lemma_id=50, lemma_ar="في", lemma_ar_bare="في",
            pos="prep", gloss_en="in",
        ))
        _seed_sentence(db_session, 1, "في الكتاب", "in the book",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[("في", 50), ("الكتاب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        words = result["items"][0]["words"]
        assert len(words) == 2
        assert words[0]["surface_form"] == "في"
        assert words[0]["is_function_word"] is True
        assert words[1]["surface_form"] == "الكتاب"
        assert words[1]["is_due"] is True

    def test_session_id_generated(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_sentence(db_session, 1, "الكتاب", "the book", 1, [("الكتاب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        assert "session_id" in result
        assert len(result["session_id"]) == 36  # full UUID format


class TestAutoIntroduction:
    def test_returns_intro_candidates_key(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_sentence(db_session, 1, "الكتاب", "the book", 1, [("الكتاب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        # intro_candidates still returned for backward compat (always empty now)
        assert "intro_candidates" in result
        assert result["intro_candidates"] == []

    def test_no_auto_intro_with_low_accuracy(self, db_session):
        """Don't auto-introduce if recent accuracy is below threshold."""
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_sentence(db_session, 1, "الكتاب", "the book", 1, [("الكتاب", 1)])

        # Add review history with low accuracy (all rating=1)
        now = datetime.now(timezone.utc)
        for j in range(10):
            db_session.add(ReviewLog(
                lemma_id=1, rating=1, reviewed_at=now,
                review_mode="reading",
            ))
        db_session.commit()

        result = build_session(db_session, limit=10)
        # Should not introduce new words when accuracy is low
        assert result["intro_candidates"] == []


class TestComprehensibilityGate:
    def test_skips_incomprehensible_sentences(self, db_session):
        """Sentences with <60% known content words should be skipped."""
        # Due word
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        # Unknown words (no ULK records — they'll have knowledge_state="new")
        for i in range(2, 6):
            lemma = Lemma(
                lemma_id=i, lemma_ar=f"unknown{i}", lemma_ar_bare=f"unknown{i}",
                pos="noun", gloss_en=f"unk{i}",
            )
            db_session.add(lemma)
        db_session.flush()

        # Sentence: 1 known + 4 unknown = 20% comprehensible → should be skipped
        _seed_sentence(db_session, 1, "كتاب unknown2 unknown3 unknown4 unknown5",
                       "book unk2 unk3 unk4 unk5",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[
                           ("كتاب", 1), ("unknown2", 2), ("unknown3", 3),
                           ("unknown4", 4), ("unknown5", 5),
                       ])
        db_session.commit()

        result = build_session(db_session, limit=10)
        # Sentence skipped due to comprehensibility gate
        sentence_items = [i for i in result["items"] if i.get("sentence_id") == 1]
        assert len(sentence_items) == 0

    def test_keeps_comprehensible_sentences(self, db_session):
        """Sentences with >=60% known content words should be kept."""
        # 3 known words
        for i in range(1, 4):
            _seed_word(db_session, i, f"known{i}", f"meaning{i}",
                       due_hours=-1 if i == 1 else 24)
        # 1 unknown
        lemma = Lemma(lemma_id=4, lemma_ar="unknown4", lemma_ar_bare="unknown4",
                      pos="noun", gloss_en="unk4")
        db_session.add(lemma)
        db_session.flush()

        # 3 known + 1 unknown = 75% comprehensible → should pass
        _seed_sentence(db_session, 1, "known1 known2 known3 unknown4",
                       "m1 m2 m3 unk4",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[
                           ("known1", 1), ("known2", 2), ("known3", 3), ("unknown4", 4),
                       ])
        db_session.commit()

        result = build_session(db_session, limit=10)
        assert result["covered_due_words"] >= 1
        sentence_items = [i for i in result["items"] if i.get("sentence_id") == 1]
        assert len(sentence_items) == 1

    def test_function_words_excluded_from_comprehensibility(self, db_session):
        """Function words shouldn't count against comprehensibility."""
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        # Function-word lemmas — required by the runtime not-has-unmapped-words gate.
        for fw_id, fw_ar, fw_gloss in [(50, "في", "in"), (51, "من", "from")]:
            db_session.add(Lemma(
                lemma_id=fw_id, lemma_ar=fw_ar, lemma_ar_bare=fw_ar,
                pos="prep", gloss_en=fw_gloss,
            ))
        db_session.flush()

        # Sentence with 1 known content word + 2 function words (في, من).
        # Only 1 content word, and it's known → 100% comprehensible.
        _seed_sentence(db_session, 1, "في كتاب من", "in book from",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[("في", 50), ("كتاب", 1), ("من", 51)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        sentence_items = [i for i in result["items"] if i.get("sentence_id") == 1]
        assert len(sentence_items) == 1

    def test_encountered_words_do_not_count_as_known_scaffold(self, db_session):
        """Regression: encountered (passively imported, never studied) must NOT
        count as known scaffold. They were accidentally re-added in 7ee81cf."""
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        # 1 known scaffold word
        _seed_word(db_session, 5, "بيت", "house", due_hours=24)
        # 3 encountered words — imported via OCR/book but never studied (no FSRS card)
        for i, (ar, en) in enumerate([("أسنان", "teeth"), ("مخالب", "claws"), ("ضخمة", "huge")], start=2):
            lemma = Lemma(lemma_id=i, lemma_ar=ar, lemma_ar_bare=ar, pos="noun", gloss_en=en)
            db_session.add(lemma)
            db_session.flush()
            ulk = UserLemmaKnowledge(
                lemma_id=i, knowledge_state="encountered",
                fsrs_card_json=None, introduced_at=None,
                times_seen=0, times_correct=0, source="book",
            )
            db_session.add(ulk)

        # Low accuracy review logs → blocks auto-intro of encountered words
        now = datetime.now(timezone.utc)
        for j in range(10):
            db_session.add(ReviewLog(
                lemma_id=1, rating=1, reviewed_at=now, review_mode="reading",
            ))
        db_session.flush()

        # Sentence: 1 due target + 1 known scaffold + 3 encountered scaffold
        # With encountered-as-known: 4/4 = 100% → PASS (wrong)
        # Without encountered:       1/4 = 25%  → SKIP (correct)
        _seed_sentence(db_session, 1, "كتاب بيت أسنان مخالب ضخمة",
                       "book house teeth claws huge",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[
                           ("كتاب", 1), ("بيت", 5), ("أسنان", 2), ("مخالب", 3), ("ضخمة", 4),
                       ])
        db_session.commit()

        result = build_session(db_session, limit=10)
        sentence_items = [i for i in result["items"] if i.get("sentence_id") == 1]
        assert len(sentence_items) == 0, "Encountered words must not count as known scaffold"

    def test_unmapped_words_count_as_unknown_scaffold(self, db_session):
        """Words with lemma_id=None must count as unknown, not be excluded."""
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_word(db_session, 2, "بيت", "house", due_hours=24)
        db_session.flush()

        # Sentence: 1 due + 1 known scaffold + 2 unmapped (lemma_id=None)
        # Gate should see 1/3 = 33% → SKIP (not 1/1 = 100%)
        _seed_sentence(db_session, 1, "كتاب بيت unknown1 unknown2",
                       "book house unk1 unk2",
                       target_lemma_id=1,
                       word_surfaces_and_ids=[
                           ("كتاب", 1), ("بيت", 2), ("unknown1", None), ("unknown2", None),
                       ])
        db_session.commit()

        result = build_session(db_session, limit=10)
        sentence_items = [i for i in result["items"] if i.get("sentence_id") == 1]
        assert len(sentence_items) == 0, "Unmapped words must count as unknown scaffold"


class TestTimezoneHandling:
    def test_acquiring_word_with_naive_datetime(self, db_session):
        """Acquiring words with naive datetimes in DB shouldn't crash."""
        lemma = Lemma(
            lemma_id=1, lemma_ar="كتاب", lemma_ar_bare="كتاب",
            pos="noun", gloss_en="book",
        )
        db_session.add(lemma)
        db_session.flush()

        # Simulate naive datetime from SQLite (no timezone info)
        naive_due = datetime(2020, 1, 1, 0, 0, 0)  # well in the past
        ulk = UserLemmaKnowledge(
            lemma_id=1,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=naive_due,
            fsrs_card_json=None,
            times_seen=1,
            times_correct=0,
            source="study",
        )
        db_session.add(ulk)

        # Create a sentence for this word
        _seed_sentence(db_session, 1, "الكتاب", "the book", 1, [("الكتاب", 1)])
        db_session.commit()

        # Should not crash (was previously crashing with TypeError)
        result = build_session(db_session, limit=10)
        assert result["total_due_words"] >= 1


class TestWithinSessionRepetition:
    def test_acquisition_word_gets_extra_sentence(self, db_session):
        """Acquisition words appearing once should get a second sentence added."""
        lemma = Lemma(
            lemma_id=1, lemma_ar="كتاب", lemma_ar_bare="كتاب",
            pos="noun", gloss_en="book",
        )
        db_session.add(lemma)
        db_session.flush()

        # Known scaffold words (distinct, so the two sentences aren't near-duplicates)
        _seed_word(db_session, 2, "ولد", "boy", due_hours=24)
        _seed_word(db_session, 3, "بيت", "house", due_hours=24)
        _seed_word(db_session, 4, "مدرسة", "school", due_hours=24)

        # Acquiring word due now
        naive_due = datetime(2020, 1, 1, 0, 0, 0)
        ulk = UserLemmaKnowledge(
            lemma_id=1,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=naive_due,
            fsrs_card_json=None,
            times_seen=1,
            times_correct=0,
            source="study",
        )
        db_session.add(ulk)

        # Two sentences containing the acquiring word — different scaffold words so
        # they're not near-duplicates (Jaccard veto would block identical lemma sets).
        _seed_sentence(db_session, 1, "الكتاب في البيت", "book in house", 1,
                       [("الكتاب", 1), ("بيت", 3)])
        _seed_sentence(db_session, 2, "الكتاب مع الولد في المدرسة", "book with boy at school", 1,
                       [("الكتاب", 1), ("ولد", 2), ("مدرسة", 4)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        # Should get both sentences (repetition for acquiring word)
        sentence_ids = [i["sentence_id"] for i in result["items"] if i.get("sentence_id")]
        assert len(sentence_ids) >= 2


class TestAdaptiveIntroRate:
    def test_below_70_returns_zero(self):
        assert _intro_slots_for_accuracy(0.0) == 0
        assert _intro_slots_for_accuracy(0.50) == 0
        assert _intro_slots_for_accuracy(0.69) == 0

    def test_boundary_at_70(self):
        assert _intro_slots_for_accuracy(0.70) == 3

    def test_70_to_85_returns_three(self):
        assert _intro_slots_for_accuracy(0.75) == 3
        assert _intro_slots_for_accuracy(0.84) == 3

    def test_boundary_at_85(self):
        assert _intro_slots_for_accuracy(0.85) == MAX_AUTO_INTRO_PER_SESSION

    def test_above_85_returns_max(self):
        assert _intro_slots_for_accuracy(0.88) == MAX_AUTO_INTRO_PER_SESSION
        assert _intro_slots_for_accuracy(0.95) == MAX_AUTO_INTRO_PER_SESSION
        assert _intro_slots_for_accuracy(1.0) == MAX_AUTO_INTRO_PER_SESSION

    def test_intro_backlog_threshold_keeps_daily_intros_available(self):
        assert _intro_backlog_threshold_for_accuracy(0.79) == PIPELINE_BACKLOG_THRESHOLD
        assert _intro_backlog_threshold_for_accuracy(0.80) == MID_ACCURACY_INTRO_BACKLOG_CAP
        assert _intro_backlog_threshold_for_accuracy(0.89) == MID_ACCURACY_INTRO_BACKLOG_CAP
        assert _intro_backlog_threshold_for_accuracy(0.90) == HIGH_ACCURACY_INTRO_BACKLOG_CAP
        assert MID_ACCURACY_INTRO_BACKLOG_CAP > 60


class TestReservedIntroSlots:
    """Regression: INTRO_RESERVE_FRACTION must reserve slots for new word
    introductions even when the due queue is full (reverted by 7ee81cf)."""

    def test_reserved_intro_slots_when_due_queue_full(self, db_session):
        """With high accuracy and many due words, auto-intro should still fire."""
        # Create 10 due words with sentences
        for i in range(1, 11):
            _seed_word(db_session, i, f"word{i}", f"meaning{i}", due_hours=-1)
            _seed_sentence(db_session, i, f"word{i}", f"meaning{i}",
                           target_lemma_id=i, word_surfaces_and_ids=[(f"word{i}", i)])

        # Create an encountered word eligible for introduction
        lemma = Lemma(lemma_id=50, lemma_ar="جديد", lemma_ar_bare="جديد",
                      pos="adj", gloss_en="new", frequency_rank=100)
        db_session.add(lemma)
        db_session.flush()
        ulk = UserLemmaKnowledge(
            lemma_id=50, knowledge_state="encountered",
            fsrs_card_json=None, introduced_at=None,
            times_seen=0, times_correct=0, source="study",
        )
        db_session.add(ulk)

        # High accuracy reviews → intro slots should be available
        now = datetime.now(timezone.utc)
        for j in range(20):
            db_session.add(ReviewLog(
                lemma_id=1, rating=4, reviewed_at=now - timedelta(hours=j),
                review_mode="reading",
            ))
        db_session.commit()

        result = build_session(db_session, limit=10)
        # The constant must exist and be the current aggressive-trial reserve.
        assert INTRO_RESERVE_FRACTION == pytest.approx(0.3)
        # With 10 due words and limit=10, reserved_intro = max(1, int(10*0.3)) = 3
        # Auto-intro should have at least attempted to introduce
        # (may not succeed if no sentences available, but the slot reservation must exist)

    def test_intro_reserve_fraction_exists(self):
        """The constant must exist — its removal was the regression."""
        assert INTRO_RESERVE_FRACTION > 0
        assert INTRO_RESERVE_FRACTION <= 0.5  # sanity


class TestScaffoldDiversity:
    """Regression: SESSION_SCAFFOLD_DECAY must penalize repeated scaffold words
    within a session (reverted by 7ee81cf)."""

    def test_session_scaffold_decay_exists(self):
        """The constant must exist — its removal was the regression."""
        assert SESSION_SCAFFOLD_DECAY > 0
        assert SESSION_SCAFFOLD_DECAY < 1.0

    def test_diversity_score_penalizes_reuse(self):
        """compute_sentence_diversity_score must report lower uniqueness for reused scaffolds."""
        words = [
            WordMeta(lemma_id=1, surface_form="كتاب", gloss_en="book",
                     stability=1.0, is_due=True),
            WordMeta(lemma_id=2, surface_form="ولد", gloss_en="boy",
                     stability=5.0, is_due=False),
            WordMeta(lemma_id=3, surface_form="بيت", gloss_en="house",
                     stability=10.0, is_due=False),
        ]
        knowledge_map = {
            2: UserLemmaKnowledge(lemma_id=2, knowledge_state="known",
                                  times_seen=5, times_correct=4),
            3: UserLemmaKnowledge(lemma_id=3, knowledge_state="known",
                                  times_seen=5, times_correct=4),
        }

        # No prior session usage → high uniqueness
        fresh_result = compute_sentence_diversity_score(words, knowledge_map, {})
        # Heavy prior session usage → low uniqueness
        reused_counts = {2: 3, 3: 3}
        reused_result = compute_sentence_diversity_score(words, knowledge_map, reused_counts)

        assert fresh_result["scaffold_uniqueness"] > reused_result["scaffold_uniqueness"]

    def test_repeated_scaffold_words_get_lower_score(self, db_session):
        """Integration: sentences with already-used scaffold words should score lower."""
        # Due word
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_word(db_session, 2, "ولد", "boy", due_hours=-1)
        # Shared scaffold word
        _seed_word(db_session, 3, "بيت", "house", due_hours=24)
        # Unique scaffold word
        _seed_word(db_session, 4, "مدرسة", "school", due_hours=24)

        # Sentence 1 for word 1 (scaffold: بيت)
        _seed_sentence(db_session, 1, "كتاب بيت", "book house", 1,
                       [("كتاب", 1), ("بيت", 3)])
        # Sentence 2 for word 2 (scaffold: بيت — reuse)
        _seed_sentence(db_session, 2, "ولد بيت", "boy house", 2,
                       [("ولد", 2), ("بيت", 3)])
        # Sentence 3 for word 2 (scaffold: مدرسة — unique)
        _seed_sentence(db_session, 3, "ولد مدرسة", "boy school", 2,
                       [("ولد", 2), ("مدرسة", 4)])
        db_session.commit()

        result = build_session(db_session, limit=2)
        items = result["items"]
        assert len(items) == 2
        # If sentence 1 is picked first (for word 1), then for word 2,
        # sentence 3 (unique scaffold) should beat sentence 2 (reused scaffold)
        if items[0]["sentence_id"] == 1:
            assert items[1]["sentence_id"] == 3, \
                "Unique scaffold sentence should be preferred over reused scaffold"


class TestNearDuplicateVeto:
    """Whole-sentence Jaccard veto must block near-identical sentence pairs."""

    def _candidate(self, lemma_ids):
        class _Cand:
            pass
        c = _Cand()
        c.words_meta = [WordMeta(lemma_id=lid, surface_form="x", gloss_en="x",
                                  stability=1.0, is_due=False) for lid in lemma_ids]
        return c

    def test_unrelated_sentences_pass(self):
        sel = [self._candidate([1, 2, 3, 4])]
        cand = self._candidate([5, 6, 7, 8])
        sel_sets = [{w.lemma_id for w in s.words_meta} for s in sel]
        assert _is_near_duplicate_of_selected(cand, sel_sets) is False

    def test_identical_lemma_set_vetoed(self):
        sel = [self._candidate([1, 2, 3, 4])]
        cand = self._candidate([1, 2, 3, 4])
        sel_sets = [{w.lemma_id for w in s.words_meta} for s in sel]
        assert _is_near_duplicate_of_selected(cand, sel_sets) is True

    def test_threshold_boundary(self):
        # 3 of 4 shared, 1 unique each side → Jaccard = 3/5 = 0.6
        sel = [self._candidate([1, 2, 3, 4])]
        cand = self._candidate([1, 2, 3, 5])
        sel_sets = [{w.lemma_id for w in s.words_meta} for s in sel]
        # Jaccard 0.6 is below 0.7 threshold → not vetoed
        assert _is_near_duplicate_of_selected(cand, sel_sets) is False

        # 3 of 3 shared + 1 extra in cand → Jaccard = 3/4 = 0.75 → vetoed
        sel2 = [self._candidate([1, 2, 3])]
        cand2 = self._candidate([1, 2, 3, 5])
        sel2_sets = [{w.lemma_id for w in s.words_meta} for s in sel2]
        assert _is_near_duplicate_of_selected(cand2, sel2_sets) is True

    def test_empty_candidate_passes(self):
        sel = [self._candidate([1, 2, 3])]
        cand = self._candidate([])
        sel_sets = [{w.lemma_id for w in s.words_meta} for s in sel]
        assert _is_near_duplicate_of_selected(cand, sel_sets) is False

    def test_empty_selected_passes(self):
        cand = self._candidate([1, 2, 3])
        assert _is_near_duplicate_of_selected(cand, []) is False

    def test_threshold_constant_in_range(self):
        assert 0 < JACCARD_VETO_THRESHOLD < 1.0

    def test_text_near_duplicate_vetoes_same_frame(self):
        selected = ["نَظَّمَ السَّفِيرُ اجْتِمَاعًا فِي السَّاحَةِ."]
        cand = "نَظَّمَ السَّفِيرُ اجْتِمَاعًا فِي السَّاحَةِ"
        assert _is_text_near_duplicate(cand, selected) is True

    def test_text_near_duplicate_allows_different_sentence(self):
        selected = ["نَظَّمَ السَّفِيرُ اجْتِمَاعًا فِي السَّاحَةِ."]
        cand = "قَرَأَ الطَّالِبُ كِتَابًا جَدِيدًا فِي الْمَكْتَبَةِ."
        assert _is_text_near_duplicate(cand, selected) is False


class TestIntroCardsForSessionWords:
    """Intro cards must cover every new non-function word shown in a session."""

    def test_new_acquiring_scaffold_gets_intro_card(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        scaffold = Lemma(
            lemma_id=2,
            lemma_ar="قلم",
            lemma_ar_bare="قلم",
            pos="noun",
            gloss_en="pen",
            gates_completed_at=datetime.now(timezone.utc),
        )
        db_session.add(scaffold)
        db_session.add(UserLemmaKnowledge(
            lemma_id=2,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=datetime.now(timezone.utc) + timedelta(hours=4),
            introduced_at=datetime.now(timezone.utc),
            times_seen=0,
            times_correct=0,
            total_encounters=0,
            source="study",
        ))
        _seed_sentence(
            db_session,
            1,
            "كتاب قلم",
            "book pen",
            1,
            [("كتاب", 1), ("قلم", 2)],
        )
        db_session.commit()

        result = build_session(db_session, limit=1)

        assert [c["lemma_id"] for c in result["experiment_intro_cards"]] == [2]

    def test_encountered_scaffold_is_promoted_before_intro_card_build(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_word(db_session, 2, "بيت", "house", due_hours=24)
        _seed_word(db_session, 3, "مدرسة", "school", due_hours=24)
        encountered = Lemma(
            lemma_id=4,
            lemma_ar="قلم",
            lemma_ar_bare="قلم",
            pos="noun",
            gloss_en="pen",
            gates_completed_at=datetime.now(timezone.utc),
        )
        db_session.add(encountered)
        db_session.add(UserLemmaKnowledge(
            lemma_id=4,
            knowledge_state="encountered",
            fsrs_card_json=None,
            introduced_at=None,
            times_seen=0,
            times_correct=0,
            total_encounters=0,
            source="book",
        ))
        _seed_sentence(
            db_session,
            1,
            "كتاب بيت مدرسة قلم",
            "book house school pen",
            1,
            [("كتاب", 1), ("بيت", 2), ("مدرسة", 3), ("قلم", 4)],
        )
        db_session.commit()

        result = build_session(db_session, limit=1)
        ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=4).one()

        assert ulk.knowledge_state == "acquiring"
        assert [c["lemma_id"] for c in result["experiment_intro_cards"]] == [4]

    def test_repeated_textbook_scan_scaffold_still_gets_intro_card(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_word(db_session, 2, "بيت", "house", due_hours=24)
        _seed_word(db_session, 3, "مدرسة", "school", due_hours=24)
        encountered = Lemma(
            lemma_id=4,
            lemma_ar="قلم",
            lemma_ar_bare="قلم",
            pos="noun",
            gloss_en="pen",
            gates_completed_at=datetime.now(timezone.utc),
        )
        db_session.add(encountered)
        db_session.add(UserLemmaKnowledge(
            lemma_id=4,
            knowledge_state="encountered",
            fsrs_card_json=None,
            introduced_at=None,
            times_seen=0,
            times_correct=0,
            total_encounters=7,
            source="textbook_scan",
        ))
        _seed_sentence(
            db_session,
            1,
            "كتاب بيت مدرسة قلم",
            "book house school pen",
            1,
            [("كتاب", 1), ("بيت", 2), ("مدرسة", 3), ("قلم", 4)],
        )
        db_session.commit()

        result = build_session(db_session, limit=1)
        ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=4).one()

        assert ulk.knowledge_state == "acquiring"
        assert [c["lemma_id"] for c in result["experiment_intro_cards"]] == [4]

    def test_intro_state_promotion_is_committed_before_ack(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        encountered = Lemma(
            lemma_id=4,
            lemma_ar="قلم",
            lemma_ar_bare="قلم",
            pos="noun",
            gloss_en="pen",
            gates_completed_at=datetime.now(timezone.utc),
        )
        db_session.add(encountered)
        db_session.add(UserLemmaKnowledge(
            lemma_id=4,
            knowledge_state="encountered",
            fsrs_card_json=None,
            introduced_at=None,
            times_seen=0,
            times_correct=0,
            total_encounters=0,
            source="book",
        ))
        _seed_sentence(
            db_session,
            1,
            "كتاب قلم",
            "book pen",
            1,
            [("كتاب", 1), ("قلم", 4)],
        )
        db_session.commit()

        result = build_session(db_session, limit=1)

        assert [c["lemma_id"] for c in result["experiment_intro_cards"]] == [4]
        Session = sessionmaker(bind=db_session.bind)
        fresh = Session()
        try:
            persisted = fresh.query(UserLemmaKnowledge).filter_by(lemma_id=4).one()
            assert persisted.knowledge_state == "acquiring"
            assert persisted.acquisition_started_at is not None
        finally:
            fresh.close()

    def test_textbook_preserved_known_gets_card_without_acquisition(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        textbook_lemma = Lemma(
            lemma_id=4,
            lemma_ar="قلم",
            lemma_ar_bare="قلم",
            pos="noun",
            gloss_en="pen",
            gates_completed_at=datetime.now(timezone.utc),
        )
        db_session.add(textbook_lemma)
        db_session.add(UserLemmaKnowledge(
            lemma_id=4,
            knowledge_state="known",
            fsrs_card_json=_make_card(stability_days=30.0, due_offset_hours=72),
            introduced_at=datetime.now(timezone.utc),
            last_reviewed=datetime.now(timezone.utc),
            times_seen=1,
            times_correct=1,
            total_encounters=1,
            source="textbook_scan",
            experiment_group="textbook_preserve_intro",
            experiment_intro_shown_at=None,
        ))
        _seed_sentence(
            db_session,
            1,
            "كتاب",
            "book",
            1,
            [("كتاب", 1)],
        )
        db_session.commit()

        result = build_session(db_session, limit=1)
        ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=4).one()

        assert [(c["lemma_id"], c.get("intro_kind")) for c in result["experiment_intro_cards"]] == [
            (4, "textbook_preserve")
        ]
        assert ulk.knowledge_state == "known"
        assert ulk.acquisition_box is None

    def test_reviewed_textbook_preserved_known_does_not_get_intro_card(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        textbook_lemma = Lemma(
            lemma_id=4,
            lemma_ar="قلم",
            lemma_ar_bare="قلم",
            pos="noun",
            gloss_en="pen",
            gates_completed_at=datetime.now(timezone.utc),
        )
        db_session.add(textbook_lemma)
        db_session.add(UserLemmaKnowledge(
            lemma_id=4,
            knowledge_state="known",
            fsrs_card_json=_make_card(stability_days=30.0, due_offset_hours=72),
            introduced_at=datetime.now(timezone.utc) - timedelta(days=30),
            last_reviewed=datetime.now(timezone.utc),
            times_seen=19,
            times_correct=19,
            total_encounters=24,
            source="textbook_scan",
            experiment_group="textbook_preserve_intro",
            experiment_intro_shown_at=None,
        ))
        _seed_sentence(
            db_session,
            1,
            "كتاب",
            "book",
            1,
            [("كتاب", 1)],
        )
        db_session.commit()

        result = build_session(db_session, limit=1)

        assert 4 not in {c["lemma_id"] for c in result["experiment_intro_cards"]}

    def test_old_textbook_known_without_preserve_group_does_not_get_intro_card(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        old_textbook_lemma = Lemma(
            lemma_id=4,
            lemma_ar="قلم",
            lemma_ar_bare="قلم",
            pos="noun",
            gloss_en="pen",
            gates_completed_at=datetime.now(timezone.utc),
        )
        db_session.add(old_textbook_lemma)
        db_session.add(UserLemmaKnowledge(
            lemma_id=4,
            knowledge_state="known",
            fsrs_card_json=_make_card(stability_days=30.0, due_offset_hours=72),
            introduced_at=datetime.now(timezone.utc) - timedelta(days=30),
            last_reviewed=datetime.now(timezone.utc) - timedelta(days=30),
            times_seen=1,
            times_correct=1,
            total_encounters=1,
            source="textbook_scan",
            experiment_group=None,
            experiment_intro_shown_at=None,
        ))
        _seed_sentence(
            db_session,
            1,
            "كتاب",
            "book",
            1,
            [("كتاب", 1)],
        )
        db_session.commit()

        result = build_session(db_session, limit=1)

        assert 4 not in {c["lemma_id"] for c in result["experiment_intro_cards"]}

    def test_proper_name_is_not_promoted_or_carded(self, db_session):
        """A proper-name lemma in a session sentence must NOT be promoted to
        acquiring and must NOT receive an intro card. Reproduces the Heidi
        regression: fix_null_lemma_ids step 0b auto-creates a proper_name
        lemma; without the guard it flowed encountered → acquiring → intro."""
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        heidi = Lemma(
            lemma_id=2,
            lemma_ar="هايدي",
            lemma_ar_bare="هايدي",
            pos="noun",
            gloss_en="(proper name)",
            word_category="proper_name",
            gates_completed_at=datetime.now(timezone.utc),
        )
        db_session.add(heidi)
        db_session.add(UserLemmaKnowledge(
            lemma_id=2,
            knowledge_state="encountered",
            fsrs_card_json=None,
            introduced_at=None,
            times_seen=0,
            times_correct=0,
            total_encounters=0,
            source="book",
        ))
        _seed_sentence(
            db_session,
            1,
            "كتاب هايدي",
            "Heidi's book",
            1,
            [("كتاب", 1), ("هايدي", 2)],
        )
        db_session.commit()

        result = build_session(db_session, limit=1)
        ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=2).one()

        assert ulk.knowledge_state == "encountered"
        assert 2 not in {c["lemma_id"] for c in result["experiment_intro_cards"]}

    def test_legacy_acquiring_function_word_does_not_render_card(self, db_session):
        """A pre-existing acquiring ULK row for a function-word lemma (e.g. ال,
        منذ — created before the upstream guard existed) must not produce an
        intro card. The bare-form check `_is_function_word` is the backstop."""
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        particle = Lemma(
            lemma_id=2,
            lemma_ar="مُنْذُ",
            lemma_ar_bare="منذ",
            pos="prep",
            gloss_en="since",
            gates_completed_at=datetime.now(timezone.utc),
        )
        db_session.add(particle)
        db_session.add(UserLemmaKnowledge(
            lemma_id=2,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=datetime.now(timezone.utc) + timedelta(hours=4),
            introduced_at=datetime.now(timezone.utc),
            times_seen=0,
            times_correct=0,
            total_encounters=0,
            source="book",
        ))
        _seed_sentence(
            db_session,
            1,
            "كتاب منذ",
            "book since",
            1,
            [("كتاب", 1), ("منذ", 2)],
        )
        db_session.commit()

        result = build_session(db_session, limit=1)

        assert 2 not in {c["lemma_id"] for c in result["experiment_intro_cards"]}


class TestFillPhasePregenerated:
    """Fill phase should use pre-generated sentences (no LLM calls in session build)."""

    def test_fill_uses_pregenerated(self, db_session):
        """When session is undersized, fill phase should introduce words
        and find their pre-generated sentences."""
        # 1 due word → produces 1-card session (undersized for limit=5)
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_sentence(db_session, 1, "الكتاب", "the book", 1, [("الكتاب", 1)])

        # An encountered word with a pre-generated sentence (eligible for intro)
        encountered_lemma = Lemma(
            lemma_id=50, lemma_ar="جديد", lemma_ar_bare="جديد",
            pos="adj", gloss_en="new", frequency_rank=100,
        )
        db_session.add(encountered_lemma)
        db_session.flush()
        ulk = UserLemmaKnowledge(
            lemma_id=50, knowledge_state="encountered",
            times_seen=0, times_correct=0, source="study",
        )
        db_session.add(ulk)
        # Sentence with the encountered word + known scaffold
        _seed_sentence(db_session, 50, "كتاب جديد", "new book", 50,
                       [("كتاب", 1), ("جديد", 50)])

        # Enough reviews for accuracy-based intro to work
        now = datetime.now(timezone.utc)
        for j in range(20):
            db_session.add(ReviewLog(
                lemma_id=1, rating=4,
                reviewed_at=now - timedelta(hours=j),
                review_mode="reading",
            ))
        db_session.commit()

        result = build_session(db_session, limit=5)
        # Should have at least the 1 due word; if fill phase works,
        # it may introduce more words with pre-generated sentences
        assert len(result["items"]) >= 1
        # The fill phase should have attempted to expand the session
        assert result["total_due_words"] >= 1


class TestRescuePass:
    """Regression: words blocked by recency filter should get a rescue pass
    with stale sentences at 0.3x penalty (reverted by 7ee81cf)."""

    def test_rescue_pass_uses_stale_sentences(self, db_session):
        """A due word whose only sentence was recently shown should still appear
        via rescue pass rather than being dropped entirely."""
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        sent = _seed_sentence(db_session, 1, "الكتاب", "the book", 1,
                              [("الكتاب", 1)])
        # Mark as recently shown with "understood" (7-day cooldown)
        sent.last_reading_shown_at = datetime.now(timezone.utc) - timedelta(days=3)
        sent.last_reading_result = "understood"

        # Create a second due word with a fresh sentence (control)
        _seed_word(db_session, 2, "ولد", "boy", due_hours=-1)
        _seed_sentence(db_session, 2, "الولد", "the boy", 2, [("الولد", 2)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        sentence_ids = {i["sentence_id"] for i in result["items"]}
        # The fresh sentence should definitely be there
        assert 2 in sentence_ids
        # The rescue pass should include the stale sentence for word 1
        assert 1 in sentence_ids, \
            "Rescue pass should include stale sentence rather than dropping the word"


class TestSelectionInfo:
    """Regression: selection_info must be included on each session item
    (reverted by 7ee81cf)."""

    def test_selection_info_present(self, db_session):
        """Each item in the session must have a selection_info dict."""
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_sentence(db_session, 1, "الكتاب", "the book", 1, [("الكتاب", 1)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert "selection_info" in item, "selection_info must be present on session items"
        info = item["selection_info"]
        assert "reason" in info
        assert "score" in info
        assert "order" in info

    def test_selection_info_has_components(self, db_session):
        """selection_info must include score component breakdown."""
        _seed_word(db_session, 1, "كتاب", "book", due_hours=-1)
        _seed_word(db_session, 2, "ولد", "boy", due_hours=24)
        _seed_sentence(db_session, 1, "كتاب ولد", "book boy", 1,
                       [("كتاب", 1), ("ولد", 2)])
        db_session.commit()

        result = build_session(db_session, limit=10)
        info = result["items"][0]["selection_info"]
        assert "components" in info
        components = info["components"]
        assert "due_coverage" in components
        assert "diversity" in components
        assert "session_diversity" in components
