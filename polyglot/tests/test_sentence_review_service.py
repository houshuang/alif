"""Tests for sentence-level review submission.

Covers Hard Invariant FOUNDATIONAL ("every word earns credit"), #9 (canonical
scheduling), #11 (client_review_id idempotency), #2 (reviewability gate),
function-word + proper-name filtering, encountered → acquiring auto-promotion,
the daily-cap deferred path, acquiring-vs-FSRS routing, the post-submit
ReviewLog tagging (credit_type / was_confused), and the undo restore path.

Tests construct Sentence/SentenceWord rows directly rather than going through
the sentence_harvest path — the latter requires a Page with PageWord rows and
a quality-gate pass, which is overkill for this unit. The harvest pipeline is
covered separately in test_sentence_harvest.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    Lemma,
    ReviewLog,
    Sentence,
    SentenceReviewLog,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.acquisition_service import start_acquisition
from app.services.fsrs_service import create_new_card
from app.services.sentence_review_service import (
    submit_sentence_review,
    undo_sentence_review,
)


# ─── Helpers ───────────────────────────────────────────────────────────────


def _seed_lemma(
    db,
    *,
    form: str,
    bare: str | None = None,
    language_code: str = "el",
    canonical: int | None = None,
    word_category: str | None = None,
) -> Lemma:
    lemma = Lemma(
        language_code=language_code,
        lemma_form=form,
        lemma_bare=bare if bare is not None else form,
        source="test",
        canonical_lemma_id=canonical,
        word_category=word_category,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _seed_sentence(
    db,
    *,
    lemma_surfaces: list[tuple[int, str]],
    language_code: str = "el",
    text: str | None = None,
    verified: bool = True,
    target_lemma_id: int | None = None,
) -> Sentence:
    """Create a Sentence + SentenceWord rows. lemma_surfaces is (lemma_id, surface)."""
    sentence = Sentence(
        language_code=language_code,
        text=text or " ".join(s for _, s in lemma_surfaces),
        source="test",
        target_lemma_id=target_lemma_id,
        mappings_verified_at=datetime.now(timezone.utc) if verified else None,
    )
    db.add(sentence)
    db.flush()
    for i, (lemma_id, surface) in enumerate(lemma_surfaces):
        db.add(SentenceWord(
            sentence_id=sentence.id,
            position=i,
            surface_form=surface,
            lemma_id=lemma_id,
        ))
    db.flush()
    return sentence


def _seed_acquiring(db, lemma_id: int, *, box: int = 1, times_seen: int = 0) -> UserLemmaKnowledge:
    now = datetime.now(timezone.utc)
    ulk = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state="acquiring",
        acquisition_box=box,
        acquisition_next_due=now,
        acquisition_started_at=now,
        entered_acquiring_at=now,
        source="test",
        times_seen=times_seen,
    )
    db.add(ulk)
    db.flush()
    return ulk


def _seed_learning(db, lemma_id: int) -> UserLemmaKnowledge:
    ulk = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state="learning",
        fsrs_card_json=create_new_card(),
        source="test",
    )
    db.add(ulk)
    db.flush()
    return ulk


# ─── Comprehension signal → rating mapping ─────────────────────────────────


def test_understood_rates_all_content_lemmas_3(tmp_db):
    with tmp_db() as db:
        a = _seed_lemma(db, form="κόσμος", bare="κοσμος")
        b = _seed_lemma(db, form="βιβλίο", bare="βιβλιο")
        _seed_learning(db, a.lemma_id)
        _seed_learning(db, b.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(a.lemma_id, "κόσμος"), (b.lemma_id, "βιβλίο")])
        db.commit()
        sid = sentence.id

    with tmp_db() as db:
        result = submit_sentence_review(
            db,
            sentence_id=sid,
            comprehension_signal="understood",
        )
        assert result["duplicate"] is False
        ratings = {wr["lemma_id"]: wr["rating"] for wr in result["word_results"]}
        assert len(ratings) == 2
        assert all(r == 3 for r in ratings.values())


def test_no_idea_rates_all_content_lemmas_1(tmp_db):
    with tmp_db() as db:
        a = _seed_lemma(db, form="κόσμος", bare="κοσμος")
        b = _seed_lemma(db, form="βιβλίο", bare="βιβλιο")
        _seed_learning(db, a.lemma_id)
        _seed_learning(db, b.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(a.lemma_id, "κόσμος"), (b.lemma_id, "βιβλίο")])
        db.commit()
        sid = sentence.id

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="no_idea")
        ratings = {wr["lemma_id"]: wr["rating"] for wr in result["word_results"]}
        assert all(r == 1 for r in ratings.values())


def test_partial_assigns_missed_confused_and_rest(tmp_db):
    with tmp_db() as db:
        miss = _seed_lemma(db, form="missed", bare="missed")
        conf = _seed_lemma(db, form="confused", bare="confused")
        rest = _seed_lemma(db, form="rest", bare="rest")
        for l in (miss, conf, rest):
            _seed_learning(db, l.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[
            (miss.lemma_id, "m"), (conf.lemma_id, "c"), (rest.lemma_id, "r"),
        ])
        db.commit()
        sid = sentence.id
        miss_id, conf_id, rest_id = miss.lemma_id, conf.lemma_id, rest.lemma_id

    with tmp_db() as db:
        result = submit_sentence_review(
            db,
            sentence_id=sid,
            comprehension_signal="partial",
            missed_lemma_ids=[miss_id],
            confused_lemma_ids=[conf_id],
        )
        ratings = {wr["lemma_id"]: wr["rating"] for wr in result["word_results"]}
        assert ratings[miss_id] == 1
        assert ratings[conf_id] == 2
        assert ratings[rest_id] == 3

    with tmp_db() as db:
        logs = {l.lemma_id: l for l in db.query(ReviewLog).all()}
        assert logs[conf_id].was_confused is True
        assert logs[miss_id].was_confused is False
        assert logs[rest_id].was_confused is False


# ─── Filtering: function words / proper names / suspended ──────────────────


def test_function_word_skipped_by_category(tmp_db):
    with tmp_db() as db:
        content = _seed_lemma(db, form="κόσμος", bare="κοσμος")
        fw = _seed_lemma(db, form="και", bare="και", word_category="function_word")
        _seed_learning(db, content.lemma_id)
        _seed_learning(db, fw.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(content.lemma_id, "κόσμος"), (fw.lemma_id, "και")])
        db.commit()
        sid, content_id, fw_id = sentence.id, content.lemma_id, fw.lemma_id

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")
        lemma_ids = {wr["lemma_id"] for wr in result["word_results"]}
        assert lemma_ids == {content_id}
        # No ReviewLog row for the function word
        fw_logs = db.query(ReviewLog).filter_by(lemma_id=fw_id).all()
        assert fw_logs == []


def test_function_word_skipped_by_bare_set(tmp_db):
    """`και` is in EL_FUNCTION_WORDS even without word_category being set."""
    with tmp_db() as db:
        content = _seed_lemma(db, form="κόσμος", bare="κοσμος")
        fw = _seed_lemma(db, form="και", bare="και")  # no word_category — only the set match
        _seed_learning(db, content.lemma_id)
        _seed_learning(db, fw.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(content.lemma_id, "κόσμος"), (fw.lemma_id, "και")])
        db.commit()
        sid, fw_id = sentence.id, fw.lemma_id

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")
        assert all(wr["lemma_id"] != fw_id for wr in result["word_results"])


def test_proper_name_skipped(tmp_db):
    with tmp_db() as db:
        content = _seed_lemma(db, form="πόλη", bare="πολη")
        name = _seed_lemma(db, form="Αθήνα", bare="αθηνα", word_category="proper_name")
        _seed_learning(db, content.lemma_id)
        _seed_learning(db, name.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(content.lemma_id, "πόλη"), (name.lemma_id, "Αθήνα")])
        db.commit()
        sid, name_id = sentence.id, name.lemma_id

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")
        assert all(wr["lemma_id"] != name_id for wr in result["word_results"])
        assert db.query(ReviewLog).filter_by(lemma_id=name_id).all() == []


def test_suspended_lemma_skipped(tmp_db):
    with tmp_db() as db:
        good = _seed_lemma(db, form="g", bare="g")
        suspended_l = _seed_lemma(db, form="s", bare="s")
        _seed_learning(db, good.lemma_id)
        db.add(UserLemmaKnowledge(
            lemma_id=suspended_l.lemma_id,
            knowledge_state="suspended",
            leech_suspended_at=datetime.now(timezone.utc),
            leech_count=1,
            source="test",
        ))
        sentence = _seed_sentence(db, lemma_surfaces=[(good.lemma_id, "g"), (suspended_l.lemma_id, "s")])
        db.commit()
        sid, suspended_id = sentence.id, suspended_l.lemma_id

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")
        assert all(wr["lemma_id"] != suspended_id for wr in result["word_results"])


# ─── Canonical resolution ──────────────────────────────────────────────────


def test_variant_credit_goes_to_canonical(tmp_db):
    """A SentenceWord pointing at a variant must produce credit on the
    canonical's ULK, never the variant's. Defense-in-depth for Invariant #9."""
    with tmp_db() as db:
        canon = _seed_lemma(db, form="canon", bare="canon")
        variant = _seed_lemma(db, form="variant", bare="variant", canonical=canon.lemma_id)
        _seed_learning(db, canon.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(variant.lemma_id, "v")])
        db.commit()
        sid, canon_id, variant_id = sentence.id, canon.lemma_id, variant.lemma_id

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")
        # Credit on canonical
        assert result["word_results"][0]["lemma_id"] == canon_id
        # No ULK appeared on the variant
        assert db.query(UserLemmaKnowledge).filter_by(lemma_id=variant_id).all() == []


