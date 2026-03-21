"""Tests for familiar encountered words counting as known in comprehensibility gate."""

from datetime import datetime, timezone, timedelta

import pytest

from app.models import Lemma, UserLemmaKnowledge, Sentence, SentenceWord
from app.services.fsrs_service import create_new_card
from app.services.sentence_selector import (
    FAMILIAR_ENCOUNTER_THRESHOLD,
    WordMeta,
    build_session,
)


def _make_card(stability_days=30.0, due_offset_hours=-1):
    card = create_new_card()
    card["stability"] = stability_days
    due = datetime.now(timezone.utc) + timedelta(hours=due_offset_hours)
    card["due"] = due.isoformat()
    return card


def _seed_word(db, lemma_id, arabic, english, state="known",
               stability=30.0, due_hours=-1, total_encounters=0):
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
        fsrs_card_json=_make_card(stability, due_hours) if state not in ("encountered",) else None,
        introduced_at=datetime.now(timezone.utc) - timedelta(days=30) if state != "encountered" else None,
        last_reviewed=datetime.now(timezone.utc) - timedelta(hours=1) if state != "encountered" else None,
        times_seen=10 if state != "encountered" else 0,
        times_correct=8 if state != "encountered" else 0,
        total_encounters=total_encounters,
        source="study" if state != "encountered" else "encountered",
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


def _gate_known_count(scaffold: list[WordMeta]) -> int:
    """Replicate the comprehensibility gate's known-scaffold count logic."""
    return sum(
        1 for w in scaffold
        if w.knowledge_state in ("known", "learning", "lapsed", "acquiring")
        or (w.knowledge_state == "encountered" and w.total_encounters >= FAMILIAR_ENCOUNTER_THRESHOLD)
    )


class TestFamiliarEncounteredGateLogic:
    """Unit tests for the comprehensibility gate's treatment of encountered words."""

    def test_encountered_below_threshold_not_counted(self):
        """Encountered words below the threshold should NOT count as known."""
        scaffold = [
            WordMeta(lemma_id=2, surface_form="ولد", gloss_en="boy",
                     stability=None, is_due=False, knowledge_state="encountered",
                     total_encounters=FAMILIAR_ENCOUNTER_THRESHOLD - 1),
            WordMeta(lemma_id=3, surface_form="قلم", gloss_en="pen",
                     stability=None, is_due=False, knowledge_state="encountered",
                     total_encounters=3),
        ]
        assert _gate_known_count(scaffold) == 0

    def test_encountered_at_threshold_counted(self):
        """Encountered words at exactly the threshold should count as known."""
        scaffold = [
            WordMeta(lemma_id=2, surface_form="ولد", gloss_en="boy",
                     stability=None, is_due=False, knowledge_state="encountered",
                     total_encounters=FAMILIAR_ENCOUNTER_THRESHOLD),
        ]
        assert _gate_known_count(scaffold) == 1

    def test_encountered_above_threshold_counted(self):
        """Encountered words well above the threshold should count as known."""
        scaffold = [
            WordMeta(lemma_id=2, surface_form="ولد", gloss_en="boy",
                     stability=None, is_due=False, knowledge_state="encountered",
                     total_encounters=FAMILIAR_ENCOUNTER_THRESHOLD + 20),
        ]
        assert _gate_known_count(scaffold) == 1

    def test_mixed_states_counted_correctly(self):
        """Mix of known, acquiring, encountered-familiar, encountered-unfamiliar, new."""
        scaffold = [
            WordMeta(lemma_id=2, surface_form="ولد", gloss_en="boy",
                     stability=30.0, is_due=False, knowledge_state="known"),
            WordMeta(lemma_id=3, surface_form="قلم", gloss_en="pen",
                     stability=0.5, is_due=False, knowledge_state="acquiring"),
            WordMeta(lemma_id=4, surface_form="بيت", gloss_en="house",
                     stability=None, is_due=False, knowledge_state="encountered",
                     total_encounters=FAMILIAR_ENCOUNTER_THRESHOLD),
            WordMeta(lemma_id=5, surface_form="شمس", gloss_en="sun",
                     stability=None, is_due=False, knowledge_state="encountered",
                     total_encounters=2),
            WordMeta(lemma_id=6, surface_form="قمر", gloss_en="moon",
                     stability=None, is_due=False, knowledge_state="new"),
        ]
        # known=1, acquiring=1, familiar_encountered=1 → 3 known
        # unfamiliar_encountered=1, new=1 → 2 unknown
        assert _gate_known_count(scaffold) == 3
        # 3/5 = 60% → passes gate
        assert _gate_known_count(scaffold) / len(scaffold) >= 0.6

    def test_all_known_states_still_counted(self):
        """Existing known states (known, learning, lapsed, acquiring) still count."""
        scaffold = [
            WordMeta(lemma_id=2, surface_form="a", gloss_en="a",
                     stability=30.0, is_due=False, knowledge_state="known"),
            WordMeta(lemma_id=3, surface_form="b", gloss_en="b",
                     stability=5.0, is_due=False, knowledge_state="learning"),
            WordMeta(lemma_id=4, surface_form="c", gloss_en="c",
                     stability=0.5, is_due=False, knowledge_state="lapsed"),
            WordMeta(lemma_id=5, surface_form="d", gloss_en="d",
                     stability=0.1, is_due=False, knowledge_state="acquiring"),
        ]
        assert _gate_known_count(scaffold) == 4

    def test_zero_encounters_not_counted(self):
        """Encountered words with 0 encounters should not count."""
        scaffold = [
            WordMeta(lemma_id=2, surface_form="ولد", gloss_en="boy",
                     stability=None, is_due=False, knowledge_state="encountered",
                     total_encounters=0),
        ]
        assert _gate_known_count(scaffold) == 0

    def test_new_state_not_counted(self):
        """Words with 'new' state should not count even with encounters."""
        scaffold = [
            WordMeta(lemma_id=2, surface_form="ولد", gloss_en="boy",
                     stability=None, is_due=False, knowledge_state="new",
                     total_encounters=20),
        ]
        assert _gate_known_count(scaffold) == 0


