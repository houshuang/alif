from types import SimpleNamespace
from unittest.mock import patch

import scripts.update_material as update_material
from app.services.llm import AllProvidersFailed


def test_has_diacritics_detects_harakat():
    assert update_material._has_diacritics("كَتَبَ")
    assert not update_material._has_diacritics("كتب")
    assert not update_material._has_diacritics(None)


@patch("app.services.llm.generate_completion")
def test_generate_corpus_enrichment_batch_uses_structured_batch_call(mock_generate):
    mock_generate.return_value = {
        "sentences": [
            {
                "id": 10,
                "diacritized": " كَتَبَ الوَلَدُ ",
                "translation": " The boy wrote. ",
            },
            {"id": 11, "diacritized": "", "translation": "The girl read."},
            {"id": 999, "diacritized": "ignored", "translation": "ignored"},
        ]
    }
    sentences = [
        SimpleNamespace(id=10, arabic_text="كتب الولد"),
        SimpleNamespace(id=11, arabic_text="قَرَأَتِ البنت"),
    ]

    out = update_material._generate_corpus_enrichment_batch(sentences)

    assert out == {
        10: {"diacritized": "كَتَبَ الوَلَدُ", "translation": "The boy wrote."},
        11: {"diacritized": "", "translation": "The girl read."},
    }
    assert mock_generate.call_count == 1
    kwargs = mock_generate.call_args.kwargs
    assert kwargs["json_schema"] == update_material._CORPUS_ENRICH_SCHEMA
    assert kwargs["task_type"] == "corpus_enrichment"
    assert "id=10" in kwargs["prompt"]
    assert "id=11" in kwargs["prompt"]


@patch("app.services.llm.generate_completion")
def test_generate_corpus_enrichment_batch_returns_empty_on_provider_failure(mock_generate):
    mock_generate.side_effect = AllProvidersFailed("no provider")

    out = update_material._generate_corpus_enrichment_batch([
        SimpleNamespace(id=12, arabic_text="ذهب الرجل"),
    ])

    assert out == {}