def test_multi_hop_variant_chain_resolves_to_root(tmp_db):
    """A→B→C variant chain where only A is in the sentence must resolve all
    the way to C — not stop at B (which would break the local logic that the
    pre-loaded-map shortcut allowed in earlier drafts of this service)."""
    with tmp_db() as db:
        root = _seed_lemma(db, form="root", bare="root")
        mid = _seed_lemma(db, form="mid", bare="mid", canonical=root.lemma_id)
        leaf = _seed_lemma(db, form="leaf", bare="leaf", canonical=mid.lemma_id)
        _seed_learning(db, root.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(leaf.lemma_id, "l")])
        db.commit()
        sid, root_id, mid_id, leaf_id = sentence.id, root.lemma_id, mid.lemma_id, leaf.lemma_id

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")
        # Credit must land on the root canonical, not the intermediate
        assert result["word_results"][0]["lemma_id"] == root_id
        # Neither intermediate variant got a ULK
        assert db.query(UserLemmaKnowledge).filter_by(lemma_id=mid_id).all() == []
        assert db.query(UserLemmaKnowledge).filter_by(lemma_id=leaf_id).all() == []
        # Root's ReviewLog properly tagged
        log = db.query(ReviewLog).filter_by(lemma_id=root_id).one()
        assert log.credit_type == "collateral"


