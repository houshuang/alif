from datetime import datetime, timezone

from app.models import Lemma, Sentence, StoryWord, UserLemmaKnowledge
from app.services.passage_generator import (
    _eligible_passage_words,
    store_maintenance_passage,
)


def _seed_lemma(db, lemma_id, arabic, bare, gloss, state="known", box=None):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=bare,
        gloss_en=gloss,
        pos="noun",
    )
    db.add(lemma)
    db.flush()
    db.add(UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        acquisition_box=box,
        introduced_at=datetime.now(timezone.utc),
        source="study",
    ))
    db.flush()
    return lemma


def test_eligible_passage_words_excludes_box1_acquisition(db_session):
    _seed_lemma(db_session, 1, "كِتَاب", "كتاب", "book", state="known")
    _seed_lemma(db_session, 2, "بَيْت", "بيت", "house", state="acquiring", box=1)
    _seed_lemma(db_session, 3, "وَلَد", "ولد", "boy", state="acquiring", box=2)
    db_session.commit()

    eligible = _eligible_passage_words(db_session)

    assert {w["lemma_id"] for w in eligible} == {1, 3}


def test_store_maintenance_passage_creates_story_and_sentence_rows(db_session):
    words = [
        _seed_lemma(db_session, 1, "كِتَاب", "كتاب", "book"),
        _seed_lemma(db_session, 2, "بَيْت", "بيت", "house"),
        _seed_lemma(db_session, 3, "وَلَد", "ولد", "boy"),
    ]
    db_session.commit()

    target_words = [
        {"lemma_id": w.lemma_id, "arabic": w.lemma_ar, "english": w.gloss_en, "pos": w.pos}
        for w in words
    ]
    generated = {
        "title_ar": "ذِكْرَى صَغِيرَةٌ",
        "title_en": "A small memory",
        "style_tag": "nostalgic",
        "sentences": [
            {"arabic": "كِتَابٌ بَيْتٌ.", "english": "A book, a house."},
            {"arabic": "وَلَدٌ كِتَابٌ.", "english": "A boy, a book."},
            {"arabic": "بَيْتٌ وَلَدٌ.", "english": "A house, a boy."},
        ],
    }

    story = store_maintenance_passage(
        db_session,
        generated,
        target_words=target_words,
        eligible_words=target_words,
        quality_gate=False,
    )

    assert story.format_type == "maintenance_passage"
    assert story.metadata_json["style_tag"] == "nostalgic"
    sentences = db_session.query(Sentence).filter(Sentence.story_id == story.id).all()
    assert len(sentences) == 3
    assert {s.source for s in sentences} == {"passage"}
    story_words = db_session.query(StoryWord).filter(StoryWord.story_id == story.id).all()
    assert {sw.sentence_index for sw in story_words} == {0, 1, 2}
