"""Integration tests for the reviews router.

Spins up a FastAPI TestClient and overrides ``get_db`` to point at the
per-test SQLite from the ``tmp_db`` fixture. Verifies routing decisions
(acquisition vs FSRS), idempotency, variant redirect, and the due/stats
endpoints.
"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import Lemma, UserLemmaKnowledge
from app.services.acquisition_service import start_acquisition
from app.services.fsrs_service import create_new_card


def _client(tmp_db) -> tuple[TestClient, callable]:
    """Build a TestClient bound to the per-test DB. Returns (client, get_session)."""
    test_session_factory = tmp_db

    def _override():
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app), test_session_factory


def _cleanup():
    app.dependency_overrides.clear()


def _seed_lemma(db, *, form="βιβλίο", bare="βιβλιο", canonical=None) -> Lemma:
    lemma = Lemma(
        language_code="el", lemma_form=form, lemma_bare=bare, source="test",
        canonical_lemma_id=canonical,
    )
    db.add(lemma)
    db.flush()
    return lemma


def test_introduce_starts_acquisition(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            lemma = _seed_lemma(db)
            db.commit()
            lemma_id = lemma.lemma_id

        r = client.post("/api/reviews/introduce", json={
            "lemma_id": lemma_id, "source": "study", "due_immediately": True,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "acquiring"
        assert body["acquisition_box"] == 1
        assert body["next_due"]
    finally:
        _cleanup()


def test_submit_routes_acquisition_review_to_acquisition_pipeline(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            lemma = _seed_lemma(db)
            db.commit()
            lemma_id = lemma.lemma_id

        # Enrol via the API itself
        client.post("/api/reviews/introduce", json={
            "lemma_id": lemma_id, "due_immediately": True,
        })
        # First correct review → Tier 0 graduation
        r = client.post("/api/reviews/submit", json={
            "lemma_id": lemma_id, "rating": 3,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["graduated"] is True
        assert body["new_state"] == "learning"
    finally:
        _cleanup()


def test_submit_routes_fsrs_review_for_learning_state(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            lemma = _seed_lemma(db)
            ulk = UserLemmaKnowledge(
                lemma_id=lemma.lemma_id,
                knowledge_state="learning",
                fsrs_card_json=create_new_card(),
                source="test",
            )
            db.add(ulk)
            db.commit()
            lemma_id = lemma.lemma_id

        r = client.post("/api/reviews/submit", json={
            "lemma_id": lemma_id, "rating": 3, "review_mode": "reading",
        })
        assert r.status_code == 200
        body = r.json()
        # FSRS path: no acquisition_box; graduated is None for FSRS reviews
        assert body["acquisition_box"] is None
        assert body["graduated"] is None
        assert body["new_state"] in {"learning", "known", "lapsed"}
    finally:
        _cleanup()


def test_submit_is_idempotent_via_client_review_id(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            lemma = _seed_lemma(db)
            ulk = UserLemmaKnowledge(
                lemma_id=lemma.lemma_id,
                knowledge_state="learning",
                fsrs_card_json=create_new_card(),
                source="test",
            )
            db.add(ulk)
            db.commit()
            lemma_id = lemma.lemma_id

        payload = {
            "lemma_id": lemma_id, "rating": 3, "client_review_id": "uuid-abc",
        }
        r1 = client.post("/api/reviews/submit", json=payload)
        # Same client_review_id, different rating — must be ignored
        r2 = client.post("/api/reviews/submit", json={**payload, "rating": 1})
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json().get("duplicate") is False
        assert r2.json()["duplicate"] is True
    finally:
        _cleanup()


def test_submit_redirects_variant_to_canonical(tmp_db):
    """A submit for a variant lemma_id should produce all bookkeeping on the
    canonical's row, not the variant's."""
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            canonical = _seed_lemma(db, form="C", bare="c")
            variant = _seed_lemma(db, form="V", bare="v", canonical=canonical.lemma_id)
            db.commit()
            start_acquisition(db, lemma_id=canonical.lemma_id, due_immediately=True)
            db.commit()
            canonical_id, variant_id = canonical.lemma_id, variant.lemma_id

        r = client.post("/api/reviews/submit", json={"lemma_id": variant_id, "rating": 3})
        assert r.status_code == 200
        # Response should reference the canonical
        assert r.json()["lemma_id"] == canonical_id

        with factory() as db:
            canonical_ulks = db.query(UserLemmaKnowledge).filter_by(lemma_id=canonical_id).all()
            variant_ulks = db.query(UserLemmaKnowledge).filter_by(lemma_id=variant_id).all()
            assert len(canonical_ulks) == 1
            assert variant_ulks == []
            assert canonical_ulks[0].times_seen == 1
    finally:
        _cleanup()


def test_due_returns_acquisition_first_then_fsrs(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            acq_lemma = _seed_lemma(db, form="acq", bare="acq")
            fsrs_lemma = _seed_lemma(db, form="fsrs", bare="fsrs")
            now = datetime.now(timezone.utc)
            db.add(UserLemmaKnowledge(
                lemma_id=acq_lemma.lemma_id, knowledge_state="acquiring",
                acquisition_box=1,
                acquisition_next_due=now - timedelta(hours=1),
                acquisition_started_at=now,
                entered_acquiring_at=now, source="test",
            ))
            # FSRS card due 30 minutes ago
            past_due_card = create_new_card()
            past_due_card["due"] = (now - timedelta(minutes=30)).isoformat()
            db.add(UserLemmaKnowledge(
                lemma_id=fsrs_lemma.lemma_id, knowledge_state="learning",
                fsrs_card_json=past_due_card, source="test",
            ))
            db.commit()

        r = client.get("/api/reviews/due", params={"language_code": "el"})
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) >= 2
        # First row should be the acquiring lemma
        assert rows[0]["state"] == "acquiring"
        assert rows[0]["acquisition_box"] == 1
        # Followed by the FSRS-due learning lemma
        assert any(r["state"] == "learning" for r in rows)
    finally:
        _cleanup()


def test_due_unknown_language_400(tmp_db):
    client, _ = _client(tmp_db)
    try:
        r = client.get("/api/reviews/due", params={"language_code": "xx"})
        assert r.status_code == 400
    finally:
        _cleanup()


def test_stats_reflects_acquisition_distribution(tmp_db):
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            for box in (1, 1, 2, 3):
                lemma = _seed_lemma(db, form=f"b{box}-{id(box)}", bare=f"b{box}-{id(box)}")
                db.add(UserLemmaKnowledge(
                    lemma_id=lemma.lemma_id, knowledge_state="acquiring",
                    acquisition_box=box,
                    acquisition_next_due=datetime.now(timezone.utc),
                    acquisition_started_at=datetime.now(timezone.utc),
                    entered_acquiring_at=datetime.now(timezone.utc),
                    source="test",
                ))
            db.commit()

        r = client.get("/api/reviews/stats")
        assert r.status_code == 200
        s = r.json()
        assert s["total_acquiring"] == 4
        assert s["box_1"] == 2
        assert s["box_2"] == 1
        assert s["box_3"] == 1
    finally:
        _cleanup()


def test_submit_reactivates_suspended_word(tmp_db):
    """A submit on a suspended (leech) lemma should auto-reactivate before
    applying the review — UX should never silently swallow a learner action.

    Note: we pre-seed enough recent ReviewLog rows that the leech detector
    uses sliding-window accuracy, not the cumulative fallback. Otherwise
    the cumulative leech-detection signal could re-suspend the word
    immediately (intended Alif behavior — see leech_service.is_leech).
    """
    from app.models import ReviewLog
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            lemma = _seed_lemma(db)
            ulk = UserLemmaKnowledge(
                lemma_id=lemma.lemma_id, knowledge_state="suspended",
                leech_suspended_at=datetime.now(timezone.utc),
                leech_count=1, source="test",
                times_seen=8, times_correct=6,  # 75% cumulative — past failures
            )
            db.add(ulk)
            # 8 recent reviews, mostly correct — sliding window says "not a leech"
            for i in range(8):
                db.add(ReviewLog(
                    lemma_id=lemma.lemma_id,
                    rating=3,
                    reviewed_at=datetime.now(timezone.utc) - timedelta(seconds=i),
                    review_mode="reading",
                ))
            db.commit()
            lemma_id = lemma.lemma_id

        r = client.post("/api/reviews/submit", json={"lemma_id": lemma_id, "rating": 3})
        assert r.status_code == 200
        with factory() as db:
            refreshed = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).one()
            assert refreshed.knowledge_state != "suspended"
    finally:
        _cleanup()
