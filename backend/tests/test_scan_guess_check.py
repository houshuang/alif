"""Textbook scan: words only a CAMeL guess matched are checked, not refused."""
from app.models import Lemma
from app.services import ocr_service
from app.services import preread_service as ps
from app.services import sentence_validator as sv
from app.services.sentence_validator import build_lemma_lookup


def _setup(db, monkeypatch, verdicts):
    visit = Lemma(lemma_ar="زَارَ", lemma_ar_bare="زار", pos="verb", gloss_en="to visit")
    db.add(visit)
    db.commit()
    lookup = build_lemma_lookup([visit])
    guess = lambda word, _lookup: visit.lemma_id if "وزارة" in word or "زيارات" in word else None
    monkeypatch.setattr(sv, "_camel_disambiguate", guess)
    monkeypatch.setattr(sv, "_camel_headword_match", guess)
    calls = []

    def fake(text, items, **kw):
        calls.append((text, items))
        return verdicts(items) if callable(verdicts) else verdicts
    monkeypatch.setattr(ps, "check_senses", fake)
    words = [{"arabic": "وزارة", "english": "ministry"}, {"arabic": "زيارات", "english": "visits"}]
    return visit, lookup, words, calls


def test_confirmed_guess_is_accepted_and_different_one_is_not(db_session, monkeypatch):
    def verdicts(items):
        by = {it["forms"][0]: it["index"] for it in items}
        return {by["وزارة"]: {"verdict": "different", "lemma_ar": "وِزَارَة", "gloss_en": "ministry",
                              "pos": "noun"}}
    visit, lookup, words, calls = _setup(db_session, monkeypatch, verdicts)
    accepted, skip = ocr_service._check_scan_guesses(db_session, words, lookup)
    assert accepted == {(None, "زيارات"): visit.lemma_id}  # وزارة takes the new-word path
    assert skip == set()
    assert len(calls) == 1 and "وزارة — ministry" in calls[0][0]


def test_broader_guess_widens_gloss(db_session, monkeypatch):
    def verdicts(items):
        return {it["index"]: {"verdict": "broader", "gloss_en": "to visit; visit (n.)", "approved": True}
                for it in items if it["forms"][0] == "زيارات"}
    visit, lookup, words, _ = _setup(db_session, monkeypatch, verdicts)
    accepted, _ = ocr_service._check_scan_guesses(db_session, words, lookup)
    assert accepted[(None, "زيارات")] == visit.lemma_id
    db_session.expire_all()
    assert db_session.get(Lemma, visit.lemma_id).gloss_en == "to visit; visit (n.)"


def test_failed_check_skips_the_guessed_words(db_session, monkeypatch):
    _, lookup, words, _ = _setup(db_session, monkeypatch, None)
    accepted, skip = ocr_service._check_scan_guesses(db_session, words, lookup)
    assert accepted == {} and skip == {(None, "وزارة"), (None, "زيارات")}


def test_no_guesses_no_call(db_session, monkeypatch):
    _, lookup, _, calls = _setup(db_session, monkeypatch, {})
    accepted, skip = ocr_service._check_scan_guesses(
        db_session, [{"arabic": "زار", "english": "visited"}], lookup)
    assert (accepted, skip, calls) == ({}, set(), [])
