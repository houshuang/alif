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


def _seed_lemma(db, *, form="βιβλίο", bare="βιβλιο", canonical=None, language_code="el") -> Lemma:
    lemma = Lemma(
        language_code=language_code, lemma_form=form, lemma_bare=bare, source="test",
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


def test_session_prefetch_suppresses_interaction_log(tmp_db, monkeypatch):
    """prefetch=true returns the same bundle shape but logs no session_built —
    so a background prefetch that may never be shown isn't counted as a session
    the learner did (mirrors Alif's prefetch flag)."""
    import app.routers.reviews as reviews_router

    calls: list[dict] = []
    monkeypatch.setattr(
        reviews_router, "log_interaction", lambda **kw: calls.append(kw)
    )

    client, _ = _client(tmp_db)
    try:
        r_normal = client.get("/api/reviews/session", params={"language_code": "el"})
        assert r_normal.status_code == 200
        assert set(r_normal.json().keys()) >= {"sentences", "intro_cards"}
        assert len(calls) == 1
        assert calls[0]["event"] == "session_built"

        r_prefetch = client.get(
            "/api/reviews/session", params={"language_code": "el", "prefetch": "true"}
        )
        assert r_prefetch.status_code == 200
        # Same shape, but no additional log row was written.
        assert r_prefetch.json().keys() == r_normal.json().keys()
        assert len(calls) == 1
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


def test_stats_scoped_by_language_code(tmp_db):
    """`/api/reviews/stats?language_code=` filters the acquisition pipeline by
    language. Regression for the 2026-05-25 review-screen empty-state leak:
    without this filter, the Box 1/2/3 counts shown in Latin mode included
    Greek's acquisition pipeline (UserLemmaKnowledge carries no language)."""
    client, factory = _client(tmp_db)
    try:
        now = datetime.now(timezone.utc)
        with factory() as db:
            for code, form in [("el", "ελ1"), ("el", "ελ2"), ("la", "la1")]:
                lemma = _seed_lemma(db, form=form, bare=form, language_code=code)
                db.add(UserLemmaKnowledge(
                    lemma_id=lemma.lemma_id, knowledge_state="acquiring",
                    acquisition_box=1, acquisition_next_due=now,
                    acquisition_started_at=now, entered_acquiring_at=now,
                    source="test",
                ))
            db.commit()

        # No filter: combined count (back-compat).
        r = client.get("/api/reviews/stats")
        assert r.status_code == 200
        assert r.json()["total_acquiring"] == 3

        # Per-language: scoped counts.
        r_el = client.get("/api/reviews/stats?language_code=el")
        assert r_el.status_code == 200
        assert r_el.json()["total_acquiring"] == 2

        r_la = client.get("/api/reviews/stats?language_code=la")
        assert r_la.status_code == 200
        assert r_la.json()["total_acquiring"] == 1
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


def test_experiment_intro_ack_stamps_field(tmp_db):
    """POST /api/reviews/experiment-intro-ack must stamp
    ``experiment_intro_shown_at`` so the working-memory gate and the
    intro-card dedup both fire on the next call."""
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            lemma = _seed_lemma(db, form="λόγος")
            db.commit()
            start_acquisition(db, lemma_id=lemma.lemma_id, source="test")
            db.commit()
            lemma_id = lemma.lemma_id

        r = client.post(
            "/api/reviews/experiment-intro-ack",
            json={"lemma_id": lemma_id, "session_id": "s1"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["lemma_id"] == lemma_id
        assert body["stamped"] is True

        with factory() as db:
            ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).one()
            assert ulk.experiment_intro_shown_at is not None
    finally:
        _cleanup()


def test_experiment_intro_ack_no_ulk_is_noop(tmp_db):
    """No ULK row → ack returns stamped=False rather than fabricating state."""
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            lemma = _seed_lemma(db, form="λόγος")
            db.commit()
            lemma_id = lemma.lemma_id

        r = client.post("/api/reviews/experiment-intro-ack", json={"lemma_id": lemma_id})
        assert r.status_code == 200
        assert r.json()["stamped"] is False
    finally:
        _cleanup()


def test_experiment_intro_ack_redirects_variants_to_canonical(tmp_db):
    """Ack on a variant lemma must stamp the canonical's ULK."""
    client, factory = _client(tmp_db)
    try:
        with factory() as db:
            canonical = _seed_lemma(db, form="C", bare="c")
            variant = _seed_lemma(db, form="V", bare="v", canonical=canonical.lemma_id)
            db.commit()
            start_acquisition(db, lemma_id=canonical.lemma_id, source="test")
            db.commit()
            variant_id = variant.lemma_id
            canonical_id = canonical.lemma_id

        r = client.post("/api/reviews/experiment-intro-ack", json={"lemma_id": variant_id})
        assert r.status_code == 200
        assert r.json()["lemma_id"] == canonical_id
        with factory() as db:
            ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=canonical_id).one()
            assert ulk.experiment_intro_shown_at is not None
    finally:
        _cleanup()


def test_session_intro_cards_skip_already_shown(tmp_db):
    """Once an intro card has been ack'd, the next session must not re-emit it."""
    client, factory = _client(tmp_db)
    try:
        from app.models import Sentence, SentenceWord

        with factory() as db:
            lemma = _seed_lemma(db, form="λόγος")
            db.commit()
            start_acquisition(db, lemma_id=lemma.lemma_id, source="test", due_immediately=True)
            sent = Sentence(
                language_code="el", text="λόγος καλός.",
                source="test", is_active=True,
                mappings_verified_at=datetime.now(timezone.utc),
            )
            db.add(sent)
            db.flush()
            db.add(SentenceWord(
                sentence_id=sent.id, position=0, surface_form="λόγος",
                lemma_id=lemma.lemma_id,
            ))
            db.commit()
            lemma_id = lemma.lemma_id

        r1 = client.get("/api/reviews/session", params={"language_code": "el"})
        assert r1.status_code == 200
        assert len(r1.json()["intro_cards"]) == 1

        r_ack = client.post(
            "/api/reviews/experiment-intro-ack",
            json={"lemma_id": lemma_id},
        )
        assert r_ack.status_code == 200

        r2 = client.get("/api/reviews/session", params={"language_code": "el"})
        assert r2.status_code == 200
        assert r2.json()["intro_cards"] == []
    finally:
        _cleanup()
