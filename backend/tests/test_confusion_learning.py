from datetime import datetime, timedelta
from unittest.mock import Mock
import pytest
from app.models import ConfusionPair, ConfusionCapture, ConfusionLink, ConfusionAttempt, Lemma, SentenceReviewLog, WordReviewEvidence
from app.services.confusion_learning import import_captures, link_capture, pair_progress, generate_lesson


def words(db):
    a = Lemma(lemma_ar="صَعِدَ", lemma_ar_bare="صعد", gloss_en="to ascend", pos="verb")
    b = Lemma(lemma_ar="سَعِيد", lemma_ar_bare="سعيد", gloss_en="happy", pos="adjective")
    db.add_all([a, b]); db.commit()
    return a, b


def test_intake_free_text_strips_direction_marks_and_does_not_link_forgot(db_session):
    a, b = words(db_session)
    db_session.add_all([ConfusionCapture(failed_lemma_id=a.lemma_id, rating=2, capture_method="free_text", confused_with_text="\u200fhappy"),
        ConfusionCapture(failed_lemma_id=a.lemma_id, rating=2, capture_method="free_text", confused_with_text="Forget")])
    db_session.commit(); import_captures(db_session); db_session.commit()
    assert db_session.query(ConfusionPair).count() == 1
    assert all(c.resolved_lemma_id is None for c in db_session.query(ConfusionCapture))
    assert sorted(x.state for x in db_session.query(ConfusionLink)) == ["linked", "not_a_word"]
    import_captures(db_session); db_session.commit()
    assert db_session.query(ConfusionLink).count() == 2


def test_ambiguous_manual_meaning_requires_confirmation(db_session):
    a, b = words(db_session)
    db_session.add(Lemma(lemma_ar="فَرِح", lemma_ar_bare="فرح", gloss_en="happy", pos="adjective")); db_session.commit()
    cap = ConfusionCapture(failed_lemma_id=a.lemma_id, rating=2, capture_method="free_text", confused_with_text="happy")
    db_session.add(cap); db_session.commit(); import_captures(db_session); db_session.commit()
    assert db_session.get(ConfusionLink, cap.id).state == "needs_confirmation"
    assert db_session.query(ConfusionPair).count() == 0


def test_reverse_reports_share_one_pair(db_session):
    a, b = words(db_session)
    for first, second in [(a,b), (b,a)]:
        c = ConfusionCapture(failed_lemma_id=first.lemma_id, rating=2, capture_method="suggested_pick", confused_with_lemma_id=second.lemma_id)
        db_session.add(c); db_session.flush(); link_capture(db_session, c, second.lemma_id, "selected")
    db_session.commit()
    assert db_session.query(ConfusionPair).count() == 1


def fixture_pair(db):
    a,b = words(db)
    p = ConfusionPair(lemma_a_id=a.lemma_id, lemma_b_id=b.lemma_id,
        lesson_json={"version":1, "questions":[{"answer_lemma_id":a.lemma_id},{"answer_lemma_id":b.lemma_id}]})
    db.add(p); db.flush()
    cap = ConfusionCapture(failed_lemma_id=a.lemma_id, rating=2, capture_method="suggested_pick", captured_at=datetime(2026,11,1), confused_with_lemma_id=b.lemma_id)
    db.add(cap); db.flush(); link_capture(db, cap, b.lemma_id, "selected"); db.commit()
    return p,a,b


def wins(db,p,a,b):
    for i, w in enumerate([a,b,a,b]):
        db.add(ConfusionAttempt(client_attempt_id=f"attempt-{i}", pair_id=p.id, session_id="one", question_index=i,
            chosen_lemma_id=w.lemma_id, target_lemma_id=w.lemma_id, correct=True, assisted=False, created_at=datetime(2026,11,2)))
    db.commit()


