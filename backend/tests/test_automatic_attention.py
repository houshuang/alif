"""Automatic eligibility must save attention without rewriting memory."""
import copy
from datetime import datetime, timedelta, timezone
import pytest
from app.models import (ActivityLog, FrequencyCoreEntry, ReadingPilotEvent, ReviewLog,
                        SentenceReviewLog, UserLemmaKnowledge)
from app.services.automatic_attention import refresh_attention, READING_REASON
from app.services.attention_policy import set_disposition
from app.services.acquisition_service import start_acquisition
from app.services.word_selector import select_next_words
from tests.test_sentence_review import _seed_word, _seed_sentence

NOW = datetime(2026, 9, 19, 20, tzinfo=timezone.utc)

def word(db, lid=1, rank=30000, *, with_card=True):
    l = _seed_word(db, lid, ['كتاب','قلم','بيت'][lid-1], 'book', with_card=with_card)
    l.frequency_rank = rank
    l.gates_completed_at = NOW - timedelta(days=40)
    db.flush()
    return l

def failures(db, lid=1, n=8):
    for i in range(n):
        db.add(ReviewLog(lemma_id=lid, rating=1 if i < 3 else 3,
                        reviewed_at=NOW-timedelta(days=i+1)))
    db.flush()

def context(db, sid, lid=1, source='book', when=NOW, verified=True):
    s = _seed_sentence(db, sid, 'كتاب', 'book', lid, [lid])
    s.source=source
    s.quality_natural=s.quality_translation_correct=verified
    db.add(SentenceReviewLog(sentence_id=sid,reviewed_at=when,comprehension='understood',review_mode='reading'))
    db.flush()

def test_cost_relief_is_reversible_metadata_only_and_idempotent(db_session):
    db=db_session; l=word(db); failures(db); db.commit()
    k=l.knowledge
    before={c.name:copy.deepcopy(getattr(k,c.name)) for c in k.__table__.columns if not c.name.startswith('attention_')}
    plan=refresh_attention(db,apply=False,now=NOW)
    assert k.attention_disposition=='maintain'
    assert plan['changes'][0]['after']=='reading_support'
    refresh_attention(db,now=NOW)
    assert k.attention_disposition=='reading_support'
    assert before=={name:getattr(k,name) for name in before}
    assert db.query(ReviewLog).count()==8
    assert refresh_attention(db,now=NOW)['changes']==[]
    assert db.query(ActivityLog).count()==1
    context(db,1);context(db,2);db.commit()
    refresh_attention(db,now=NOW)
    assert k.attention_disposition=='maintain' and k.attention_reason==READING_REASON
    assert before=={name:getattr(k,name) for name in before}
    refresh_attention(db,now=NOW+timedelta(days=31))
    assert k.attention_disposition=='reading_support'

@pytest.mark.parametrize('rank,review_count',[(None,8),(100,8),(30000,5)])
def test_unknown_frequency_common_words_and_small_samples_are_protected(db_session,rank,review_count):
    l=word(db_session,rank=rank);failures(db_session,n=review_count)
    refresh_attention(db_session,now=NOW)
    assert l.knowledge.attention_disposition=='maintain'

def test_quran_rank_is_not_modern_utility_and_fiction_protects_existing_word(db_session):
    db=db_session;l=word(db);failures(db)
    f=FrequencyCoreEntry(core_rank=10,lemma_id=1,lemma_key='test',display_form=l.lemma_ar,islamic_rank=1)
    db.add(f);db.commit();refresh_attention(db,now=NOW)
    assert l.knowledge.attention_disposition=='reading_support'
    f.hindawi_rank=150;db.commit();refresh_attention(db,now=NOW)
    assert l.knowledge.attention_disposition=='maintain'

@pytest.mark.parametrize('source,verified,count',[('llm',True,2),('passage',True,2),('book',False,2),('book',True,1)])
def test_generated_unverified_or_repeated_same_context_is_not_reading_need(db_session,source,verified,count):
    db=db_session;l=word(db,with_card=False)
    set_disposition(db,1,'reading_support','import')
    for sid in range(1,count+1):context(db,sid,source=source,verified=verified)
    db.add(SentenceReviewLog(sentence_id=1,reviewed_at=NOW,comprehension='understood',review_mode='reading'))
    db.commit();refresh_attention(db,now=NOW)
    assert l.knowledge.attention_disposition=='reading_support'

