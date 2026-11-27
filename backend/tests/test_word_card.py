from datetime import datetime

from app.models import Lemma, Root, Sentence, SentenceWord, UserLemmaKnowledge
from app.services import word_card
from app.services.word_card import enrich_word_cards, select_candidates, validate


def _inp(**over):
    base = {
        "lemma_id": 5,
        "ar": "مُعَلِّم",
        "bare": "معلم",
        "gloss": "teacher",
        "pos": "noun",
        "wazn": None,
        "verb_form": None,
        "root": "ع.ل.م",
        "root_meaning": "knowledge",
        "forms": {"plural": "مُعَلِّمُون", "feminine": "مُعَلِّمَة"},
        "family": [
            {"lemma_id": 1, "ar": "عَلِمَ", "gloss": "to know", "pos": "verb", "wazn": None},
            {"lemma_id": 2, "ar": "عَلَّمَ", "gloss": "to teach", "pos": "verb", "wazn": None},
        ],
        "book_phrases": ["معلم اللغة"],
    }
    base.update(over)
    return base


def _raw(**over):
    raw = {
        "lemma_id": 5,
        "chain": [
            {"ar": "عَلِمَ", "en": "to know", "tag": "I", "lemma_id": 1},
            {"ar": "عَلَّمَ", "en": "to make know: teach", "tag": "II", "lemma_id": 2},
            {"ar": "مُعَلِّم", "en": "one who teaches", "tag": "doer", "lemma_id": 99},
        ],
        "forms": [{"key": "plural", "en": "teachers"}, {"key": "masdar", "en": "not stored"}],
        "phrases": [
            {"ar": "مُعَلِّمُ اللُّغَةِ", "en": "language teacher", "from_book": True},
            {"ar": "غُرْفَةُ المُعَلِّمِينَ", "en": "staff room", "from_book": False},
            {"ar": "فِي الصَّفِّ", "en": "in class", "from_book": False},
        ],
    }
    raw.update(over)
    return raw


def test_validate_keeps_checked_chain_forms_and_phrases():
    card = validate(_inp(), _raw())
    assert [n["ar"] for n in card["chain"]] == ["عَلِمَ", "عَلَّمَ", "مُعَلِّم"]
    assert card["chain"][-1]["lemma_id"] == 5  # last node is always this lemma
    assert card["forms"] == {"plural": "teachers"}  # only stored forms
    # The phrase without the word is dropped; the plural-with-al phrase is kept.
    assert [p["en"] for p in card["phrases"]] == ["language teacher", "staff room"]
    assert card["phrases"][0]["attested"] is True
    assert card["phrases"][1]["attested"] is False


def test_validate_rejects_chain_not_ending_at_the_word():
    raw = _raw(chain=[
        {"ar": "عَلِمَ", "en": "to know", "tag": "I", "lemma_id": 1},
        {"ar": "عَلَّمَ", "en": "to teach", "tag": "II", "lemma_id": 2},
    ])
    assert validate(_inp(), raw)["chain"] == []


def test_validate_allows_one_unlisted_step_only():
    one = _raw(chain=[
        {"ar": "شَفَى", "en": "to heal", "tag": "I", "lemma_id": None},
        {"ar": "مُعَلِّم", "en": "x", "tag": "doer", "lemma_id": None},
    ])
    assert len(validate(_inp(), one)["chain"]) == 2
    two = _raw(chain=[
        {"ar": "شَفَى", "en": "to heal", "tag": "I", "lemma_id": None},
        {"ar": "اِسْتَشْفَى", "en": "to seek healing", "tag": "X", "lemma_id": None},
        {"ar": "مُعَلِّم", "en": "x", "tag": "doer", "lemma_id": None},
    ])
    assert validate(_inp(), two)["chain"] == []


def test_validate_drops_family_id_that_does_not_match_the_arabic():
    raw = _raw()
    raw["chain"][0]["lemma_id"] = 2  # claims عَلَّمَ's id for عَلِمَ
    card = validate(_inp(), raw)
    assert card["chain"][0]["lemma_id"] is None


def test_validate_uses_the_resolver_for_phrases():
    raw = _raw(phrases=[{"ar": "كَلِمَةٌ أُخْرَى", "en": "other", "from_book": False}])
    assert validate(_inp(), raw)["phrases"] == []
    resolved = validate(_inp(), raw, resolve=lambda tok: 5 if tok == "كَلِمَةٌ" else None)
    assert [p["en"] for p in resolved["phrases"]] == ["other"]


