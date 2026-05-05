"""Runtime gate: sentences with unmapped words are invisible to selection.

Two-concerns separation:
- Storage: SentenceWord.lemma_id IS NULL is allowed (book/corpus exception).
- Reviewability: any selection query that touches the user must filter them out.
"""

from datetime import datetime, timezone

from app.models import Lemma, Sentence, SentenceWord
from app.services.sentence_eligibility import (
    not_has_unmapped_words,
    reviewable_sentence_clauses,
)


def _lemma(db, arabic, **kw):
    lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        gloss_en=kw.get("gloss_en", arabic),
        word_category=kw.get("word_category"),
        gates_completed_at=datetime.now(timezone.utc),
    )
    db.add(lemma)
    db.flush()
    return lemma


def _sentence_with_words(db, *, sentence_id, words):
    sent = Sentence(
        id=sentence_id,
        arabic_text=" ".join(w[0] for w in words),
        is_active=True,
        target_lemma_id=next((lid for _, lid in words if lid), None),
    )
    db.add(sent)
    db.flush()
    for i, (surface, lemma_id) in enumerate(words):
        db.add(SentenceWord(
            sentence_id=sent.id,
            position=i,
            surface_form=surface,
            lemma_id=lemma_id,
        ))
    db.flush()
    return sent


def test_not_has_unmapped_words_excludes_sentence_with_null_lemma(db_session):
    l1 = _lemma(db_session, "كتاب")
    fully_mapped = _sentence_with_words(
        db_session,
        sentence_id=900_001,
        words=[("كتاب", l1.lemma_id), ("جديد", l1.lemma_id)],
    )
    has_null = _sentence_with_words(
        db_session,
        sentence_id=900_002,
        words=[("كتاب", l1.lemma_id), ("روزي", None)],
    )
    db_session.commit()

    rows = (
        db_session.query(Sentence)
        .filter(Sentence.id.in_([fully_mapped.id, has_null.id]))
        .filter(not_has_unmapped_words())
        .all()
    )
    visible_ids = {s.id for s in rows}
    assert fully_mapped.id in visible_ids
    assert has_null.id not in visible_ids


def test_reviewable_clause_combines_active_and_mapped(db_session):
    l1 = _lemma(db_session, "بيت")
    inactive = _sentence_with_words(
        db_session,
        sentence_id=900_010,
        words=[("بيت", l1.lemma_id)],
    )
    inactive.is_active = False
    unmapped = _sentence_with_words(
        db_session,
        sentence_id=900_011,
        words=[("بيت", l1.lemma_id), ("هايدي", None)],
    )
    ok = _sentence_with_words(
        db_session,
        sentence_id=900_012,
        words=[("بيت", l1.lemma_id)],
    )
    db_session.commit()

    rows = (
        db_session.query(Sentence)
        .filter(Sentence.id.in_([inactive.id, unmapped.id, ok.id]))
        .filter(reviewable_sentence_clauses())
        .all()
    )
    assert {s.id for s in rows} == {ok.id}


def test_proper_name_lemma_makes_sentence_reviewable(db_session):
    """Auto-created proper-name lemmas restore the lemma_id, unblocking the gate."""
    l1 = _lemma(db_session, "بيت")
    name = _lemma(db_session, "هايدي", word_category="proper_name", gloss_en="(proper name)")
    sent = _sentence_with_words(
        db_session,
        sentence_id=900_020,
        words=[("بيت", l1.lemma_id), ("هايدي", name.lemma_id)],
    )
    db_session.commit()

    rows = (
        db_session.query(Sentence)
        .filter(Sentence.id == sent.id, not_has_unmapped_words())
        .all()
    )
    assert len(rows) == 1
