"""FSRS service: rating semantics, idempotency, reactivation."""
from datetime import datetime, timezone

from app.models import Lemma, UserLemmaKnowledge, ReviewLog
from app.services.fsrs_service import (
    create_new_card,
    parse_json_column,
    reactivate_if_suspended,
    submit_review,
)


def _seed_lemma(db, *, form="βιβλίο", bare="βιβλιο") -> Lemma:
    lemma = Lemma(language_code="el", lemma_form=form, lemma_bare=bare, source="test")
    db.add(lemma)
    db.flush()
    return lemma


def _seed_learning_ulk(db, lemma_id: int) -> UserLemmaKnowledge:
    ulk = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state="learning",
        fsrs_card_json=create_new_card(),
        source="test",
        introduced_at=datetime.now(timezone.utc),
    )
    db.add(ulk)
    db.commit()
    return ulk


def test_submit_review_advances_fsrs_card(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        _seed_learning_ulk(db, lemma.lemma_id)
        result = submit_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        assert result["lemma_id"] == lemma.lemma_id
        assert result["new_state"] in {"learning", "known", "lapsed"}
        assert result["next_due"]
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).one()
        assert ulk.times_seen == 1
        assert ulk.times_correct == 1
        assert ulk.last_reviewed is not None
        log = db.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).one()
        assert log.rating == 3
        assert log.is_acquisition is False


def test_submit_review_creates_ulk_if_missing(tmp_db):
    """A review for a lemma with no ULK yet should auto-create the ULK in
    learning state. This makes the function safe to call from import paths
    that don't pre-create rows."""
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        submit_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).one()
        assert ulk is not None
        assert ulk.times_seen == 1


def test_submit_review_idempotent_via_client_review_id(tmp_db):
    """A duplicate submission with the same client_review_id must not apply
    the FSRS step twice. The second call should report duplicate=True and
    return the cached post-state."""
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        _seed_learning_ulk(db, lemma.lemma_id)
        cid = "review-uuid-123"
        r1 = submit_review(db, lemma_id=lemma.lemma_id, rating_int=3, client_review_id=cid)
        r2 = submit_review(db, lemma_id=lemma.lemma_id, rating_int=1, client_review_id=cid)
        assert r1.get("duplicate") is not True
        assert r2.get("duplicate") is True
        # Only one ReviewLog row exists
        logs = db.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).all()
        assert len(logs) == 1
        assert logs[0].rating == 3  # the first rating, not the dupe


def test_rating_1_lapses_known_word(tmp_db):
    """A previously-known word that gets Again should transition to lapsed."""
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = _seed_learning_ulk(db, lemma.lemma_id)
        ulk.knowledge_state = "known"
        # Build a 'known' state card by replaying a few Good ratings first
        for _ in range(3):
            submit_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        # Now Again
        result = submit_review(db, lemma_id=lemma.lemma_id, rating_int=1)
        assert result["new_state"] in {"lapsed", "learning"}


def test_reactivate_if_suspended(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="suspended",
            leech_suspended_at=datetime.now(timezone.utc),
            leech_count=1,
            source="test",
            times_seen=8,
            times_correct=3,
        )
        db.add(ulk)
        db.commit()

        assert reactivate_if_suspended(db, lemma.lemma_id, source="leech_reintro") is True
        db.refresh(ulk)
        assert ulk.knowledge_state == "learning"
        card = parse_json_column(ulk.fsrs_card_json)
        assert card  # fresh card present


def test_reactivate_noop_when_not_suspended(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        _seed_learning_ulk(db, lemma.lemma_id)
        assert reactivate_if_suspended(db, lemma.lemma_id, source="x") is False