def test_new_reading_need_enrolls_automatically_but_only_through_normal_cap(db_session,monkeypatch):
    db=db_session;l=word(db,with_card=False);context(db,1);context(db,2);db.commit()
    refresh_attention(db,now=NOW)
    k=db.query(UserLemmaKnowledge).one()
    assert k.knowledge_state=='encountered' and k.fsrs_card_json is None
    assert select_next_words(db)[0]['score_breakdown']['priority_tier']=='reading_recurrence'
    monkeypatch.setattr('app.services.acquisition_service._recovery_mode_intro_budget',lambda *args:0)
    start_acquisition(db,1,source='book')
    assert k.knowledge_state=='encountered'
    monkeypatch.setattr('app.services.acquisition_service._recovery_mode_intro_budget',lambda *args:2)
    start_acquisition(db,1,source='book')
    assert k.knowledge_state=='acquiring'

def test_common_staged_import_does_not_wait_for_manual_enrollment(db_session):
    db=db_session;word(db,rank=100,with_card=False)
    k=start_acquisition(db,1,source='book');db.commit()
    assert k.attention_disposition=='reading_support'
    refresh_attention(db,now=NOW)
    assert k.attention_disposition=='maintain'
    assert k.knowledge_state=='encountered' and k.fsrs_card_json is None
    assert select_next_words(db)[0]['lemma_id']==1

def test_qa_parked_word_cannot_be_reactivated_by_frequency_or_reading(db_session):
    db=db_session;word(db,rank=10);set_disposition(db,1,'parked','QA identity hold')
    context(db,1);context(db,2);db.commit();refresh_attention(db,now=NOW)
    assert db.query(UserLemmaKnowledge).one().attention_disposition=='parked'

def test_old_ui_cannot_override_automatic_policy(client,db_session):
    l=word(db_session);db_session.commit()
    r=client.put('/api/words/1/attention',json={'disposition':'parked'})
    assert r.status_code==410
    assert l.knowledge.attention_disposition=='maintain'


def test_prefetch_is_inert_but_fresh_session_automatically_reconciles(client,db_session):
    db=db_session;l=word(db);failures(db);db.commit()
    before=copy.deepcopy(l.knowledge.fsrs_card_json)
    assert client.get('/api/review/next-sentences?prefetch=true').status_code==200
    assert l.knowledge.attention_disposition=='maintain'
    assert db.query(ActivityLog).filter_by(event_type='automatic_attention').count()==0
    assert client.get('/api/review/next-sentences').status_code==200
    db.expire_all()
    assert l.knowledge.attention_disposition=='reading_support'
    assert l.knowledge.fsrs_card_json==before


def test_supported_chapter_lookups_require_distinct_paragraphs_and_unique_identity(db_session,monkeypatch):
    from app.models import Lemma
    db=db_session;l=word(db,with_card=False);l.lemma_ar='كِتَاب'
    chapters={'chapters':[{'id':'a','paragraphs':[
        {'id':p,'tokens':[{'id':'t','surface':'كِتَاب'}]} for p in ['p1','p2']]}]}
    monkeypatch.setattr('app.services.reading_chapters.get_reading_chapters',lambda:chapters)
    def lookup(i,p):
        db.add(ReadingPilotEvent(client_event_id=f'chapter:{i}',received_at=NOW,payload_json={
            'reader_id':'library-bridge','kind':'word','chapter_id':'a','paragraph_id':p,
            'token_id':'t','occurred_at':NOW.isoformat()}));db.commit()
    lookup(1,'p1');lookup(2,'p1');refresh_attention(db,now=NOW)
    assert db.query(UserLemmaKnowledge).count()==0
    lookup(3,'p2');refresh_attention(db,now=NOW)
    assert db.query(UserLemmaKnowledge).one().attention_reason==READING_REASON
    # Even an ungated duplicate makes the identity ambiguous on the next pass.
    db.add(Lemma(lemma_id=2,lemma_ar='كِتَاب',lemma_ar_bare='كتاب'));db.commit()
    refresh_attention(db,now=NOW)
    assert db.query(UserLemmaKnowledge).one().attention_disposition=='reading_support'


def test_book_receipts_count_only_completed_authentic_distinct_sentence_ranges(db_session):
    from app.models import Story, StoryWord
    db=db_session;word(db,with_card=False)
    story=Story(source='generated',body_ar='كتاب',metadata_json={'book_reader':{'passages':{
        'a':{'completed_at':NOW.isoformat(),'passage_token_positions':[0,1]}}}})
    db.add(story);db.flush()
    for pos in [0,1]:db.add(StoryWord(story_id=story.id,lemma_id=1,position=pos,sentence_index=pos,surface_form='كتاب'))
    db.commit();refresh_attention(db,now=NOW)
    assert db.query(UserLemmaKnowledge).count()==0
    story.source='book_ocr';db.commit();refresh_attention(db,now=NOW)
    assert db.query(UserLemmaKnowledge).one().attention_reason==READING_REASON