def clean_context(db, w, sid, day, *, eligible=True, causes=None):
    date = datetime(2026,11,day)
    db.add(SentenceReviewLog(sentence_id=sid, session_id="s", client_review_id=f"r-{sid}", reviewed_at=date,
        comprehension="understood", reading_timing_json={"eligible":eligible}, review_mode="reading"))
    e = WordReviewEvidence(client_review_id=f"r-{sid}", sentence_id=sid, sentence_word_id=sid, position=0,
        lemma_id=w.lemma_id, canonical_lemma_id=w.lemma_id, rating=3, review_mode="reading", protocol_version=3,
        surface_form=w.lemma_ar, rendered_front_form=w.lemma_ar, default_show_tashkeel=True, front_initial_tashkeel_visible=True,
        front_ever_tashkeel_visible=True, front_tashkeel_visible_at_answer=True, created_at=date, failure_causes_json=causes)
    db.add(e); db.commit(); return e


def test_practice_is_partial_delayed_both_sides_required_and_undo_recalculates(db_session):
    p,a,b = fixture_pair(db_session); wins(db_session,p,a,b)
    assert pair_progress(db_session,p)["status"] == "partially_resolved"
    clean_context(db_session,a,101,3); clean_context(db_session,a,102,4)
    assert pair_progress(db_session,p)["status"] != "resolved"
    last = clean_context(db_session,b,103,4)
    assert pair_progress(db_session,p)["status"] == "resolved"
    db_session.delete(last); db_session.commit()
    assert pair_progress(db_session,p)["status"] == "partially_resolved"


def test_helped_and_immediate_contexts_never_resolve(db_session):
    p,a,b = fixture_pair(db_session); wins(db_session,p,a,b)
    clean_context(db_session,a,101,1); clean_context(db_session,b,102,1)
    clean_context(db_session,a,103,4,eligible=False); clean_context(db_session,b,104,5,eligible=False)
    assert pair_progress(db_session,p)["status"] != "resolved"


def test_new_report_reopens_and_duplicate_practice_does_not_add_credit(db_session):
    p,a,b = fixture_pair(db_session); wins(db_session,p,a,b)
    for sid,w,day in [(101,a,3),(102,b,4),(103,a,4)]: clean_context(db_session,w,sid,day)
    assert pair_progress(db_session,p)["status"] == "resolved"
    cap=ConfusionCapture(failed_lemma_id=b.lemma_id, rating=2, capture_method="suggested_pick", captured_at=datetime(2026,11,6))
    db_session.add(cap); db_session.flush(); link_capture(db_session,cap,a.lemma_id,"selected"); db_session.commit()
    assert pair_progress(db_session,p)["status"] == "active"


def test_attempt_endpoint_is_idempotent_and_server_grades(client,db_session):
    p,a,b = fixture_pair(db_session)
    body=dict(client_attempt_id="one-answer-id",session_id="session",question_index=0,chosen_lemma_id=b.lemma_id)
    first=client.post(f"/api/confusions/{p.id}/attempts",json=body)
    assert first.status_code == 200 and first.json()["correct"] is False
    assert client.post(f"/api/confusions/{p.id}/attempts",json=body).status_code == 200
    assert db_session.query(ConfusionAttempt).count() == 1
    body["chosen_lemma_id"]=a.lemma_id
    assert client.post(f"/api/confusions/{p.id}/attempts",json=body).status_code == 409


def test_rejected_generated_lesson_never_becomes_teaching_content():
    draft=dict(distinction="Different letters and different meanings",a_cue="first cue",b_cue="second cue",
        clozes=[dict(sentence_ar="هَذَا ___ .",translation_en="This is a sample.",a_form="صَعِدَ",b_form="سَعِيد",answer=s,feedback="Check the different first letter.") for s in ["a","b","a","b"]])
    complete=Mock(side_effect=[draft,{"accepted":False,"reason":"Ambiguous sentences"},draft,{"accepted":False,"reason":"Ambiguous sentences"}])
    with pytest.raises(ValueError,match="Ambiguous"):
        generate_lesson(dict(id=1,gloss="up",arabic="صَعِدَ"),dict(id=2,gloss="happy",arabic="سَعِيد"),complete)


