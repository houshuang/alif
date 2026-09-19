"""Attention choices must survive every automatic route into scheduling."""
import copy
from datetime import datetime, timedelta, timezone
import pytest
from app.models import Lemma, UserLemmaKnowledge, ReviewLog, Sentence, SentenceWord, WordReviewEvidence
from app.services.attention_policy import set_disposition, maintenance_clause
from app.services.acquisition_service import start_acquisition, get_acquisition_due, recovery_status, submit_acquisition_review
from app.services.fsrs_service import submit_review
from app.services.frequency_lanes import due_lane_snapshot
from app.services.cohort_service import get_focus_cohort
from app.services.leech_service import check_leech_reintroductions
from app.services.word_selector import select_next_words, introduce_word
from app.services.sentence_eligibility import reviewable_sentence_clauses
from app.services.pipeline_tiers import compute_word_tiers
from tests.test_sentence_review import _seed_word, _seed_sentence, _evidence
from app.services.sentence_review_service import submit_sentence_review

@pytest.mark.parametrize('disposition', ['parked', 'reading_support'])
@pytest.mark.parametrize('source', ['bookifier','dragoman','textbook_scan','frequency_core','study'])
def test_all_paths_preserve_parked_history(db_session, disposition, source):
    db = db_session
    lemma = _seed_word(db, 1, 'كتاب', 'book')
    ulk = lemma.knowledge
    ulk.source = source
    ulk.leech_suspended_at = datetime.now(timezone.utc) - timedelta(days=100)
    before = copy.deepcopy(ulk.fsrs_card_json)
    seen = ulk.times_seen
    set_disposition(db, 1, disposition, 'test')
    db.commit()
    assert 1 not in due_lane_snapshot(db).due_ids
    assert 1 not in get_focus_cohort(db)
    assert not compute_word_tiers(db)
    assert get_acquisition_due(db) == []
    assert not select_next_words(db)
    assert start_acquisition(db, 1, source=source, restart_known=True) is ulk
    assert introduce_word(db, 1)['cap_deferred']
    assert submit_review(db, 1, 1)['exposure_only']
    assert submit_acquisition_review(db, 1, 1)['exposure_only']
    assert check_leech_reintroductions(db) == []
    assert ulk.fsrs_card_json == before
    assert ulk.times_seen == seen
    assert db.query(ReviewLog).count() == 0
    set_disposition(db, 1, 'maintain', 'resume')
    assert ulk.fsrs_card_json == before
    assert 1 in due_lane_snapshot(db).due_ids


def test_canonical_choice_blocks_variant_inventory_and_stale_review(db_session):
    db = db_session
    lemma = _seed_word(db, 1, 'كتاب', 'book')
    variant = Lemma(lemma_id=2, lemma_ar='الكتاب', lemma_ar_bare='الكتاب', canonical_lemma_id=1)
    db.add(variant)
    db.add(UserLemmaKnowledge(lemma_id=2, knowledge_state='acquiring', acquisition_box=1,
                             acquisition_next_due=datetime.now(timezone.utc)-timedelta(days=1)))
    db.flush()
    _seed_sentence(db, 1, 'الكتاب', 'the book', 2, [2])
    before = copy.deepcopy(lemma.knowledge.fsrs_card_json)
    set_disposition(db, 2, 'parked', 'variant choice')
    db.commit()
    assert db.query(UserLemmaKnowledge).filter(maintenance_clause()).count() == 0
    assert db.query(Sentence).filter(reviewable_sentence_clauses()).count() == 0
    assert submit_review(db, 2, 1)['exposure_only']
    sw = db.query(SentenceWord).one()
    sw.surface_form = 'الْكِتَاب'
    db.flush()
    submit_sentence_review(db, sentence_id=1, primary_lemma_id=2,
                           comprehension_signal='partial', missed_lemma_ids=[2],
                           client_review_id='parked-offline',
                           word_evidence_protocol_version=3,
                           word_review_evidence=[_evidence(sw, rating=1)])
    assert db.query(ReviewLog).count() == 0
    evidence = db.query(WordReviewEvidence).one()
    assert evidence.canonical_lemma_id == 1 and evidence.rating == 1
    assert evidence.review_log_id is None
    assert lemma.knowledge.fsrs_card_json == before
    assert lemma.knowledge.total_encounters == 1
    # Retry is idempotent even though no per-word ReviewLog was produced.
    submit_sentence_review(db, sentence_id=1, primary_lemma_id=2,
                           comprehension_signal='partial', missed_lemma_ids=[2],
                           client_review_id='parked-offline')
    assert lemma.knowledge.total_encounters == 1


