from datetime import datetime, timedelta, timezone

from app.models import Lemma, ReviewLog, Sentence, SentenceReviewLog, UserLemmaKnowledge
from app.services.today_progress import today_progress


def test_today_progress_counts_only_today(client, db_session):
    now = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
    today = now.replace(tzinfo=None) - timedelta(hours=2)
    yesterday = today - timedelta(days=1)
    lemmas = [Lemma(lemma_ar=f"كلمة{i}", lemma_ar_bare=f"كلمة{i}", gloss_en=f"w{i}", pos="noun") for i in range(5)]
    sent = Sentence(arabic_text="نص", english_translation="text")
    db_session.add_all(lemmas + [sent])
    db_session.flush()
    ids = [l.lemma_id for l in lemmas]
    db_session.add_all([
        # one passage card with two child sentences, one plain card, one from yesterday
        SentenceReviewLog(sentence_id=sent.id, comprehension="understood", reviewed_at=today, client_review_id="p1:s0"),
        SentenceReviewLog(sentence_id=sent.id, comprehension="understood", reviewed_at=today, client_review_id="p1:s1"),
        SentenceReviewLog(sentence_id=sent.id, comprehension="partial", reviewed_at=today, client_review_id="c2"),
        SentenceReviewLog(sentence_id=sent.id, comprehension="understood", reviewed_at=yesterday, client_review_id="old"),
        ReviewLog(lemma_id=ids[0], rating=3, reviewed_at=today, sentence_id=sent.id, is_acquisition=True,
                  fsrs_log_json={"acquisition_box_before": 1, "acquisition_box_after": 2, "graduated": False}),
        ReviewLog(lemma_id=ids[0], rating=3, reviewed_at=today, sentence_id=sent.id, is_acquisition=True,
                  fsrs_log_json={"acquisition_box_before": 2, "acquisition_box_after": 3, "graduated": False}),
        ReviewLog(lemma_id=ids[1], rating=3, reviewed_at=today, sentence_id=sent.id, is_acquisition=True,
                  fsrs_log_json={"acquisition_box_before": 2, "acquisition_box_after": None, "graduated": True}),
        ReviewLog(lemma_id=ids[2], rating=1, reviewed_at=today, sentence_id=sent.id, is_acquisition=False),
        ReviewLog(lemma_id=ids[3], rating=3, reviewed_at=yesterday, sentence_id=sent.id, is_acquisition=False),
        UserLemmaKnowledge(lemma_id=ids[0], knowledge_state="acquiring", acquisition_started_at=today,
                           acquisition_episode_kind="new"),
        UserLemmaKnowledge(lemma_id=ids[1], knowledge_state="learning", graduated_at=today,
                           acquisition_started_at=yesterday, acquisition_episode_kind="new"),
        UserLemmaKnowledge(lemma_id=ids[2], knowledge_state="known"),
        UserLemmaKnowledge(lemma_id=ids[3], knowledge_state="acquiring", acquisition_started_at=today,
                           acquisition_episode_kind="leech_reintro"),
    ])
    db_session.commit()

    assert today_progress(db_session, now) == {
        "cards": 2, "due_words": 0, "words_reviewed": 4, "words_correct": 3,
        "new_words": 1, "moved_up": 1, "graduated": 1, "known": 2,
    }
    assert set(client.get("/api/review/today").json()) == {
        "cards", "due_words", "words_reviewed", "words_correct", "new_words", "moved_up", "graduated", "known",
    }


def test_today_progress_counts_distinct_due_or_target_words(db_session):
    now = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
    today = now.replace(tzinfo=None) - timedelta(hours=2)
    lemmas = [Lemma(lemma_ar=f"كلمة{i}", lemma_ar_bare=f"كلمة{i}", gloss_en=f"w{i}", pos="noun") for i in range(6)]
    sent = Sentence(arabic_text="نص", english_translation="text")
    db_session.add_all(lemmas + [sent])
    db_session.flush()
    ids = [l.lemma_id for l in lemmas]
    due_before = (today - timedelta(hours=1)).isoformat() + "+00:00"
    due_after = (today + timedelta(days=3)).isoformat() + "+00:00"
    db_session.add_all([
        # target word, reviewed twice: counted once
        ReviewLog(lemma_id=ids[0], rating=3, reviewed_at=today, sentence_id=sent.id, credit_type="primary"),
        ReviewLog(lemma_id=ids[0], rating=3, reviewed_at=today, sentence_id=sent.id, credit_type="primary"),
        # collateral acquisition word that was due
        ReviewLog(lemma_id=ids[1], rating=3, reviewed_at=today, sentence_id=sent.id, credit_type="collateral",
                  is_acquisition=True, fsrs_log_json={"is_due_at_review": True}),
        # collateral FSRS word whose card was due before the review
        ReviewLog(lemma_id=ids[2], rating=3, reviewed_at=today, sentence_id=sent.id, credit_type="collateral",
                  fsrs_log_json={"pre_card": {"due": due_before}}),
        # collateral words that were not due: not counted
        ReviewLog(lemma_id=ids[3], rating=3, reviewed_at=today, sentence_id=sent.id, credit_type="collateral",
                  fsrs_log_json={"pre_card": {"due": due_after}}),
        ReviewLog(lemma_id=ids[4], rating=3, reviewed_at=today, sentence_id=sent.id, credit_type="collateral",
                  is_acquisition=True, fsrs_log_json={"is_due_at_review": False}),
        # due target from yesterday: not counted
        ReviewLog(lemma_id=ids[5], rating=3, reviewed_at=today - timedelta(days=1), sentence_id=sent.id,
                  credit_type="primary"),
    ])
    db_session.commit()

    assert today_progress(db_session, now)["due_words"] == 3
