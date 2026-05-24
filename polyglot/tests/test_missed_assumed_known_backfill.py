import json
from datetime import datetime, timezone

from app.models import Lemma, ReviewLog, Sentence, UserLemmaKnowledge
from scripts.backfill_missed_assumed_known import backfill_from_logs


def test_backfill_missed_assumed_known_from_interaction_logs(tmp_db, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with tmp_db() as db:
        lemma = Lemma(language_code="el", lemma_form="γνωστός", lemma_bare="γνωστος")
        db.add(lemma)
        db.flush()
        db.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="known",
            fsrs_card_json=None,
            knowledge_origin="pre_known",
            total_encounters=2,
        ))
        sentence = Sentence(
            language_code="el",
            text="γνωστός",
            source="test",
            mappings_verified_at=datetime.now(timezone.utc),
        )
        db.add(sentence)
        db.commit()
        lemma_id = lemma.lemma_id
        sentence_id = sentence.id

    event = {
        "ts": "2026-05-20T12:00:00+00:00",
        "event": "sentence_review",
        "language_code": "el",
        "sentence_id": sentence_id,
        "comprehension_signal": "partial",
        "missed_lemma_ids": [lemma_id],
        "word_results": [],
    }
    (log_dir / "interactions_2026-05-20.jsonl").write_text(
        json.dumps(event, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with tmp_db() as db:
        dry = backfill_from_logs(
            db,
            log_dir=log_dir,
            language_code="el",
            apply_changes=False,
        )
        assert dry["eligible"] == 1
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).one()
        assert ulk.knowledge_state == "known"

    with tmp_db() as db:
        applied = backfill_from_logs(
            db,
            log_dir=log_dir,
            language_code="el",
            apply_changes=True,
        )
        assert applied["updated"] == 1
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).one()
        assert ulk.knowledge_state == "acquiring"
        assert ulk.acquisition_box == 1
        assert ulk.first_failed_at is not None
        assert ulk.failure_count == 1
        assert ulk.total_encounters == 3

        log = db.query(ReviewLog).filter_by(lemma_id=lemma_id).one()
        assert log.rating == 1
        assert log.is_acquisition is True
        assert log.context == "backfill_missed_assumed_known"

    with tmp_db() as db:
        rerun = backfill_from_logs(
            db,
            log_dir=log_dir,
            language_code="el",
            apply_changes=True,
        )
        assert rerun["updated"] == 0
