from types import SimpleNamespace
from unittest.mock import patch

from app.services import grammar_tagger


@patch("app.services.grammar_tagger.generate_completion")
def test_tag_lemmas_grammar_batch_uses_structured_batch_call(mock_generate):
    mock_generate.return_value = {
        "lemmas": [
            {"lemma_id": 1, "features": ["past", "form_1", "not_valid"]},
            {"lemma_id": 2, "features": ["feminine", "plural_broken"]},
            {"lemma_id": 999, "features": ["past"]},
        ]
    }
    lemmas = [
        SimpleNamespace(lemma_id=1, lemma_ar="كَتَبَ", pos="verb", gloss_en="to write"),
        SimpleNamespace(lemma_id=2, lemma_ar="مَدْرَسَة", pos="noun", gloss_en="school"),
    ]

    out = grammar_tagger.tag_lemmas_grammar_batch(lemmas)

    assert out == {
        1: ["past", "form_1"],
        2: ["feminine", "plural_broken"],
    }
    assert mock_generate.call_count == 1
    kwargs = mock_generate.call_args.kwargs
    assert kwargs["json_schema"] == grammar_tagger._LEMMA_GRAMMAR_BATCH_SCHEMA
    assert kwargs["task_type"] == "grammar_tag"
    assert "lemma_id=1" in kwargs["prompt"]
    assert "lemma_id=2" in kwargs["prompt"]


@patch("app.services.grammar_tagger.generate_completion")
def test_tag_lemmas_grammar_batch_accepts_direct_array_shape(mock_generate):
    mock_generate.return_value = [
        {"lemma_id": 3, "features": ["active_participle", "bad"]},
    ]

    out = grammar_tagger.tag_lemmas_grammar_batch([
        {"lemma_id": 3, "lemma_ar": "كَاتِب", "pos": "noun", "gloss_en": "writer"},
    ])

    assert out == {3: ["active_participle"]}


@patch("app.services.grammar_tagger.generate_completion")
def test_tag_lemma_grammar_single_call_uses_same_cleaner(mock_generate):
    mock_generate.return_value = {"features": ["feminine", "unknown"]}

    out = grammar_tagger.tag_lemma_grammar("كَبِيرَة", "adjective", "big")

    assert out == ["feminine"]