# ─── Auto-introduction (FOUNDATIONAL invariant) ────────────────────────────


def test_encountered_lemma_auto_promoted_and_credited(tmp_db):
    """A lemma in 'encountered' state must transition to 'acquiring' and earn
    a review credit on the same sentence appearance."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="enc", bare="enc")
        db.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="encountered",
            source="test",
        ))
        sentence = _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, "e")])
        db.commit()
        sid, lemma_id = sentence.id, lemma.lemma_id

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")
        assert len(result["word_results"]) == 1
        # Tier 0 graduation: first-correct review → straight to FSRS 'learning'
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).one()
        assert ulk.knowledge_state in ("learning", "acquiring")
        # If it didn't graduate, it must be in acquiring (NOT encountered)
        assert ulk.knowledge_state != "encountered"


def test_unknown_lemma_auto_creates_ulk(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="unk", bare="unk")
        sentence = _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, "u")])
        db.commit()
        sid, lemma_id = sentence.id, lemma.lemma_id
        assert db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).first() is None

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")
        assert len(result["word_results"]) == 1
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).one()
        # Either graduated to learning (Tier 0) or sitting in acquiring
        assert ulk.knowledge_state in ("learning", "acquiring")


def test_daily_cap_deferred_bumps_total_encounters_but_no_review(tmp_db, monkeypatch):
    """When the daily cap is hit, encountered lemmas must NOT get a review
    log but their `total_encounters` must increment."""
    # Force the cap to zero so any new acquisition is deferred.
    import app.services.acquisition_service as acq
    monkeypatch.setattr(acq, "DAILY_INTRO_CAP", 0)
    # _recovery_mode_intro_budget returns DAILY_INTRO_CAP for the non-overloaded
    # branch, so patching the constant is sufficient.

    with tmp_db() as db:
        lemma = _seed_lemma(db, form="enc", bare="enc")
        db.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="encountered",
            source="test",
            total_encounters=0,
        ))
        sentence = _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, "e")])
        db.commit()
        sid, lemma_id = sentence.id, lemma.lemma_id

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")
        # Cap-deferred → no word_results row
        assert result["word_results"] == []
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).one()
        assert ulk.knowledge_state == "encountered"
        assert ulk.total_encounters == 1
        # No FSRS-side ReviewLog row either
        assert db.query(ReviewLog).filter_by(lemma_id=lemma_id).all() == []


# ─── Routing: acquiring vs FSRS ────────────────────────────────────────────


def test_acquiring_lemma_routes_through_acquisition(tmp_db):
    """An acquiring lemma should hit Tier 0 graduation on first correct review."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="acq", bare="acq")
        _seed_acquiring(db, lemma.lemma_id, box=1, times_seen=0)
        sentence = _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, "a")])
        db.commit()
        sid, lemma_id = sentence.id, lemma.lemma_id

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")
        wr = result["word_results"][0]
        assert wr["new_state"] == "learning"  # Tier 0 graduated
        # ReviewLog is_acquisition flag indicates acquisition path was used
        log = db.query(ReviewLog).filter_by(lemma_id=lemma_id).one()
        assert log.is_acquisition is True