def test_teaching_exposure_never_counts_as_failure_or_success(client,db_session):
    p,a,b=fixture_pair(db_session); wins(db_session,p,a,b)
    before=pair_progress(db_session,p)
    response=client.post(f"/api/confusions/{p.id}/attempts",json=dict(client_attempt_id="view-only-event",session_id="session",question_index=-1,chosen_lemma_id=None,assisted=True))
    assert response.status_code==200
    assert pair_progress(db_session,p)==before


def test_marked_confusion_on_a_member_reopens_pair(db_session):
    p,a,b=fixture_pair(db_session); wins(db_session,p,a,b)
    for sid,w,day in [(101,a,3),(102,b,4),(103,a,4)]: clean_context(db_session,w,sid,day)
    assert pair_progress(db_session,p)["status"]=="resolved"
    e=clean_context(db_session,a,104,5,eligible=False,causes=["mixed_up"])
    e.rating=2; db_session.commit()
    assert pair_progress(db_session,p)["status"]=="active"


def test_prepare_claim_does_not_schedule_duplicate_generation(client,db_session):
    p,a,b=fixture_pair(db_session); p.lesson_json=None; db_session.commit()
    assert client.post(f"/api/confusions/{p.id}/prepare").json()["generation_state"]=="working"
    started=db_session.get(ConfusionPair,p.id).generation_started_at
    assert client.post(f"/api/confusions/{p.id}/prepare").status_code==200
    db_session.expire_all()
    assert db_session.get(ConfusionPair,p.id).generation_started_at==started


def test_manual_confirmation_is_teaching_only_not_a_selector_mutation(client,db_session):
    a,b=words(db_session)
    cap=ConfusionCapture(failed_lemma_id=a.lemma_id,rating=2,capture_method="free_text",confused_with_text="happy",session_id="current-reading")
    db_session.add(cap); db_session.commit()
    response=client.post(f"/api/confusions/captures/{cap.id}/confirm",json={"lemma_id":b.lemma_id})
    assert response.status_code==200
    assert response.json()["pairs"][0]["last_session_id"]=="current-reading"
    db_session.expire_all()
    assert db_session.get(ConfusionCapture,cap.id).resolved_lemma_id is None
    assert db_session.get(ConfusionLink,cap.id).resolution_method=="manual"


def run(db, p, a, b, session, day, hour=9, *, correct=True):
    for i, w in enumerate([a, b]):
        db.add(ConfusionAttempt(client_attempt_id=f"{session}-{i}", pair_id=p.id, session_id=session, question_index=i,
            chosen_lemma_id=w.lemma_id if correct else None, target_lemma_id=w.lemma_id, correct=correct,
            assisted=not correct, created_at=datetime(2026, 11, day, hour, i)))
    db.commit()


def schedule_at(db, p, day, hour=12):
    from app.services import confusion_learning
    real = confusion_learning.now
    confusion_learning.now = lambda: datetime(2026, 11, day, hour)
    try:
        return pair_progress(db, p)
    finally:
        confusion_learning.now = real


def test_clean_runs_space_out_and_same_day_runs_do_not_advance(db_session):
    p, a, b = fixture_pair(db_session)
    assert schedule_at(db_session, p, 2)["due_now"] and schedule_at(db_session, p, 2)["practice_step"] == 0
    run(db_session, p, a, b, "s1", 2)
    s = schedule_at(db_session, p, 2, 18)
    assert (s["practice_step"], s["due_now"], s["practice_start"]) == (1, False, 0)  # Two-question fixture wraps to the start.
    run(db_session, p, a, b, "s2", 2, 15)  # Same afternoon: no credit.
    assert schedule_at(db_session, p, 3, 12)["practice_step"] == 1
    assert schedule_at(db_session, p, 3, 12)["due_now"]
    run(db_session, p, a, b, "s3", 3, 12)
    s = schedule_at(db_session, p, 10)
    assert (s["practice_step"], s["due_now"], s["next_due"]) == (2, False, "2026-11-17T12:01:00")
    assert schedule_at(db_session, p, 17, 13)["due_now"]