@pytest.mark.parametrize('source', ['bookifier','dragoman','book','textbook_scan','story_import','book_ocr'])
def test_new_import_is_staged_and_explicit_choice_respects_cap(db_session, client, source):
    lemma = _seed_word(db_session, 1, 'كتاب', 'book', with_card=False)
    ulk = start_acquisition(db_session, 1, source=source, enforce_daily_cap=False)
    assert ulk.knowledge_state == 'encountered'
    assert ulk.attention_disposition == 'reading_support'
    assert ulk.fsrs_card_json is None
    db_session.commit()
    response = client.put('/api/words/1/attention', json={'disposition':'maintain'})
    assert response.status_code == 200
    assert response.json()['state'] == 'acquiring'
    assert client.get('/api/words/1').json()['attention_disposition'] == 'maintain'
    assert client.put('/api/words/1/attention', json={'disposition':'bad'}).status_code == 422


def test_parked_acquisition_is_not_recovery_debt(db_session):
    db = db_session
    for i in range(1, 9):
        _seed_word(db, i, f'كتاب{i}', 'book', with_card=False)
        db.add(UserLemmaKnowledge(lemma_id=i, knowledge_state='acquiring', acquisition_box=1,
                                 acquisition_next_due=datetime.now(timezone.utc)-timedelta(days=1),
                                 attention_disposition='parked'))
    db.commit()
    assert get_acquisition_due(db) == []
    assert not due_lane_snapshot(db).due_ids
    status = recovery_status(db)
    assert status['box1_actionable'] == 0


def test_recent_reading_choice_is_prioritized_then_expires(db_session):
    from app.models import FrequencyCoreEntry
    db = db_session
    book = _seed_word(db, 1, 'كتاب', 'book', with_card=False)
    core = _seed_word(db, 2, 'جبل', 'mountain', with_card=False)
    for lemma in (book, core):
        lemma.gates_completed_at = datetime.now(timezone.utc)
    book.source = 'book'
    db.add(FrequencyCoreEntry(core_rank=50,lemma_id=2,lemma_key='mountain',display_form='جبل',score=10))
    chosen = set_disposition(db, 1, 'maintain', 'Needed in next passage')
    db.commit()
    assert select_next_words(db,count=2)[0]['lemma_id'] == 1
    chosen.attention_updated_at = datetime.now(timezone.utc)-timedelta(days=15)
    db.commit()
    assert select_next_words(db,count=2)[0]['lemma_id'] == 2
    assert chosen.attention_disposition == 'maintain'  # only intake priority expires


def test_curation_preimages_fail_before_any_write(db_session):
    from scripts.apply_attention_curation import apply_curation
    db = db_session
    _seed_word(db, 1, 'كتاب', 'book')
    manifest = {'policy_version':'reading_attention_v1','entries':[
        {'expected':{'lemma_id':1,'gloss_en':'book'},'disposition':'parked','reason':'QA'},
        {'expected':{'lemma_id':999,'gloss_en':'stale'},'disposition':'parked','reason':'QA'}]}
    with pytest.raises(ValueError,match='Stale identity'):
        apply_curation(db,manifest,True)
    assert db.query(UserLemmaKnowledge).one().attention_disposition == 'maintain'
