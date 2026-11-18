"""The sentence a word was added from anchors its generated review material.

It is a hint in the generation prompt only: it never becomes a review
sentence and never bypasses the verified pipeline.
"""
from unittest.mock import patch

from app.models import Lemma, Sentence, UserLemmaKnowledge, WordAddContext
from app.services import material_generator
from app.services.add_context import add_sentences_for
from app.services.llm import AllProvidersFailed, _format_target_example_block


def _word(db, verdicts):
    lem = Lemma(lemma_id=11, lemma_ar="قَرْن", lemma_ar_bare="قرن", gloss_en="century; horn",
                pos="noun")
    db.add_all([lem, UserLemmaKnowledge(lemma_id=11, knowledge_state="acquiring")])
    for i, (sentence, verdict) in enumerate(verdicts):
        db.add(WordAddContext(lemma_id=11, source="snap", sentence_ar=sentence,
                              check_verdict=verdict))
        db.flush()
    db.commit()
    return lem


def test_latest_same_word_sentence_is_the_hint(db_session):
    _word(db_session, [("عاش في القرن العشرين", "fits"),
                       ("للثور قرن طويل", "different"),
                       ("قبل قرن من الزمن", None)])
    assert add_sentences_for(db_session, [11]) == {11: "قبل قرن من الزمن"}


def test_other_word_sentences_are_not_a_hint(db_session):
    _word(db_session, [("قرن الدين", "different"), ("قرن", "name")])
    assert add_sentences_for(db_session, [11]) == {}


def test_prompt_block_puts_the_add_sentence_first():
    block = _format_target_example_block("مثال", "example", "قبل قرن من الزمن")
    assert block.index("قبل قرن من الزمن") < block.index("مثال")
    assert "do not copy" in block
    assert _format_target_example_block(None, None, None) == ""


def test_single_word_generation_passes_the_hint(db_session):
    _word(db_session, [("قبل قرن من الزمن", "fits")])
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        raise AllProvidersFailed("stop")
    with patch("app.services.llm.generate_sentences_batch", side_effect=fake):
        assert material_generator.generate_material_for_word(11, needed=1) == 0
    assert seen["target_context_ar"] == "قبل قرن من الزمن"
    assert db_session.query(Sentence).count() == 0


def test_batch_generation_passes_the_hint(db_session, monkeypatch):
    monkeypatch.delenv("ALIF_USE_LEGACY_BATCH", raising=False)
    _word(db_session, [("قبل قرن من الزمن", None)])
    seen = []

    def fake(**kwargs):
        seen.append(kwargs["target_words"])
        return []
    with patch("app.services.llm.generate_sentences_for_words", side_effect=fake):
        material_generator.batch_generate_material([11], count_per_word=1)
    assert seen[0][0]["context_ar"] == "قبل قرن من الزمن"