def test_three_spaced_runs_then_retired_until_error(db_session):
    p, a, b = fixture_pair(db_session)
    for session, day in [("s1", 2), ("s2", 3), ("s3", 18)]:
        run(db_session, p, a, b, session, day)
    assert schedule_at(db_session, p, 20)["practice_step"] == 3
    # The 60-day wait would end in January; a fourth clean run after it retires the pair.
    from app.services.confusion_learning import practice_schedule
    attempts = db_session.query(ConfusionAttempt).all()
    late = [ConfusionAttempt(session_id="s4", created_at=datetime(2027, 1, 20, 9, i), correct=True, assisted=False) for i in range(2)]
    retired = practice_schedule(attempts + late, datetime.min, at=datetime(2027, 6, 1))
    assert retired["practice_retired"] and not retired["due_now"]
    run(db_session, p, a, b, "miss", 20, correct=False)
    s = schedule_at(db_session, p, 20, 13)
    assert (s["practice_step"], s["due_now"]) == (0, True)


def test_only_two_new_pairs_a_day_but_reviews_are_not_limited(client, db_session):
    from app.routers.confusions import limit_new_pairs
    p, a, b = fixture_pair(db_session)
    db_session.add(ConfusionAttempt(client_attempt_id="view-old", pair_id=p.id, session_id="x", question_index=-1,
        target_lemma_id=a.lemma_id, correct=False, assisted=True, created_at=datetime(2026, 11, 25, 8)))
    db_session.commit()
    fake = lambda i, report, state="ready": dict(id=i, due_now=True, last_report=report, generation_state=state, lesson={})
    pairs = [dict(id=p.id, due_now=True, last_report="2026-11-01", generation_state="ready", lesson={}),
             fake(901, "2026-11-20"), fake(902, "2026-11-24", "failed"), fake(903, "2026-11-23"), fake(904, "2026-11-22")]
    limit_new_pairs(db_session, pairs, at=datetime(2026, 11, 25, 12))
    assert {x["id"] for x in pairs if x["due_now"]} == {p.id, 903}
    limit_new_pairs(db_session, pairs := [fake(905, "2026-11-23"), fake(906, "2026-11-22")], at=datetime(2026, 11, 26, 12))
    assert all(x["due_now"] for x in pairs)


def test_search_finds_a_word_by_sound_in_plain_letters(client, db_session):
    a, b = words(db_session)
    a.transliteration_ala_lc, b.transliteration_ala_lc = "ṣaʿida", "saʿīd"
    db_session.add(Lemma(lemma_ar="حَزِين", lemma_ar_bare="حزين", gloss_en="sad", pos="adjective"))
    db_session.commit()
    for query in ("said", "saed", "saida"):
        found = [w["arabic"] for w in client.get("/api/confusions/search", params={"q": query}).json()]
        assert "صَعِدَ" in found and "سَعِيد" in found, query
    # A plain-letter sound match ranks above a weak fuzzy English guess.
    found = [w["arabic"] for w in client.get("/api/confusions/search", params={"q": "saed"}).json()]
    assert found.index("صَعِدَ") < found.index("حَزِين") if "حَزِين" in found else True


def test_transliteration_never_auto_links_free_text(db_session):
    a, b = words(db_session)
    b.transliteration_ala_lc = "man"
    cap = ConfusionCapture(failed_lemma_id=a.lemma_id, rating=2, capture_method="free_text", confused_with_text="man")
    db_session.add(cap); db_session.commit(); import_captures(db_session); db_session.commit()
    assert db_session.query(ConfusionPair).count() == 0