def test_learning_lemma_routes_through_fsrs(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="lrn", bare="lrn")
        _seed_learning(db, lemma.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, "l")])
        db.commit()
        sid, lemma_id = sentence.id, lemma.lemma_id

    with tmp_db() as db:
        result = submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")
        log = db.query(ReviewLog).filter_by(lemma_id=lemma_id).one()
        assert log.is_acquisition is False
        assert result["word_results"][0]["new_state"] in {"learning", "known", "lapsed"}


# ─── Tagging + sentence-level audit row ────────────────────────────────────


def test_credit_type_primary_vs_collateral_is_tagged(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="tgt", bare="tgt")
        collat = _seed_lemma(db, form="col", bare="col")
        _seed_learning(db, target.lemma_id)
        _seed_learning(db, collat.lemma_id)
        sentence = _seed_sentence(
            db,
            lemma_surfaces=[(target.lemma_id, "t"), (collat.lemma_id, "c")],
            target_lemma_id=target.lemma_id,
        )
        db.commit()
        sid, target_id, collat_id = sentence.id, target.lemma_id, collat.lemma_id

    with tmp_db() as db:
        submit_sentence_review(
            db,
            sentence_id=sid,
            comprehension_signal="understood",
            primary_lemma_id=target_id,
        )
        target_log = db.query(ReviewLog).filter_by(lemma_id=target_id).one()
        collat_log = db.query(ReviewLog).filter_by(lemma_id=collat_id).one()
        assert target_log.credit_type == "primary"
        assert collat_log.credit_type == "collateral"
        # sentence_id stamped on every row
        assert target_log.sentence_id == sid
        assert collat_log.sentence_id == sid


def test_sentence_review_log_row_created(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="x", bare="x")
        _seed_learning(db, lemma.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, "x")])
        db.commit()
        sid = sentence.id

    with tmp_db() as db:
        submit_sentence_review(
            db,
            sentence_id=sid,
            comprehension_signal="partial",
            response_ms=1500,
            session_id="sess-1",
        )
        srl = db.query(SentenceReviewLog).filter_by(sentence_id=sid).one()
        assert srl.comprehension == "partial"
        assert srl.response_ms == 1500
        assert srl.session_id == "sess-1"
        assert srl.review_mode == "reading"
        sentence = db.query(Sentence).filter_by(id=sid).one()
        assert sentence.times_shown == 1
        assert sentence.last_reading_shown_at is not None
        assert sentence.last_reading_comprehension == "partial"


