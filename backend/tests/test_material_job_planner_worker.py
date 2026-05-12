from datetime import datetime, timedelta, timezone

from app.models import Lemma, UserLemmaKnowledge
from app.services.material_job_planner import plan_sentence_shards
from app.services.material_job_worker import process_material_job
from app.services.material_jobs import (
    STATUS_DONE,
    enqueue_material_job,
    lease_material_jobs,
)


def _now() -> datetime:
    return datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


def _seed_lemma(
    db_session,
    lemma_id: int,
    arabic: str,
    bare: str,
    gloss: str,
    *,
    state: str = "learning",
    due: datetime | None = None,
    word_category: str | None = None,
):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=bare,
        gloss_en=gloss,
        pos="noun",
        word_category=word_category,
    )
    db_session.add(lemma)
    ulk = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
    )
    if state == "acquiring":
        ulk.acquisition_next_due = due
    elif due is not None:
        ulk.fsrs_card_json = {"due": due.isoformat()}
    db_session.add(ulk)
    return lemma


def test_plan_sentence_shards_prioritizes_rescue_and_skips_inert_words(db_session):
    _seed_lemma(db_session, 1, "كِتَاب", "كتاب", "book", state="acquiring", due=_now())
    _seed_lemma(db_session, 2, "بَيْت", "بيت", "house", state="acquiring", due=_now() + timedelta(hours=1))
    _seed_lemma(db_session, 3, "قَلَم", "قلم", "pen", due=_now() + timedelta(hours=3))
    _seed_lemma(db_session, 4, "عَلِيّ", "علي", "Ali", due=_now(), word_category="proper_name")
    _seed_lemma(db_session, 5, "وَ", "و", "and", due=_now())
    db_session.commit()

    plan = plan_sentence_shards(
        db_session,
        sentence_budget=3,
        max_jobs=2,
        shard_size=2,
        count_per_word=1,
        now=_now(),
    )

    assert plan.budget == 3
    assert plan.planned_sentences == 3
    assert len(plan.shards) == 2
    planned_ids = [lid for shard in plan.shards for lid in shard.lemma_ids]
    assert planned_ids == [1, 2, 3]
    assert 4 not in planned_ids
    assert 5 not in planned_ids
    assert plan.shards[0].payload["count_per_word"] == 1
    assert plan.shards[0].priority == 0


def test_process_sentence_shard_job_completes_and_records_failures(db_session):
    _seed_lemma(db_session, 1, "كِتَاب", "كتاب", "book", state="acquiring", due=_now())
    _seed_lemma(db_session, 2, "بَيْت", "بيت", "house", state="acquiring", due=_now())
    db_session.commit()
    enqueue_material_job(
        db_session,
        kind="sentence_shard",
        payload={"lemma_ids": [1, 2], "count_per_word": 1},
        now=_now(),
    )
    job = lease_material_jobs(db_session, worker_id="worker-a", now=_now())[0]
    calls = []

    def fake_generator(lemma_ids, count_per_word, model):
        calls.append((lemma_ids, count_per_word, model))
        return {"generated": 1, "words_covered": 1, "words_failed": [2]}

    updated = process_material_job(
        db_session,
        job,
        model="test-model",
        generator=fake_generator,
    )

    assert calls == [([1, 2], 1, "test-model")]
    assert updated.status == STATUS_DONE
    assert updated.result_json["generated"] == 1
    failed = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=2).one()
    assert failed.generation_failed_count == 1
