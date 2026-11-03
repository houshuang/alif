"""Clean fluency observations survive sync/undo without changing scheduling."""
import pytest
from app.models import SentenceWord, SentenceReviewLog, ConfusionCapture
from tests.test_sentence_review import _seed_word, _seed_sentence


def payload(db):
    _seed_word(db, 1, "كِتَاب", "book")
    _seed_word(db, 2, "قَلَم", "pen")
    _seed_sentence(db, 1, "كِتَاب قَلَم", "book pen", target_lemma_id=1, word_ids=[1, 2])
    for sw in db.query(SentenceWord).all():
        sw.surface_form = "كِتَاب" if sw.lemma_id == 1 else "قَلَم"
    db.commit()
    evidence = []
    for sw in db.query(SentenceWord).all():
        evidence.append(dict(sentence_word_id=sw.id, rating=3, surface_form=sw.surface_form,
            rendered_front_form=sw.surface_form, default_show_tashkeel=True,
            front_initial_tashkeel_visible=True, front_ever_tashkeel_visible=True,
            front_tashkeel_visible_at_answer=True, front_toggle_count=0,
            answer_revealed=True, back_tashkeel_visible_at_rating=True,
            back_toggle_count=0, failure_causes=[]))
    return dict(sentence_id=1, primary_lemma_id=1, comprehension_signal="understood",
        response_ms=30000, reading_timing=dict(version=1, elapsed_ms=12000, exclusions=[]),
        lookup_count=0, audio_play_count=0, parent_card_type="sentence", session_id="timing", client_review_id="timing",
        word_evidence_protocol_version=3, word_review_evidence=evidence)


@pytest.mark.parametrize("sync", [False, True])
def test_clean_persisted_online_and_sync_idempotent(client, db_session, sync):
    data = payload(db_session)
    if sync:
        send = lambda: client.post("/api/review/sync", json={"reviews": [dict(type="sentence", client_review_id="timing", payload=data)]})
    else:
        send = lambda: client.post("/api/review/submit-sentence", json=data)
    response = send()
    assert response.status_code == 200, response.text
    row = db_session.query(SentenceReviewLog).one()
    assert row.reading_timing_json == dict(version=1, elapsed_ms=12000, exclusions=[], eligible=True)
    assert row.response_ms == 30000
    assert send().status_code == 200
    assert db_session.query(SentenceReviewLog).count() == 1
    response = client.post("/api/review/undo-sentence", json={"client_review_id":"timing"})
    assert response.status_code == 200, response.text
    assert db_session.query(SentenceReviewLog).count() == 0


@pytest.mark.parametrize("change,reason", [
    ({"parent_card_type":"passage"}, "not_single_reading_sentence"),
    ({"lookup_count":1}, "assistance_or_unknown"),
    ({"lookup_count":None}, "assistance_or_unknown"),
    ({"word_review_evidence":[]}, "missing_token_evidence"),
    ({"word_evidence_protocol_version":2}, "missing_token_evidence"),
    ({"comprehension_signal":"partial", "missed_lemma_ids":[1]}, "marked_word"),
    ({"reading_timing":{"version":1,"elapsed_ms":12000,"exclusions":["manual"]}}, "manual"),
])
def test_contradictory_or_incomplete_evidence_is_not_clean(client, db_session, change, reason):
    data=payload(db_session); data.update(change)
    response=client.post("/api/review/submit-sentence",json=data)
    assert response.status_code==200,response.text
    timing=db_session.query(SentenceReviewLog).one().reading_timing_json
    assert not timing["eligible"] and reason in timing["exclusions"]


def test_partial_token_evidence_not_clean(client, db_session):
    data=payload(db_session); data['word_review_evidence'].pop()
    response=client.post('/api/review/submit-sentence',json=data)
    assert response.status_code==200,response.text
    assert 'missing_token_evidence' in db_session.query(SentenceReviewLog).one().reading_timing_json['exclusions']


def test_old_client_does_not_manufacture_timing(client, db_session):
    data=payload(db_session); del data['reading_timing']
    assert client.post('/api/review/submit-sentence',json=data).status_code==200
    assert db_session.query(SentenceReviewLog).one().reading_timing_json is None


def test_red_capture_retains_rating_one(client, db_session):
    data=payload(db_session); data.update(comprehension_signal='partial',missed_lemma_ids=[1],
        confusion_captures=[dict(failed_lemma_id=1,capture_method='free_text',confused_with_text='pen')])
    assert client.post('/api/review/submit-sentence',json=data).status_code==200
    row=db_session.query(ConfusionCapture).one()
    assert row.rating==1 and row.confused_with_text=='pen'
