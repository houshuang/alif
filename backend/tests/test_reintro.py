"""Tests for re-introduction cards and context diversity."""

from datetime import datetime, timezone, timedelta

import pytest

from app.models import Lemma, Root, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.fsrs_service import create_new_card
from app.services.sentence_selector import build_session


def _make_card(stability_days=30.0, due_offset_hours=-1):
    card = create_new_card()
    card["stability"] = stability_days
    due = datetime.now(timezone.utc) + timedelta(hours=due_offset_hours)
    card["due"] = due.isoformat()
    return card


def _seed_word(db, lemma_id, arabic, english, state="known",
               stability=30.0, due_hours=-1, times_seen=10, times_correct=8,
               root_id=None):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        pos="noun",
        gloss_en=english,
        root_id=root_id,
    )
    db.add(lemma)
    db.flush()

    knowledge = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        fsrs_card_json=_make_card(stability, due_hours),
        introduced_at=datetime.now(timezone.utc) - timedelta(days=30),
        last_reviewed=datetime.now(timezone.utc) - timedelta(hours=1),
        times_seen=times_seen,
        times_correct=times_correct,
        source="study",
    )
    db.add(knowledge)
    db.flush()
    return lemma, knowledge


def _seed_sentence(db, sentence_id, arabic, english, target_lemma_id, word_surfaces_and_ids,
                   times_shown=0):
    sent = Sentence(
        id=sentence_id,
        arabic_text=arabic,
        arabic_diacritized=arabic,
        english_translation=english,
        target_lemma_id=target_lemma_id,
        times_shown=times_shown,
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


class TestReintroCards:
    def test_struggling_words_become_reintro(self, db_session):
        """Words with times_seen >= 3 and times_correct == 0 should appear as reintro cards."""
        _seed_word(db_session, 1, "صعب", "difficult",
                   stability=0.1, due_hours=-1, times_seen=5, times_correct=0)
        _seed_word(db_session, 2, "سهل", "easy",
                   stability=0.1, due_hours=-1, times_seen=5, times_correct=4)

        _seed_sentence(db_session, 1, "هذا سهل", "this is easy", 2,
                       [("هذا", None), ("سهل", 2)])

        db_session.commit()

        result = build_session(db_session, limit=10, log_events=False)
        reintro = result.get("reintro_cards", [])
        assert len(reintro) == 1
        assert reintro[0]["lemma_id"] == 1
        assert reintro[0]["lemma_ar"] == "صعب"

    def test_struggling_words_removed_from_sentence_pool(self, db_session):
        """Struggling words should NOT appear as sentence targets."""
        _seed_word(db_session, 1, "صعب", "difficult",
                   stability=0.1, due_hours=-1, times_seen=5, times_correct=0)

        _seed_sentence(db_session, 1, "هذا صعب", "this is difficult", 1,
                       [("هذا", None), ("صعب", 1)])

        db_session.commit()

        result = build_session(db_session, limit=10, log_events=False)
        # Should not have any sentence items since the only due word is struggling
        sentence_items = [i for i in result["items"] if i.get("sentence_id")]
        assert len(sentence_items) == 0

    def test_reintro_limit(self, db_session):
        """At most 3 reintro cards per session."""
        for i in range(6):
            _seed_word(db_session, i + 1, f"word{i}", f"word_{i}",
                       stability=0.1, due_hours=-1, times_seen=5, times_correct=0)
        db_session.commit()

        result = build_session(db_session, limit=10, log_events=False)
        reintro = result.get("reintro_cards", [])
        assert len(reintro) <= 3

    def test_non_struggling_words_not_reintro(self, db_session):
        """Words with some correct reviews should NOT be reintro'd."""
        _seed_word(db_session, 1, "كتاب", "book",
                   stability=0.1, due_hours=-1, times_seen=5, times_correct=2)

        _seed_sentence(db_session, 1, "هذا كتاب", "this is a book", 1,
                       [("هذا", None), ("كتاب", 1)])

        db_session.commit()

        result = build_session(db_session, limit=10, log_events=False)
        reintro = result.get("reintro_cards", [])
        assert len(reintro) == 0

    def test_reintro_includes_root_info(self, db_session):
        """Reintro cards should have root and root_family data."""
        root = Root(root="كتب", core_meaning_en="writing")
        db_session.add(root)
        db_session.flush()

        _seed_word(db_session, 1, "كتاب", "book",
                   stability=0.1, due_hours=-1, times_seen=5, times_correct=0,
                   root_id=root.root_id)
        _seed_word(db_session, 2, "كاتب", "writer",
                   stability=30, due_hours=24, times_seen=10, times_correct=8,
                   root_id=root.root_id)
        db_session.commit()

        result = build_session(db_session, limit=10, log_events=False)
        reintro = result.get("reintro_cards", [])
        assert len(reintro) == 1
        card = reintro[0]
        assert card["root"] == "كتب"
        assert card["root_meaning"] == "writing"
        assert len(card["root_family"]) >= 1


class TestReintroEndpoint:
    def test_remember(self, db_session, client):
        _seed_word(db_session, 1, "صعب", "difficult",
                   stability=0.1, due_hours=-1, times_seen=5, times_correct=0)
        db_session.commit()

        resp = client.post("/api/review/reintro-result", json={
            "lemma_id": 1,
            "result": "remember",
            "session_id": "test-session",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["result"] == "remember"

    def test_show_again(self, db_session, client):
        _seed_word(db_session, 1, "صعب", "difficult",
                   stability=0.1, due_hours=-1, times_seen=5, times_correct=0)
        db_session.commit()

        resp = client.post("/api/review/reintro-result", json={
            "lemma_id": 1,
            "result": "show_again",
            "session_id": "test-session",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["result"] == "show_again"


class TestContextDiversity:
    def test_least_shown_sentence_preferred(self, db_session):
        """When two sentences cover the same due word, prefer the less-shown one."""
        _seed_word(db_session, 1, "كتاب", "book",
                   stability=0.1, due_hours=-1, times_seen=5, times_correct=3)

        # Sentence shown many times
        _seed_sentence(db_session, 1, "هذا كتاب كبير", "this is a big book", 1,
                       [("هذا", None), ("كتاب", 1), ("كبير", None)],
                       times_shown=10)

        # Sentence never shown
        _seed_sentence(db_session, 2, "كتاب جديد", "a new book", 1,
                       [("كتاب", 1), ("جديد", None)],
                       times_shown=0)

        db_session.commit()

        result = build_session(db_session, limit=1, log_events=False)
        items = result["items"]
        assert len(items) >= 1
        # The first selected sentence should be the less-shown one
        assert items[0]["sentence_id"] == 2

    def test_diversity_doesnt_block_selection(self, db_session):
        """A highly-shown sentence should still be selected if it's the only option."""
        _seed_word(db_session, 1, "كتاب", "book",
                   stability=0.1, due_hours=-1, times_seen=5, times_correct=3)

        _seed_sentence(db_session, 1, "هذا كتاب", "this is a book", 1,
                       [("هذا", None), ("كتاب", 1)],
                       times_shown=50)

        db_session.commit()

        result = build_session(db_session, limit=1, log_events=False)
        items = result["items"]
        assert len(items) >= 1
