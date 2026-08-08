from app.models import Lemma, ReviewLog, Sentence, SentenceReviewLog, SentenceWord
from app.routers.review import _session_activity_counts


def test_session_activity_counts_treats_passage_children_as_one_card(db_session):
    lemma = Lemma(lemma_id=1, lemma_ar="كِتَاب", lemma_ar_bare="كتاب", gloss_en="book")
    passage_a = Sentence(id=1, arabic_text="كِتَاب فِي", source="passage", target_lemma_id=1)
    passage_b = Sentence(id=2, arabic_text="بَيْت مَعَ كِتَاب", source="passage", target_lemma_id=1)
    ordinary = Sentence(id=3, arabic_text="كِتَاب", source="llm", target_lemma_id=1)
    db_session.add_all([lemma, passage_a, passage_b, ordinary])
    db_session.flush()

    for sentence_id, count in ((1, 2), (2, 3), (3, 1)):
        for position in range(count):
            db_session.add(SentenceWord(
                sentence_id=sentence_id,
                position=position,
                surface_form="كلمة",
                lemma_id=1,
            ))

    db_session.add_all([
        SentenceReviewLog(
            sentence_id=1,
            session_id="session-1",
            comprehension="partial",
            client_review_id="card-a",
        ),
        SentenceReviewLog(
            sentence_id=2,
            session_id="session-1",
            comprehension="partial",
            client_review_id="card-a:s2",
        ),
        SentenceReviewLog(
            sentence_id=3,
            session_id="session-1",
            comprehension="understood",
            client_review_id="card-b",
        ),
    ])
    for index, rating in enumerate((1, 2, 3, 4), start=1):
        db_session.add(ReviewLog(
            lemma_id=1,
            rating=rating,
            session_id="session-1",
            sentence_id=1,
            client_review_id=f"word-{index}",
        ))
    db_session.add(ReviewLog(
        lemma_id=1,
        rating=1,
        session_id="session-1",
        sentence_id=None,
        client_review_id="word-only",
    ))
    db_session.commit()

    assert _session_activity_counts(db_session, "session-1") == {
        "review_card_count": 2,
        "passage_card_count": 1,
        "arabic_word_count": 6,
        "word_review_count": 4,
        "word_red_count": 1,
        "word_yellow_count": 1,
        "word_green_count": 2,
    }
