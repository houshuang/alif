"""Tests for re-introduction cards and context diversity."""

from datetime import datetime, timezone, timedelta

import pytest

from app.models import Lemma, ReviewLog, Root, Sentence, SentenceWord, UserLemmaKnowledge
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
        gates_completed_at=datetime.now(timezone.utc),
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
        english_translation=english,
        target_lemma_id=target_lemma_id,
        times_shown=times_shown,
        mappings_verified_at=datetime.now(timezone.utc),
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

    def test_struggling_words_keep_sentence_alongside_reintro(self, db_session):
        """Struggling words stay in the sentence pool AND get a reintro card.

        The reintro card without a paired sentence is orphaned teaching — the
        learner sees the card but has no review to reinforce it. Fixed in c9e9793.
        """
        _seed_word(db_session, 1, "صعب", "difficult",
                   stability=0.1, due_hours=-1, times_seen=5, times_correct=0)
        # Function-word lemma — required by the not-has-unmapped-words gate.
        db_session.add(Lemma(
            lemma_id=50, lemma_ar="هذا", lemma_ar_bare="هذا",
            pos="pron", gloss_en="this",
        ))

        _seed_sentence(db_session, 1, "هذا صعب", "this is difficult", 1,
                       [("هذا", 50), ("صعب", 1)])

        db_session.commit()

        result = build_session(db_session, limit=10, log_events=False)
        sentence_items = [i for i in result["items"] if i.get("sentence_id")]
        assert len(sentence_items) == 1
        reintro = result.get("reintro_cards", [])
        assert len(reintro) == 1
        assert reintro[0]["lemma_id"] == 1

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
        db_session.add(Lemma(
            lemma_id=50, lemma_ar="هذا", lemma_ar_bare="هذا",
            pos="pron", gloss_en="this",
        ))

        _seed_sentence(db_session, 1, "هذا كتاب", "this is a book", 1,
                       [("هذا", 50), ("كتاب", 1)])

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
    def test_acknowledgement_does_not_review_acquiring_word(
        self, db_session, client, monkeypatch
    ):
        _, knowledge = _seed_word(
            db_session,
            1,
            "صعب",
            "difficult",
            state="acquiring",
            stability=0.1,
            due_hours=-1,
            times_seen=5,
            times_correct=0,
        )
        acquisition_due = datetime(2026, 7, 9, 12, 0)
        last_reviewed = datetime(2026, 7, 8, 12, 0)
        knowledge.acquisition_box = 1
        knowledge.acquisition_next_due = acquisition_due
        knowledge.fsrs_card_json = None
        knowledge.last_reviewed = last_reviewed
        db_session.commit()

        interactions = []
        monkeypatch.setattr(
            "app.routers.review.log_interaction",
            lambda **payload: interactions.append(payload),
        )

        resp = client.post(
            "/api/review/reintro-result",
            json={
                "lemma_id": 1,
                "result": "acknowledged",
                "session_id": "test-session",
                "client_review_id": "reintro-ack-1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["result"] == "acknowledged"

        db_session.refresh(knowledge)
        assert knowledge.knowledge_state == "acquiring"
        assert knowledge.acquisition_box == 1
        assert knowledge.acquisition_next_due == acquisition_due
        assert knowledge.fsrs_card_json is None
        assert knowledge.times_seen == 5
        assert knowledge.times_correct == 0
        assert knowledge.last_reviewed == last_reviewed
        assert db_session.query(ReviewLog).count() == 0
        assert interactions == [
            {
                "event": "reintro_acknowledged",
                "lemma_id": 1,
                "session_id": "test-session",
                "client_review_id": "reintro-ack-1",
                "submitted_result": "acknowledged",
            }
        ]

    @pytest.mark.parametrize("legacy_result", ["remember", "show_again"])
    def test_legacy_offline_results_are_acknowledgement_only(
        self, db_session, client, legacy_result
    ):
        _, knowledge = _seed_word(
            db_session,
            1,
            "صعب",
            "difficult",
            state="acquiring",
            stability=0.1,
            due_hours=-1,
            times_seen=5,
            times_correct=0,
        )
        knowledge.acquisition_box = 1
        knowledge.acquisition_next_due = datetime(
            2026, 7, 9, 12, 0, tzinfo=timezone.utc
        )
        knowledge.fsrs_card_json = None
        db_session.commit()

        resp = client.post(
            "/api/review/reintro-result",
            json={
                "lemma_id": 1,
                "result": legacy_result,
                "session_id": "test-session",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["result"] == "acknowledged"

        db_session.refresh(knowledge)
        assert knowledge.knowledge_state == "acquiring"
        assert knowledge.acquisition_box == 1
        assert knowledge.fsrs_card_json is None
        assert knowledge.times_seen == 5
        assert knowledge.times_correct == 0
        assert db_session.query(ReviewLog).count() == 0

    def test_acknowledgement_stamps_intro_shown(self, db_session, client):
        """The reintro ack must persist experiment_intro_shown_at so the
        struggling-reintro cooldown can suppress same-day repeats (a word got
        9 reintro cards in 3 days before this, 2026-07-20)."""
        _, knowledge = _seed_word(
            db_session, 1, "صعب", "difficult",
            state="acquiring", stability=0.1, due_hours=-1,
            times_seen=5, times_correct=0,
        )
        assert knowledge.experiment_intro_shown_at is None
        db_session.commit()

        resp = client.post(
            "/api/review/reintro-result",
            json={"lemma_id": 1, "result": "acknowledged", "session_id": "s"},
        )
        assert resp.status_code == 200
        db_session.refresh(knowledge)
        assert knowledge.experiment_intro_shown_at is not None


class TestReintroCooldown:
    def test_fresh_ack_suppresses_reintro_card(self, db_session):
        """A struggling word acked within the cooldown gets no reintro card."""
        _, knowledge = _seed_word(
            db_session, 1, "صعب", "difficult",
            stability=0.1, due_hours=-1, times_seen=5, times_correct=0,
        )
        # Naive datetime, matching how the ack endpoint writes the stamp.
        knowledge.experiment_intro_shown_at = datetime.utcnow() - timedelta(hours=1)
        db_session.commit()

        result = build_session(db_session, limit=10, log_events=False)
        assert result.get("reintro_cards", []) == []

    def test_stale_ack_allows_reintro_card(self, db_session):
        """Once the cooldown has elapsed the reintro card may fire again."""
        _, knowledge = _seed_word(
            db_session, 1, "صعب", "difficult",
            stability=0.1, due_hours=-1, times_seen=5, times_correct=0,
        )
        knowledge.experiment_intro_shown_at = datetime.utcnow() - timedelta(hours=21)
        db_session.commit()

        result = build_session(db_session, limit=10, log_events=False)
        reintro = result.get("reintro_cards", [])
        assert len(reintro) == 1
        assert reintro[0]["lemma_id"] == 1


class TestRescueReservation:
    def test_rescue_not_starved_by_new_backlog(self, db_session):
        """With a big never-reviewed backlog, rescue still gets reserved slots.

        Before 2026-07-20 a 200-word explicit import filled all 6 intro slots
        with new cards for weeks while 50 rescue-eligible words waited.
        """
        from app.services.sentence_selector import _build_intro_cards

        new_ids, rescue_ids = [], []
        for i in range(1, 9):  # 8 new candidates (times_seen=0)
            _, k = _seed_word(
                db_session, i, f"جديد{i}", f"new-{i}",
                state="acquiring", times_seen=0, times_correct=0,
            )
            k.acquisition_box = 1
            new_ids.append(i)
        for i in range(20, 22):  # 2 rescue candidates (seen 6, correct 1)
            _, k = _seed_word(
                db_session, i, f"عالق{i}", f"stuck-{i}",
                state="acquiring", times_seen=6, times_correct=1,
            )
            k.acquisition_box = 1
            rescue_ids.append(i)
        db_session.commit()

        knowledge_by_id = {
            u.lemma_id: u for u in db_session.query(UserLemmaKnowledge).all()
        }
        cards = _build_intro_cards(
            db_session, knowledge_by_id, set(new_ids) | set(rescue_ids)
        )
        card_ids = [c["lemma_id"] for c in cards]
        n_new = sum(1 for cid in card_ids if cid in new_ids)
        n_rescue = sum(1 for cid in card_ids if cid in rescue_ids)
        assert len(cards) <= 6
        assert n_rescue == 2, f"rescue starved: {card_ids}"
        assert n_new == 4


class TestContextDiversity:
    def test_least_shown_sentence_preferred(self, db_session):
        """When two sentences cover the same due word, prefer the less-shown one."""
        _seed_word(db_session, 1, "كتاب", "book",
                   stability=0.1, due_hours=-1, times_seen=5, times_correct=3)
        _seed_word(db_session, 2, "كبير", "big", due_hours=24)
        _seed_word(db_session, 3, "جديد", "new", due_hours=24)

        # Sentence shown many times
        _seed_sentence(db_session, 1, "هذا كتاب كبير", "this is a big book", 1,
                       [("هذا", None), ("كتاب", 1), ("كبير", 2)],
                       times_shown=10)

        # Sentence never shown
        _seed_sentence(db_session, 2, "كتاب جديد", "a new book", 1,
                       [("كتاب", 1), ("جديد", 3)],
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
        _seed_word(db_session, 2, "هذا", "this", due_hours=24)

        _seed_sentence(db_session, 1, "هذا كتاب", "this is a book", 1,
                       [("هذا", 2), ("كتاب", 1)],
                       times_shown=50)

        db_session.commit()

        result = build_session(db_session, limit=1, log_events=False)
        items = result["items"]
        assert len(items) >= 1
