"""Regression tests for standalone story generators' vocabulary accounting."""

from unittest.mock import patch

from scripts.benchmark_stories import analyze_compliance
from scripts.generate_story_claude import check_compliance
from scripts.generate_story_podcasts import map_story_to_lemma_ids

from app.services.sentence_validator import build_lemma_lookup


class _LookupLemma:
    def __init__(self, lemma_id, lemma_ar, lemma_ar_bare, forms_json=None):
        self.lemma_id = lemma_id
        self.lemma_ar = lemma_ar
        self.lemma_ar_bare = lemma_ar_bare
        self.forms_json = forms_json
        self.gates_completed_at = "2026-01-01"


def _collision_lookups():
    lemmas = [
        _LookupLemma(270, "نَاسٌ", "ناس"),
        _LookupLemma(
            3711,
            "نَسِيَ",
            "نسي",
            {"active_participle": "نَاسٍ"},
        ),
        _LookupLemma(2054, "قَدْ", "قد"),
        _LookupLemma(2189, "فَقْد", "فقد"),
    ]
    lookup = build_lemma_lookup(lemmas)
    return lookup, lookup


def test_claude_story_compliance_uses_exact_aliases():
    compliance_lookup, all_lookup = _collision_lookups()

    result = check_compliance(
        "أُنَاسٌ فَقَدْ",
        compliance_lookup,
        all_lookup,
        set(),
        set(),
    )

    assert result["content_total"] == 1
    assert result["content_known"] == 1
    assert result["unknown_words"] == []


def test_benchmark_story_compliance_uses_exact_aliases():
    compliance_lookup, all_lookup = _collision_lookups()

    result = analyze_compliance(
        "أُنَاسٌ فَقَدْ",
        compliance_lookup,
        all_lookup,
        set(),
        set(),
    )

    assert result["total_content_words"] == 1
    assert result["known_content_words"] == 1
    assert result["function_word_count"] == 1
    assert result["unknown_word_list"] == []


def test_story_compliance_uses_complete_lookup_for_source_conflicts():
    compliance_lookup = build_lemma_lookup([
        _LookupLemma(270, "نَاسٌ", "ناس"),
    ])
    all_lookup = build_lemma_lookup([
        _LookupLemma(270, "نَاسٌ", "ناس"),
        _LookupLemma(9999, "أُنَاسٌ", "أناس"),
    ])

    result = check_compliance(
        "أُنَاسٌ",
        compliance_lookup,
        all_lookup,
        set(),
        set(),
    )

    assert result["content_total"] == 1
    assert result["content_known"] == 0
    assert result["unknown_words"] == ["اناس"]


def test_unresolved_function_alias_is_not_treated_as_free_scaffold():
    collision_only = build_lemma_lookup([
        _LookupLemma(2189, "فَقْد", "فقد"),
    ])

    claude_result = check_compliance(
        "فَقَدْ",
        collision_only,
        collision_only,
        set(),
        set(),
    )
    benchmark_result = analyze_compliance(
        "فَقَدْ",
        collision_only,
        collision_only,
        set(),
        set(),
    )

    assert claude_result["content_total"] == 1
    assert claude_result["content_known"] == 0
    assert claude_result["unknown_words"] == ["فقد"]
    assert benchmark_result["total_content_words"] == 1
    assert benchmark_result["known_content_words"] == 0
    assert benchmark_result["function_word_count"] == 0
    assert benchmark_result["unknown_word_list"] == ["فقد"]


@patch("app.services.sentence_validator.build_comprehensive_lemma_lookup")
def test_story_podcast_persistence_keeps_exact_alias_identity(mock_build_lookup):
    lookup, _ = _collision_lookups()
    mock_build_lookup.return_value = lookup

    lemma_ids, sentences = map_story_to_lemma_ids(
        {"sentences": [{"arabic": "أُنَاسٌ فَقَدْ"}]},
        object(),
    )

    assert lemma_ids == [270]
    assert sentences[0]["word_mappings"] == [
        {
            "surface": "أُنَاسٌ",
            "lemma_id": 270,
            "is_function_word": False,
        },
        {
            "surface": "فَقَدْ",
            "lemma_id": 2054,
            "is_function_word": True,
        },
    ]
