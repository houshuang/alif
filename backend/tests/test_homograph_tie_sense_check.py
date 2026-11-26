"""Homograph ties go to the sense check with the same-spelling siblings offered.

دَار "house" and دَارَ "to turn" share the bare form دار; nothing in the bare
word tells them apart, so the resolver keeps the first-registered row and
flags ``homograph_tie``. The unchecked writers (textbook scan, story import,
Snap) must let the sense check pick the sibling, never add a duplicate.
"""
import pytest

from app.models import Lemma, Story, StoryWord, UserLemmaKnowledge
from app.services import lookup_sense
from app.services import ocr_service
from app.services import preread_service as ps
from app.services import story_service
from app.services.lookup_sense import validate_verdict
from app.services.sentence_validator import (
    LemmaMatchProvenance,
    build_lemma_lookup,
    lookup_lemma_id,
)


@pytest.fixture
def pair(db_session):
    house = Lemma(lemma_ar="دَار", lemma_ar_bare="دار", pos="noun", gloss_en="house",
                  source="frequency_core")
    turn = Lemma(lemma_ar="دَارَ", lemma_ar_bare="دار", pos="verb", gloss_en="to turn",
                 source="frequency_core")
    db_session.add_all([house, turn])
    db_session.commit()
    return house, turn


def test_tie_reports_the_siblings(pair):
    house, turn = pair
    provenance = LemmaMatchProvenance()
    lookup = build_lemma_lookup([house, turn])
    assert lookup_lemma_id("دار", lookup, provenance, strict_fallback=True) == house.lemma_id
    assert provenance.homograph_tie and provenance.tied_lemma_ids == [turn.lemma_id]
    assert not provenance.is_guess


# ── sense-check validation ──────────────────────────────────────────────

CANDIDATES = [{"lemma_id": 7, "lemma_ar": "دَارَ", "gloss_en": "to turn", "pos": "verb"}]
ENTRY = {"lemma_ar": "دَار", "gloss_en": "house", "pos": "noun", "candidates": CANDIDATES}


@pytest.mark.parametrize("res, expected", [
    ({"verdict": "sibling", "candidate_id": 7}, {"verdict": "sibling", "lemma_id": 7}),
    ({"verdict": "sibling", "candidate_id": 99}, None),
    ({"verdict": "sibling"}, None),
    # A "different" answer naming an offered entry is that entry, not a new word.
    ({"verdict": "different", "lemma_ar": "دَارَ", "gloss_en": "to turn", "pos": "verb"},
     {"verdict": "sibling", "lemma_id": 7}),
    ({"verdict": "fits"}, {"verdict": "fits"}),
])
def test_lookup_verdict_validates_the_chosen_sibling(res, expected):
    assert validate_verdict(res, ENTRY) == expected


def test_lookup_sibling_needs_candidates():
    entry = {k: v for k, v in ENTRY.items() if k != "candidates"}
    assert validate_verdict({"verdict": "sibling", "candidate_id": 7}, entry) is None


def test_check_senses_offers_and_validates_siblings(monkeypatch):
    prompts = []

    def fake(**kwargs):
        prompts.append(kwargs["prompt"])
        return {"mismatches": [
            {"index": 0, "verdict": "sibling", "candidate_id": 7},
            {"index": 1, "verdict": "sibling", "candidate_id": 7},  # not offered
            {"index": 2, "verdict": "sibling", "candidate_id": 3},  # not among its ids
        ]}
    from app.services import llm
    monkeypatch.setattr(llm, "generate_completion", fake)
    items = [
        {"index": 0, "lemma_ar": "دَار", "gloss_en": "house", "pos": "noun", "forms": ["دار"],
         "candidates": CANDIDATES},
        {"index": 1, "lemma_ar": "بَيْت", "gloss_en": "house", "pos": "noun", "forms": ["بيت"]},
        {"index": 2, "lemma_ar": "دَار", "gloss_en": "house", "pos": "noun", "forms": ["دار"],
         "candidates": CANDIDATES},
    ]
    assert ps.check_senses("دار الرجل", items) == {0: {"verdict": "sibling", "lemma_id": 7}}
    assert "other entries with this spelling: id 7: دَارَ (verb) — to turn" in prompts[0]


