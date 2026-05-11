from unittest.mock import patch

from app.models import Lemma
from app.services import lemma_enrichment as enrichment
from app.services.llm import AllProvidersFailed


def _lemma(lemma_id: int, arabic: str, pos: str, gloss: str) -> Lemma:
    return Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        pos=pos,
        gloss_en=gloss,
    )


@patch("app.services.llm.generate_completion")
def test_generate_forms_batch_uses_one_structured_call(mock_generate):
    mock_generate.return_value = {
        "words": [
            {
                "lemma_id": 1,
                "forms": {
                    "present": " يَكْتُبُ ",
                    "plural": "",
                    "unexpected": "drop",
                },
            },
            {"lemma_id": 2, "forms": {"gender": " f "}},
        ]
    }

    out = enrichment._generate_forms_batch(
        [
            _lemma(1, "كَتَبَ", "verb", "to write"),
            _lemma(2, "مَدْرَسَة", "noun", "school"),
        ]
    )

    assert out == {
        1: {"present": "يَكْتُبُ"},
        2: {"gender": "f"},
    }
    assert mock_generate.call_count == 1
    kwargs = mock_generate.call_args.kwargs
    assert kwargs["json_schema"] == enrichment._FORMS_BATCH_SCHEMA
    assert kwargs["task_type"] == "enrichment_forms"
    assert "lemma_id=1" in kwargs["prompt"]
    assert "lemma_id=2" in kwargs["prompt"]


@patch("app.services.llm.generate_completion")
def test_generate_forms_batch_accepts_direct_array_shape(mock_generate):
    mock_generate.return_value = [
        {"lemma_id": 3, "plural": "كُتُب", "bad": "drop"},
        {"lemma_id": 999, "plural": "ignore unrequested ids"},
    ]

    out = enrichment._generate_forms_batch([
        _lemma(3, "كِتَاب", "noun", "book"),
    ])

    assert out == {3: {"plural": "كُتُب"}}


@patch("app.services.llm.generate_completion")
def test_generate_forms_batch_returns_empty_on_provider_failure(mock_generate):
    mock_generate.side_effect = AllProvidersFailed("no provider")

    out = enrichment._generate_forms_batch([
        _lemma(4, "قَرَأَ", "verb", "to read"),
    ])

    assert out == {}


def test_generate_forms_single_wrapper_preserves_existing_api(monkeypatch):
    lemma = _lemma(5, "كَبِير", "adjective", "big")

    monkeypatch.setattr(
        enrichment,
        "_generate_forms_batch",
        lambda lemmas: {lemmas[0].lemma_id: {"feminine": "كَبِيرَة"}},
    )

    assert enrichment._generate_forms(lemma) == {"feminine": "كَبِيرَة"}
