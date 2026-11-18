"""Story import: unknown words that only a CAMeL guess linked are checked."""
import pytest

from app.models import Lemma, Story, StoryWord
from app.services import import_quality, lemma_quality
from app.services import preread_service as ps
from app.services import story_service
from app.services.sentence_validator import build_lemma_lookup


@pytest.fixture
def setup(db_session, monkeypatch):
    visit = Lemma(lemma_ar="زَارَ", lemma_ar_bare="زار", pos="verb", gloss_en="to visit")
    db_session.add(visit)
    db_session.flush()
    story = Story(body_ar="ذهب إلى وزارة التعليم", source="imported", status="active")
    db_session.add(story)
    db_session.flush()
    story.words.extend(
        # Only وزارة is unknown; its neighbours are the check's context.
        StoryWord(position=i, surface_form=w, is_function_word=w != "وزارة")
        for i, w in enumerate(["ذهب", "إلى", "وزارة", "التعليم"])
    )
    db_session.commit()
    lookup = build_lemma_lookup([visit])

    def features(word):
        if "وزارة" in word:
            return {"lex": "زار", "root": "ز.و.ر", "pos": "verb"}
        raise AssertionError(word)
    monkeypatch.setattr(story_service, "get_word_features", features)
    translations = []

    def translate(**kwargs):
        translations.append(kwargs["prompt"])
        return {"words": [{"index": 1, "arabic": "وِزَارَة", "english": "ministry", "pos": "noun"}]}
    monkeypatch.setattr(story_service, "generate_completion", translate)
    monkeypatch.setattr(lemma_quality, "run_quality_gates", lambda *a, **k: None)
    monkeypatch.setattr(import_quality, "classify_lemmas", lambda words: (words, []))
    monkeypatch.setattr(story_service, "_verify_new_story_mappings", lambda *a, **k: None)
    calls = []

    def use_verdicts(verdicts):
        def fake(text, items):
            assert not db_session.new and not db_session.dirty
            calls.append((text, items))
            return verdicts(items) if callable(verdicts) else verdicts
        monkeypatch.setattr(ps, "check_senses", fake)

    return visit, story, lookup, use_verdicts, calls, translations


def _ministry(story):
    return next(w for w in story.words if w.surface_form == "وزارة")


def test_fitting_guess_links(db_session, setup):
    visit, story, lookup, use_verdicts, calls, translations = setup
    use_verdicts({})
    assert story_service._import_unknown_words(db_session, story, lookup) == []
    sw = _ministry(story)
    assert (sw.lemma_id, sw.gloss_en) == (visit.lemma_id, "to visit")
    assert len(calls) == 1
    assert calls[0][1][0]["forms"] == ["وزارة"]
    assert "ذهب إلى وزارة التعليم" in calls[0][0]


def test_broader_guess_links_and_widens_gloss(db_session, setup):
    visit, story, lookup, use_verdicts, _, _ = setup
    use_verdicts(lambda items: {items[0]["index"]: {"verdict": "broader",
                                                    "gloss_en": "to visit; visit (n.)"}})
    story_service._import_unknown_words(db_session, story, lookup)
    sw = _ministry(story)
    assert (sw.lemma_id, sw.gloss_en) == (visit.lemma_id, "to visit; visit (n.)")
    db_session.expire_all()
    assert db_session.get(Lemma, visit.lemma_id).gloss_en == "to visit; visit (n.)"


def test_different_guess_takes_new_word_path(db_session, setup):
    visit, story, lookup, use_verdicts, _, translations = setup
    use_verdicts(lambda items: {items[0]["index"]: {
        "verdict": "different", "lemma_ar": "وِزَارَة", "gloss_en": "ministry", "pos": "noun"}})
    new_ids = story_service._import_unknown_words(db_session, story, lookup)
    assert len(new_ids) == 1
    sw = _ministry(story)
    assert sw.lemma_id == new_ids[0] != visit.lemma_id
    new = db_session.get(Lemma, new_ids[0])
    assert (new.lemma_ar, new.lemma_ar_bare, new.root_id) == ("وِزَارَة", "وزارة", None)
    assert any("candidate_lemma=وِزَارَة" in p for p in translations)
    assert db_session.get(Lemma, visit.lemma_id).gloss_en == "to visit"


def test_failed_check_leaves_guess_unlinked(db_session, setup):
    visit, story, lookup, use_verdicts, _, translations = setup
    use_verdicts(None)
    assert story_service._import_unknown_words(db_session, story, lookup) == []
    sw = _ministry(story)
    assert sw.lemma_id is None and sw.gloss_en is None
    assert db_session.query(Lemma).count() == 1 and translations == []