# ─── Idempotency ───────────────────────────────────────────────────────────


def test_client_review_id_idempotency(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="x", bare="x")
        _seed_learning(db, lemma.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, "x")])
        db.commit()
        sid, lemma_id = sentence.id, lemma.lemma_id

    with tmp_db() as db:
        r1 = submit_sentence_review(
            db, sentence_id=sid, comprehension_signal="understood",
            client_review_id="abc-uuid",
        )
        assert r1["duplicate"] is False
        # Replay
        r2 = submit_sentence_review(
            db, sentence_id=sid, comprehension_signal="no_idea",
            client_review_id="abc-uuid",
        )
        assert r2["duplicate"] is True

    with tmp_db() as db:
        # Exactly one ReviewLog and one SentenceReviewLog should exist
        assert db.query(ReviewLog).filter_by(lemma_id=lemma_id).count() == 1
        assert db.query(SentenceReviewLog).filter_by(sentence_id=sid).count() == 1


# ─── Reviewability gate ────────────────────────────────────────────────────


def test_unverified_sentence_rejected_by_service(tmp_db):
    """Service-level guard. Router has its own 400."""
    import pytest
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="x", bare="x")
        _seed_learning(db, lemma.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, "x")], verified=False)
        db.commit()
        sid = sentence.id

    with tmp_db() as db:
        with pytest.raises(ValueError, match="mappings_verified_at"):
            submit_sentence_review(db, sentence_id=sid, comprehension_signal="understood")


# ─── Router integration ───────────────────────────────────────────────────


def _client(tmp_db):
    session_factory = tmp_db

    def _override():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app), session_factory


def test_router_rejects_unverified_sentence_with_400(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            lemma = _seed_lemma(db, form="x", bare="x")
            _seed_learning(db, lemma.lemma_id)
            sentence = _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, "x")], verified=False)
            db.commit()
            sid = sentence.id

        r = client.post("/api/reviews/submit-sentence", json={
            "sentence_id": sid,
            "comprehension_signal": "understood",
        })
        assert r.status_code == 400
        assert "mappings_verified_at" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_router_404_on_missing_sentence(tmp_db):
    client, _ = _client(tmp_db)
    try:
        r = client.post("/api/reviews/submit-sentence", json={
            "sentence_id": 99999,
            "comprehension_signal": "understood",
        })
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


# ─── Undo ──────────────────────────────────────────────────────────────────


def test_undo_restores_pre_state(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="x", bare="x")
        # Pre-state: learning with a specific card
        original_card = create_new_card()
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="learning",
            fsrs_card_json=original_card,
            times_seen=3,
            times_correct=2,
            source="test",
        )
        db.add(ulk)
        sentence = _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, "x")])
        db.commit()
        sid, lemma_id = sentence.id, lemma.lemma_id

    with tmp_db() as db:
        submit_sentence_review(
            db, sentence_id=sid, comprehension_signal="no_idea",
            client_review_id="undo-test",
        )
        # State changed
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).one()
        assert ulk.times_seen == 4

    with tmp_db() as db:
        result = undo_sentence_review(db, client_review_id="undo-test")
        assert result["undone"] is True
        assert result["reviews_removed"] == 1
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).one()
        assert ulk.times_seen == 3
        assert ulk.times_correct == 2
        # ReviewLog deleted
        assert db.query(ReviewLog).filter_by(lemma_id=lemma_id).all() == []
        # SentenceReviewLog deleted, times_shown decremented
        assert db.query(SentenceReviewLog).filter_by(sentence_id=sid).all() == []
        s = db.query(Sentence).filter_by(id=sid).one()
        assert s.times_shown == 0


def test_undo_idempotent_returns_false_on_replay(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="x", bare="x")
        _seed_learning(db, lemma.lemma_id)
        sentence = _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, "x")])
        db.commit()
        sid = sentence.id

    with tmp_db() as db:
        submit_sentence_review(
            db, sentence_id=sid, comprehension_signal="understood",
            client_review_id="undo-replay",
        )

    with tmp_db() as db:
        r1 = undo_sentence_review(db, client_review_id="undo-replay")
        assert r1["undone"] is True
        r2 = undo_sentence_review(db, client_review_id="undo-replay")
        assert r2["undone"] is False
