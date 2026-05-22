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


@patch("app.services.llm.generate_completion")
def test_generate_etymology_batch_normalizes_and_filters(mock_generate):
    mock_generate.return_value = {
        "words": [
            {"lemma_id": 1, "etymology": {
                "root_meaning": "writing",
                "derivation": "k-t-b = writing = book",
            }},
            # function word: empty etymology object, no derivation → dropped
            {"lemma_id": 2, "etymology": {}},
            # unrequested id → ignored
            {"lemma_id": 999, "etymology": {"derivation": "from nowhere"}},
        ]
    }

    out = enrichment._generate_etymology_batch(
        [_lemma(1, "كِتَاب", "noun", "book"), _lemma(2, "فِي", "particle", "in")],
        roots_by_id={},
    )

    assert set(out) == {1}
    # normalized to the full stable shape (missing fields → None, loanwords → [])
    assert out[1] == {
        "root_meaning": "writing",
        "pattern": None,
        "pattern_meaning": None,
        "derivation": "k-t-b = writing = book",
        "semantic_field": None,
        "cultural_note": None,
        "related_loanwords": [],
    }
    assert mock_generate.call_args.kwargs["json_schema"] == enrichment._ETYMOLOGY_BATCH_SCHEMA


@patch("app.services.llm.generate_completion")
def test_etymology_coherence_flags_only_mismatch(mock_generate):
    mock_generate.return_value = {
        "results": [
            {"lemma_id": 65, "coherent": False, "reason": "laptop != repentance"},
            {"lemma_id": 178, "coherent": True, "reason": "jacket matches jacket"},
        ]
    }

    incoherent = enrichment.verify_etymology_coherence_batch([
        (_lemma(65, "توب", "noun", "repentance, returning to God"),
         {"derivation": "From English 'laptop' (portable computer)",
          "semantic_field": "technology, computing"}),
        (_lemma(178, "جاكيت", "noun", "jacket"),
         {"derivation": "From English 'jacket'"}),
    ])

    assert incoherent == {65}


@patch("app.services.llm.generate_completion")
def test_etymology_coherence_fails_open_on_error(mock_generate):
    mock_generate.side_effect = RuntimeError("CLI down")

    result = enrichment.verify_etymology_coherence_batch([
        (_lemma(65, "توب", "noun", "repentance"), {"derivation": "From English 'laptop'"}),
    ])

    # None signals failure so callers keep the etymology rather than dropping it
    assert result is None


def test_etymology_coherence_skips_llm_when_no_derivation():
    # candidates with no usable derivation → empty set, no LLM call attempted
    result = enrichment.verify_etymology_coherence_batch([
        (_lemma(7, "مِن", "particle", "from"), {}),
    ])
    assert result == set()