class TestFamiliarEncounteredIntegration:
    """Integration tests via build_session that verify familiar encountered
    words affect sentence selection."""

    def test_familiar_encountered_enables_sentence_selection(self, db_session):
        """A sentence that would fail the gate without familiar encountered words
        should pass when encountered words have enough encounters."""
        # Create 10 due words to fill the session (prevents auto-intro from
        # using all slots and converting encountered words)
        for i in range(1, 11):
            _seed_word(db_session, i, f"word{i}", f"word{i}", state="known", due_hours=-1)
            _seed_sentence(db_session, i, f"word{i}", f"word{i}",
                           target_lemma_id=i,
                           word_surfaces_and_ids=[(f"word{i}", i)])

        # Add a sentence with a due word and familiar encountered scaffold
        _seed_word(db_session, 100, "ولد", "boy", state="encountered",
                   total_encounters=FAMILIAR_ENCOUNTER_THRESHOLD + 5)
        _seed_word(db_session, 101, "قلم", "pen", state="encountered",
                   total_encounters=FAMILIAR_ENCOUNTER_THRESHOLD)
        # Due word with sentence that has encountered scaffold
        _seed_word(db_session, 50, "كتاب", "book", state="known", due_hours=-1)
        _seed_sentence(db_session, 50, "الولد والقلم والكتاب",
                       "the boy and pen and book",
                       target_lemma_id=50,
                       word_surfaces_and_ids=[
                           ("ولد", 100), ("قلم", 101), ("كتاب", 50),
                       ])
        db_session.commit()

        result = build_session(db_session, limit=15)
        sentence_ids = [i["sentence_id"] for i in result["items"]]
        # Sentence 50 should be selectable (familiar encountered scaffold = known)
        assert 50 in sentence_ids

    def test_threshold_constant_value(self):
        """FAMILIAR_ENCOUNTER_THRESHOLD should be 8."""
        assert FAMILIAR_ENCOUNTER_THRESHOLD == 8

    def test_wordmeta_has_total_encounters(self):
        """WordMeta should have total_encounters field with default 0."""
        wm = WordMeta(lemma_id=1, surface_form="test", gloss_en="test",
                      stability=1.0, is_due=False)
        assert wm.total_encounters == 0
        wm2 = WordMeta(lemma_id=1, surface_form="test", gloss_en="test",
                       stability=1.0, is_due=False, total_encounters=15)
        assert wm2.total_encounters == 15
