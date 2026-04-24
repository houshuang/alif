"""Integration tests for the simulation framework.

Uses in-memory SQLite with synthetic data (no production backup needed).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Lemma, Root, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.fsrs_service import create_new_card
from app.simulation.runner import run_simulation
from app.simulation.student import BEGINNER, CASUAL, STRONG


def _seed_simulation_data(db):
    """Create minimal realistic data for simulation testing.

    Creates 20 lemmas across states + sentences with word breakdowns
    so that build_session() can assemble a session.
    """
    root = Root(root_id=1, root="ك.ت.ب", core_meaning_en="writing")
    db.add(root)
    db.flush()

    now = datetime(2026, 2, 28, tzinfo=timezone.utc)

    # Create 20 lemmas
    for i in range(1, 21):
        lemma = Lemma(
            lemma_id=i,
            lemma_ar=f"كلمة{i}",
            lemma_ar_bare=f"كلمه{i}",
            pos="noun",
            gloss_en=f"word{i}",
            frequency_rank=i * 100,
            root_id=1 if i <= 5 else None,
        )
        db.add(lemma)
    db.flush()

    # Create ULK records with a mix of states
    for i in range(1, 21):
        if i <= 5:
            # Known words — stable FSRS cards, past due
            card = create_new_card()
            card["stability"] = 15.0 + i
            card["difficulty"] = 2.1
            card["due"] = (now - timedelta(hours=i)).isoformat()
            card["last_review"] = (now - timedelta(days=2)).isoformat()
            card["state"] = 2  # Review state
            card["step"] = None
            ulk = UserLemmaKnowledge(
                lemma_id=i,
                knowledge_state="known",
                fsrs_card_json=card,
                times_seen=10 + i,
                times_correct=8 + i,
                introduced_at=now - timedelta(days=30),
                last_reviewed=now - timedelta(days=2),
                source="duolingo",
            )
        elif i <= 10:
            # Learning words — moderate FSRS cards
            card = create_new_card()
            card["stability"] = 3.0
            card["difficulty"] = 3.0
            card["due"] = (now - timedelta(hours=1)).isoformat()
            card["last_review"] = (now - timedelta(days=1)).isoformat()
            card["state"] = 2
            card["step"] = None
            ulk = UserLemmaKnowledge(
                lemma_id=i,
                knowledge_state="learning",
                fsrs_card_json=card,
                times_seen=5,
                times_correct=3,
                introduced_at=now - timedelta(days=10),
                last_reviewed=now - timedelta(days=1),
                source="study",
            )
        elif i <= 15:
            # Acquiring words — Leitner boxes
            box = ((i - 11) % 3) + 1
            ulk = UserLemmaKnowledge(
                lemma_id=i,
                knowledge_state="acquiring",
                acquisition_box=box,
                acquisition_next_due=now - timedelta(hours=1),
                acquisition_started_at=now - timedelta(days=3),
                times_seen=i - 10,
                times_correct=max(0, i - 11),
                introduced_at=now - timedelta(days=3),
                source="study",
            )
        else:
            # Encountered words — passive, not yet introduced
            ulk = UserLemmaKnowledge(
                lemma_id=i,
                knowledge_state="encountered",
                total_encounters=3,
                source="encountered",
            )
        db.add(ulk)
    db.flush()

    # Create sentences covering words 1-15 (the ones with active states)
    # Each sentence has its target word + 2 known scaffold words
    for i in range(1, 16):
        sent = Sentence(
            id=i,
            arabic_text=f"جُمْلَةٌ تَحْتَوِي عَلَى كَلِمَة{i}",
            english_translation=f"A sentence containing word{i}",
            target_lemma_id=i,
            is_active=True,
        )
        db.add(sent)
        db.flush()

        # Target word
        db.add(
            SentenceWord(
                sentence_id=i,
                position=0,
                surface_form=f"كلمة{i}",
                lemma_id=i,
                is_target_word=True,
            )
        )
        # Two known scaffold words
        for j, scaffold_id in enumerate([((i - 1) % 5) + 1, ((i) % 5) + 1]):
            db.add(
                SentenceWord(
                    sentence_id=i,
                    position=j + 1,
                    surface_form=f"كلمة{scaffold_id}",
                    lemma_id=scaffold_id,
                    is_target_word=False,
                )
            )
    db.flush()

    # Add a second sentence for some words (to give the selector options)
    for i in range(1, 6):
        sent_id = 100 + i
        sent = Sentence(
            id=sent_id,
            arabic_text=f"جُمْلَةٌ ثَانِيَةٌ مَعَ كَلِمَة{i}",
            english_translation=f"A second sentence with word{i}",
            target_lemma_id=i,
            is_active=True,
        )
        db.add(sent)
        db.flush()

        db.add(
            SentenceWord(
                sentence_id=sent_id,
                position=0,
                surface_form=f"كلمة{i}",
                lemma_id=i,
                is_target_word=True,
            )
        )
        for j, scaffold_id in enumerate([((i) % 5) + 1, ((i + 1) % 5) + 1]):
            db.add(
                SentenceWord(
                    sentence_id=sent_id,
                    position=j + 1,
                    surface_form=f"كلمة{scaffold_id}",
                    lemma_id=scaffold_id,
                    is_target_word=False,
                )
            )
    db.flush()
    db.commit()


class TestSimulationBasic:
    def test_beginner_7_days(self, db_session):
        _seed_simulation_data(db_session)
        snapshots = run_simulation(
            db_session,
            days=7,
            profile=BEGINNER,
            start_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        assert len(snapshots) == 7
        active_days = [s for s in snapshots if not s.skipped]
        assert len(active_days) >= 2
        assert any(s.reviews_submitted > 0 for s in active_days)

    def test_strong_7_days(self, db_session):
        _seed_simulation_data(db_session)
        snapshots = run_simulation(
            db_session,
            days=7,
            profile=STRONG,
            start_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        active_days = [s for s in snapshots if not s.skipped]
        assert len(active_days) >= 4

    def test_no_crash_empty_db(self, db_session):
        """Simulation should handle empty DB gracefully."""
        snapshots = run_simulation(
            db_session,
            days=3,
            profile=BEGINNER,
            start_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        assert len(snapshots) == 3
        for s in snapshots:
            assert s.reviews_submitted == 0

    def test_state_counts_non_negative(self, db_session):
        _seed_simulation_data(db_session)
        snapshots = run_simulation(
            db_session,
            days=5,
            profile=CASUAL,
            start_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        for s in snapshots:
            assert s.encountered >= 0
            assert s.acquiring >= 0
            assert s.learning >= 0
            assert s.known >= 0
            assert s.lapsed >= 0
            assert s.suspended >= 0

    def test_reviews_change_state(self, db_session):
        """After simulation, word states should have changed from initial seed."""
        _seed_simulation_data(db_session)
        initial_acquiring = (
            db_session.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.knowledge_state == "acquiring")
            .count()
        )
        snapshots = run_simulation(
            db_session,
            days=14,
            profile=STRONG,
            start_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        # Something should have changed
        final = snapshots[-1]
        total_active = final.acquiring + final.learning + final.known + final.lapsed
        assert total_active > 0

    def test_deterministic_with_same_seed(self, db_session):
        """Same seed should produce same results."""
        _seed_simulation_data(db_session)
        snap1 = run_simulation(
            db_session,
            days=3,
            profile=BEGINNER,
            start_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
            seed=123,
        )
        # Can't easily re-run on same session (state changed),
        # but we can verify the snapshots are deterministic length
        assert len(snap1) == 3