# ── textbook scan ───────────────────────────────────────────────────────

def _fake_check(monkeypatch, verdicts):
    calls = []

    def fake(text, items, **kw):
        calls.append(items)
        return verdicts(items) if callable(verdicts) else verdicts
    monkeypatch.setattr(ps, "check_senses", fake)
    return calls


def test_scan_tie_takes_the_sibling_the_check_names(db_session, monkeypatch, pair):
    house, turn = pair
    lookup = build_lemma_lookup([house, turn])
    calls = _fake_check(monkeypatch, lambda items: {
        items[0]["index"]: {"verdict": "sibling", "lemma_id": turn.lemma_id}})
    accepted, skip = ocr_service._check_scan_guesses(
        db_session, [{"arabic": "دار", "english": "to turn"}], lookup)
    assert accepted == {(None, "دار"): turn.lemma_id} and skip == set()
    assert calls[0][0]["candidates"] == [
        {"lemma_id": turn.lemma_id, "lemma_ar": "دَارَ", "gloss_en": "to turn", "pos": "verb"}]


@pytest.mark.parametrize("verdicts", [
    None,  # failed check
    lambda items: {items[0]["index"]: {"verdict": "different", "lemma_ar": "دَارّ",
                                       "gloss_en": "flowing", "pos": "adj"}},
])
def test_scan_tie_otherwise_keeps_the_resolver_answer(db_session, monkeypatch, pair, verdicts):
    house, turn = pair
    lookup = build_lemma_lookup([house, turn])
    _fake_check(monkeypatch, verdicts)
    accepted, skip = ocr_service._check_scan_guesses(
        db_session, [{"arabic": "دار", "english": "house"}], lookup)
    # Never skipped and never sent down the new-word path.
    assert skip == set()
    assert accepted.get((None, "دار"), house.lemma_id) == house.lemma_id


# ── story import ────────────────────────────────────────────────────────

def _story(db_session, house):
    story = Story(body_ar="رأيت دار الرجل", source="imported", status="active")
    db_session.add(story)
    db_session.flush()
    story.words.extend([
        StoryWord(position=0, surface_form="رأيت", is_function_word=True),
        StoryWord(position=1, surface_form="دار", lemma_id=house.lemma_id, gloss_en="house"),
        StoryWord(position=2, surface_form="الرجل", is_function_word=True),
        StoryWord(position=3, surface_form="دار", lemma_id=house.lemma_id, gloss_en="house"),
    ])
    db_session.commit()
    return story


def test_story_tie_relinks_to_the_named_sibling(db_session, monkeypatch, pair):
    house, turn = pair
    db_session.add(UserLemmaKnowledge(lemma_id=turn.lemma_id, knowledge_state="known"))
    story = _story(db_session, house)
    lookup = build_lemma_lookup([house, turn])
    calls = _fake_check(monkeypatch, lambda items: {
        items[0]["index"]: {"verdict": "sibling", "lemma_id": turn.lemma_id}})
    assert story_service._import_unknown_words(db_session, story, lookup) == []
    tied = [w for w in story.words if w.surface_form == "دار"]
    assert [(w.lemma_id, w.gloss_en, w.is_known_at_creation) for w in tied] == [
        (turn.lemma_id, "to turn", True)] * 2
    # One check item per surface, with the story text around it.
    assert len(calls) == 1 and len(calls[0]) == 1
    assert calls[0][0]["candidates"][0]["lemma_id"] == turn.lemma_id


def test_story_tie_failed_check_leaves_the_link(db_session, monkeypatch, pair):
    house, turn = pair
    story = _story(db_session, house)
    lookup = build_lemma_lookup([house, turn])
    _fake_check(monkeypatch, None)
    assert story_service._import_unknown_words(db_session, story, lookup) == []
    assert {w.lemma_id for w in story.words if w.surface_form == "دار"} == {house.lemma_id}
    assert db_session.query(Lemma).count() == 2


# ── Snap ────────────────────────────────────────────────────────────────

