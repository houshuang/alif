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
        "cards": 2, "words_reviewed": 4, "words_correct": 3,
        "new_words": 1, "moved_up": 1, "graduated": 1, "known": 2,
    }
    assert set(client.get("/api/review/today").json()) == {
        "cards", "words_reviewed", "words_correct", "new_words", "moved_up", "graduated", "known",
    }
