"""ReviewLog.event_type + graduation_tier are written at every review path,
so analytics are a direct query rather than fsrs_log_json reconstruction."""
from datetime import datetime, timedelta, timezone

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services import fsrs_service
from app.services.acquisition_service import start_acquisition, submit_acquisition_review


def _seed_lemma(db, *, form="βιβλίο", bare="βιβλιο", language_code="el") -> Lemma:
    lemma = Lemma(language_code=language_code, lemma_form=form, lemma_bare=bare, source="test")
    db.add(lemma)
    db.flush()
    return lemma


def _log_for(db, lemma_id) -> ReviewLog:
    return (
        db.query(ReviewLog)
        .filter_by(lemma_id=lemma_id)
        .order_by(ReviewLog.id.desc())
        .first()
    )


def test_tier0_graduation_logs_event_type_and_tier(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        start_acquisition(db, lemma_id=lemma.lemma_id, due_immediately=True, source="test")
        db.commit()
        submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        log = _log_for(db, lemma.lemma_id)
        assert log.event_type == "acquisition_review"
        assert log.graduation_tier == 0


def test_tier1_graduation_logs_tier_1(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        db.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id, knowledge_state="acquiring", acquisition_box=1,
            acquisition_next_due=datetime.now(timezone.utc) - timedelta(minutes=5),
            acquisition_started_at=datetime.now(timezone.utc),
            entered_acquiring_at=datetime.now(timezone.utc),
            source="test", times_seen=2, times_correct=2,
        ))
        db.commit()
        submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        log = _log_for(db, lemma.lemma_id)
        assert log.event_type == "acquisition_review"
        assert log.graduation_tier == 1


def test_non_graduating_acquisition_review_has_null_tier(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        # collateral, not due → counts exposure but does not graduate
        start_acquisition(db, lemma_id=lemma.lemma_id, due_immediately=False, source="collateral")
        db.commit()
        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        assert result["graduated"] is not True
        log = _log_for(db, lemma.lemma_id)
        assert log.event_type == "acquisition_review"
        assert log.graduation_tier is None


def test_scaffold_confirmation_event_type(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        db.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id, knowledge_state="known", source="test",
            knowledge_origin="pre_known",
        ))
        db.commit()
        fsrs_service.record_scaffold_confirmation(db, lemma.lemma_id, rating_int=3)
        db.commit()
        log = _log_for(db, lemma.lemma_id)
        assert log.event_type == "scaffold_confirmation"
        assert log.graduation_tier is None


def test_fsrs_review_event_type(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        # graduate via Tier 0 to mint an FSRS card, then a normal FSRS review.
        start_acquisition(db, lemma_id=lemma.lemma_id, due_immediately=True, source="test")
        db.commit()
        submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        db.commit()
        fsrs_service.submit_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        db.commit()
        log = _log_for(db, lemma.lemma_id)
        assert log.event_type == "fsrs_review"
        assert log.graduation_tier is None