def test_lookup_sense_offers_siblings_and_returns_the_chosen_one(
    client, db_session, monkeypatch, pair
):
    house, turn = pair
    seen = []

    def fake(sentence, surface, entry):
        seen.append(entry)
        return validate_verdict({"verdict": "sibling", "candidate_id": turn.lemma_id}, entry)
    monkeypatch.setattr(lookup_sense, "check_lookup_sense", fake)
    body = client.post("/api/discover/lookup/sense", json={
        "lemma_id": house.lemma_id, "surface": "دار", "context": "دار الرجل حول البيت"}).json()
    assert [c["lemma_id"] for c in seen[0]["candidates"]] == [turn.lemma_id]
    assert body["verdict"] == "different" and body["sibling"] is True
    assert (body["word"]["lemma_id"], body["word"]["lemma_source"]) == (
        turn.lemma_id, "homograph_sibling")


def test_lookup_sense_without_a_tie_offers_nothing(client, db_session, monkeypatch, pair):
    house, _ = pair
    seen = []
    monkeypatch.setattr(lookup_sense, "check_lookup_sense",
                        lambda s, surface, entry: seen.append(entry) or {"verdict": "fits"})
    client.post("/api/discover/lookup/sense", json={
        "lemma_id": house.lemma_id, "surface": "الدار", "context": "في الدار"})
    assert "candidates" not in seen[0]


def test_snap_chip_tie_is_replaced_by_the_sibling(db_session, monkeypatch, pair):
    from app.routers import discover

    house, turn = pair
    calls = _fake_check(monkeypatch, lambda items: {
        items[0]["index"]: {"verdict": "sibling", "lemma_id": turn.lemma_id}})
    words = discover.discover_words_in_text(db_session, "دار", count=5, include_oov=True,
                                            include_learning=True)
    assert words[0]["lemma_id"] == house.lemma_id
    assert words[0]["homograph_candidates"] == [turn.lemma_id]
    out = discover._sense_check_chips(db_session, "دار", words)
    assert calls[0][0]["candidates"][0]["lemma_id"] == turn.lemma_id
    assert out[0]["lemma_id"] == turn.lemma_id and out[0]["gloss_en"] == "to turn"
    assert out[0]["sense_from"]["lemma_id"] == house.lemma_id


# ── pre-read ────────────────────────────────────────────────────────────

def _preread_words(house, turn):
    return [{
        "surface": "دار", "surface_forms": ["دار"], "lemma_ar": "دَار", "lemma_ar_bare": "دار",
        "gloss_en": "house", "pos": "noun", "is_proper_noun": False, "freq_rank": 900,
        "count_in_text": 3, "lemma_id": house.lemma_id, "transliteration": "dār",
        "register": None, "dialect": None, "homograph_candidates": [turn.lemma_id],
    }]


def _preread(db_session, monkeypatch, house, turn, verdicts):
    monkeypatch.setattr("app.routers.discover.discover_words_in_text",
                        lambda *a, **k: [dict(w) for w in _preread_words(house, turn)])
    monkeypatch.setattr(ps, "rate_importance", lambda text, items: {it["index"]: 2 for it in items})
    calls = _fake_check(monkeypatch, verdicts)
    return ps.analyze_pages(db_session, ["دار الرجل حول البيت"]), calls


def test_preread_tie_enters_the_sibling_the_check_names(db_session, monkeypatch, pair):
    from app.routers import preread as pr
    from app.services import acquisition_service

    house, turn = pair
    db_session.add(UserLemmaKnowledge(lemma_id=house.lemma_id, knowledge_state="encountered"))
    db_session.commit()
    plan, calls = _preread(db_session, monkeypatch, house, turn, lambda items: {
        items[0]["index"]: {"verdict": "sibling", "lemma_id": turn.lemma_id}})
    assert calls[0][0]["candidates"][0]["lemma_id"] == turn.lemma_id
    (w,) = plan["learn"]
    assert (w["lemma_id"], w["lemma_ar"], w["gloss_en"], w["pos"]) == (
        turn.lemma_id, "دَارَ", "to turn", "verb")
    assert w["sense_sibling"] and w["sense_from"]["lemma_id"] == house.lemma_id
    assert plan["stats"]["sense_sibling"] == 1 and plan["stats"]["sense_rerouted"] == 0

    monkeypatch.setattr(acquisition_service, "_recovery_mode_intro_budget", lambda *a: 0)
    pr._apply_plan(plan)
    db_session.expire_all()
    assert db_session.query(Lemma).count() == 2  # never a new lemma
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=turn.lemma_id).one()
    assert ulk.knowledge_state == "acquiring"
    assert db_session.query(UserLemmaKnowledge).filter_by(
        lemma_id=house.lemma_id).one().knowledge_state == "encountered"