def _seed(db, word_state="known", stability=10.0, with_sentence=True):
    root = Root(root="ع.ل.م", core_meaning_en="knowledge")
    db.add(root)
    db.flush()
    lemma = Lemma(lemma_ar="مُعَلِّم", lemma_ar_bare="معلم", gloss_en="teacher", pos="noun",
                  root_id=root.root_id, gates_completed_at=datetime.utcnow())
    db.add(lemma)
    db.flush()
    db.add(UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state=word_state,
                              fsrs_card_json={"stability": stability}))
    if with_sentence:
        s = Sentence(arabic_text="جملة", english_translation="s", source="llm", is_active=True,
                     mappings_verified_at=datetime.utcnow())
        db.add(s)
        db.flush()
        db.add(SentenceWord(sentence_id=s.id, position=0, surface_form="معلم", lemma_id=lemma.lemma_id))
    db.commit()
    return lemma


def test_select_candidates_orders_learning_before_known_and_needs_a_sentence(db_session):
    known = _seed(db_session, "known", 50.0)
    other_root = Root(root="ك.ت.ب")
    db_session.add(other_root)
    db_session.flush()
    learning = Lemma(lemma_ar="كِتَاب", lemma_ar_bare="كتاب", gloss_en="book", pos="noun",
                     root_id=other_root.root_id, gates_completed_at=datetime.utcnow())
    no_sentence = Lemma(lemma_ar="قَلَم", lemma_ar_bare="قلم", gloss_en="pen", pos="noun",
                        gates_completed_at=datetime.utcnow())
    db_session.add_all([learning, no_sentence])
    db_session.flush()
    db_session.add_all([
        UserLemmaKnowledge(lemma_id=learning.lemma_id, knowledge_state="learning"),
        UserLemmaKnowledge(lemma_id=no_sentence.lemma_id, knowledge_state="learning"),
    ])
    s = Sentence(arabic_text="ج", english_translation="s", source="llm", is_active=True)
    db_session.add(s)
    db_session.flush()
    db_session.add(SentenceWord(sentence_id=s.id, position=0, surface_form="كتاب", lemma_id=learning.lemma_id))
    db_session.commit()

    assert select_candidates(db_session, 10) == [learning.lemma_id, known.lemma_id]


def test_enrich_writes_cards_and_skips_failed_batches(db_session, monkeypatch):
    lemma = _seed(db_session)
    monkeypatch.setattr(word_card, "load_phrase_candidates", lambda: {})
    monkeypatch.setattr(word_card, "call_model", lambda inputs: None)
    stats = enrich_word_cards(db_session, 5)
    assert stats["failed_batches"] == 1 and stats["written"] == 0
    db_session.refresh(lemma)
    assert lemma.word_card_json is None  # failure is not stored, so a later run retries

    chain = [{"ar": "عَلَّمَ", "en": "to teach", "tag": "II", "lemma_id": None},
             {"ar": "مُعَلِّم", "en": "one who teaches", "tag": "doer", "lemma_id": None}]
    monkeypatch.setattr(word_card, "call_model",
                        lambda inputs: {lemma.lemma_id: _raw(lemma_id=lemma.lemma_id, chain=chain)})
    stats = enrich_word_cards(db_session, 5)
    assert stats["written"] == 1
    db_session.refresh(lemma)
    assert lemma.word_card_json["version"] == word_card.WORD_CARD_VERSION
    assert lemma.word_card_json["chain"][-1]["lemma_id"] == lemma.lemma_id
    assert select_candidates(db_session, 5) == []


def _senses(*items):
    return [{"en": en, "ar": ar, "example_en": "x"} for en, ar in items]


def test_validate_keeps_distinct_senses_whose_example_contains_the_word():
    raw = _raw(senses=_senses(
        ("teacher", "مُعَلِّمُ الصَّفِّ"),
        ("master craftsman", "مُعَلِّمُ النِّجَارَةِ"),
        ("teacher", "مُعَلِّمٌ جَدِيدٌ"),        # duplicate meaning
        ("lesson", "دَرْسٌ صَعْبٌ"),             # example lacks the word
        ("guide", "مُعَلِّم"),                   # one-word example
    ))
    card = validate(_inp(), raw)
    assert [s["en"] for s in card["senses"]] == ["teacher", "master craftsman"]
    assert card["senses"][0]["ar"] == "مُعَلِّمُ الصَّفِّ"


