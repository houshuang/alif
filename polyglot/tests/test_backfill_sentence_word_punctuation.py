from datetime import datetime, timezone

from app.models import Lemma, Sentence, SentenceWord
from scripts.backfill_sentence_word_punctuation import _rebuild_sentence_words


def test_rebuild_sentence_words_adds_punctuation_rows(tmp_db):
    with tmp_db() as db:
        lemma = Lemma(
            language_code="el",
            lemma_form="βιβλίο",
            lemma_bare="βιβλιο",
            gloss_en="book",
            source="test",
        )
        db.add(lemma)
        db.flush()
        sentence = Sentence(
            language_code="el",
            text="βιβλίο.",
            source="llm",
            is_active=True,
            mappings_verified_at=datetime.now(timezone.utc),
        )
        db.add(sentence)
        db.flush()
        db.add(SentenceWord(
            sentence_id=sentence.id,
            position=0,
            surface_form="βιβλίο",
            lemma_id=lemma.lemma_id,
            is_target_word=True,
        ))
        lemma_id = lemma.lemma_id
        sentence_id = sentence.id
        db.commit()

    with tmp_db() as db:
        sentence = db.get(Sentence, sentence_id)
        assert _rebuild_sentence_words(db, sentence, dry_run=True) == "would_update"
        assert _rebuild_sentence_words(db, sentence, dry_run=False) == "updated"
        db.commit()

    with tmp_db() as db:
        rows = (
            db.query(SentenceWord)
            .filter(SentenceWord.sentence_id == sentence_id)
            .order_by(SentenceWord.position.asc())
            .all()
        )
        assert [(row.surface_form, row.lemma_id, row.is_target_word) for row in rows] == [
            ("βιβλίο", lemma_id, True),
            (".", None, False),
        ]