def test_preread_tie_sibling_already_learning_leaves_the_plan(db_session, monkeypatch, pair):
    house, turn = pair
    db_session.add_all([
        UserLemmaKnowledge(lemma_id=house.lemma_id, knowledge_state="encountered"),
        UserLemmaKnowledge(lemma_id=turn.lemma_id, knowledge_state="acquiring"),
    ])
    db_session.commit()
    plan, _ = _preread(db_session, monkeypatch, house, turn, lambda items: {
        items[0]["index"]: {"verdict": "sibling", "lemma_id": turn.lemma_id}})
    assert plan["learn"] == [] and plan["shaky"] == []


def test_preread_tie_failed_check_holds_the_word_back(db_session, monkeypatch, pair):
    house, turn = pair
    db_session.add(UserLemmaKnowledge(lemma_id=house.lemma_id, knowledge_state="encountered"))
    db_session.commit()
    plan, calls = _preread(db_session, monkeypatch, house, turn, None)
    assert calls[0][0]["candidates"][0]["lemma_id"] == turn.lemma_id
    assert plan["learn"] == [] and plan["stats"]["sense_check_held_back"] == 1


# ── add-context check ───────────────────────────────────────────────────

def _add_context(db_session, lemma, surface="دار"):
    from app.models import WordAddContext

    row = WordAddContext(lemma_id=lemma.lemma_id, source="snap", surface=surface,
                         sentence_ar="دار الرجل حول البيت", gloss_at_add=lemma.gloss_en,
                         introduced_by_add=True)
    db_session.add(row)
    db_session.commit()
    return row.id


def test_add_check_tie_records_the_sibling_and_swap_enrolls_it(
    client, db_session, monkeypatch, pair
):
    from app.models import WordAddContext
    from app.services import add_context

    house, turn = pair
    db_session.add(UserLemmaKnowledge(lemma_id=house.lemma_id, knowledge_state="acquiring"))
    db_session.commit()
    cid = _add_context(db_session, house)
    seen = []

    def fake(sentence, surface, entry):
        seen.append(entry)
        return validate_verdict({"verdict": "sibling", "candidate_id": turn.lemma_id}, entry)
    monkeypatch.setattr(lookup_sense, "check_lookup_sense", fake)
    assert add_context.verify_add_contexts([cid]) == {"different": 1}
    assert [c["lemma_id"] for c in seen[0]["candidates"]] == [turn.lemma_id]
    db_session.expire_all()
    row = db_session.get(WordAddContext, cid)
    assert row.check_verdict == "different"
    assert (row.check_json["sibling_lemma_id"], row.check_json["lemma_ar"]) == (turn.lemma_id, "دَارَ")

    item = client.get("/api/discover/add-checks").json()["items"][0]
    assert item["proposed"]["lemma_id"] == turn.lemma_id
    out = client.post(f"/api/discover/add-checks/{cid}", json={"action": "swap"}).json()
    assert out["lemma_id"] == turn.lemma_id and out["created"] is False
    db_session.expire_all()
    assert db_session.query(Lemma).count() == 2


def test_add_check_without_a_tie_offers_nothing(db_session, monkeypatch, pair):
    from app.services import add_context

    house, _ = pair
    other = Lemma(lemma_ar="بَيْت", lemma_ar_bare="بيت", pos="noun", gloss_en="house",
                  source="frequency_core")
    db_session.add(other)
    db_session.commit()
    cid = _add_context(db_session, other, surface="البيت")
    seen = []
    monkeypatch.setattr(lookup_sense, "check_lookup_sense",
                        lambda s, surface, entry: seen.append(entry) or {"verdict": "fits"})
    assert add_context.verify_add_contexts([cid]) == {"fits": 1}
    assert "candidates" not in seen[0]