def test_validate_stores_a_single_sense_as_none():
    card = validate(_inp(), _raw(senses=_senses(("teacher", "مُعَلِّمُ الصَّفِّ"))))
    assert card["senses"] == []
    assert validate(_inp(), _raw())["senses"] == []  # older answers without the field


def test_select_candidates_puts_new_words_first_and_stale_multi_meaning_cards_last(db_session):
    known = _seed(db_session, "known", 50.0)
    s = db_session.query(Sentence).first()

    def add(ar, bare, gloss, state, card=None, times_seen=0):
        lemma = Lemma(lemma_ar=ar, lemma_ar_bare=bare, gloss_en=gloss, pos="verb",
                      gates_completed_at=datetime.utcnow())
        if card is not None:  # an explicit None would store JSON null, not SQL NULL
            lemma.word_card_json = card
        db_session.add(lemma); db_session.flush()
        db_session.add(UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state=state, times_seen=times_seen))
        db_session.add(SentenceWord(sentence_id=s.id, position=lemma.lemma_id, surface_form=bare, lemma_id=lemma.lemma_id))
        return lemma

    new = add("كَتَبَ", "كتب", "to write", "acquiring")
    acquiring = add("دَرَسَ", "درس", "to study", "acquiring", times_seen=3)
    stale_multi = add("حَلَّ", "حل", "to solve; to arrive", "known", card={"version": 1, "chain": []})
    add("ذَهَبَ", "ذهب", "to go, to leave", "known", card={"version": 1, "chain": []})          # one meaning
    add("قَرَأَ", "قرأ", "to read; to recite", "known",
        card={"version": word_card.WORD_CARD_VERSION, "chain": []})                               # current
    db_session.commit()

    assert select_candidates(db_session, 10) == [new.lemma_id, acquiring.lemma_id, known.lemma_id, stale_multi.lemma_id]


def test_enrich_replaces_an_old_card_but_never_a_current_one(db_session, monkeypatch):
    lemma = _seed(db_session)
    lemma.gloss_en = "teacher; master"
    lemma.word_card_json = {"version": 1, "chain": [], "forms": {}, "phrases": []}
    db_session.commit()
    monkeypatch.setattr(word_card, "load_phrase_candidates", lambda: {})
    senses = _senses(("teacher", "مُعَلِّمُ الصَّفِّ"), ("master craftsman", "مُعَلِّمُ النِّجَارَةِ"))
    monkeypatch.setattr(word_card, "call_model",
                        lambda inputs: {lemma.lemma_id: _raw(lemma_id=lemma.lemma_id, senses=senses)})
    assert enrich_word_cards(db_session, 5)["written"] == 1
    db_session.refresh(lemma)
    assert lemma.word_card_json["version"] == word_card.WORD_CARD_VERSION
    assert len(lemma.word_card_json["senses"]) == 2

    monkeypatch.setattr(word_card, "call_model",
                        lambda inputs: {lemma.lemma_id: _raw(lemma_id=lemma.lemma_id, senses=[])})
    word_card.enrich_word_cards_for(db_session, [lemma.lemma_id])
    db_session.refresh(lemma)
    assert len(lemma.word_card_json["senses"]) == 2


def test_senses_that_leave_out_the_stored_main_meaning_are_not_stored():
    # Seen on حَلّ "solution": the model listed dissolution and untying only.
    inp = _inp(gloss="solution")
    raw = _raw(senses=_senses(("dissolution", "مُعَلِّمُ الصَّفِّ"), ("untying", "مُعَلِّمُ النِّجَارَةِ")))
    assert validate(inp, raw)["senses"] == []
    # The gloss's first meaning may come later in the list (most common first).
    inp = _inp(gloss="to place or rank; to solve")
    raw = _raw(senses=_senses(("to solve", "مُعَلِّمُ الصَّفِّ"), ("to rank", "مُعَلِّمُ النِّجَارَةِ")))
    assert [s["en"] for s in validate(inp, raw)["senses"]] == ["to solve", "to rank"]
