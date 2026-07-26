from scripts.replay_selector_s1 import compare, public_summary, summarize


def _item(
    sentence_id,
    reason,
    due_ids,
    word_ids,
    quality=1.0,
    recovery_lemma_id=None,
):
    components = {"quality_multiplier": quality}
    return {
        "sentence_id": sentence_id,
        "sentence_ids": [sentence_id],
        "primary_lemma_id": recovery_lemma_id,
        "words": [
            {
                "lemma_id": lemma_id,
                "canonical_lemma_id": None,
                "is_function_word": False,
                "is_proper_name": False,
            }
            for lemma_id in word_ids
        ],
        "selection_info": {
            "reason": reason,
            "due_lemma_ids": due_ids,
            "components": components,
        },
    }


def test_summary_counts_all_sentence_words_regardless_of_primary_role():
    result = {
        "items": [
            _item(1, "frequency_due_first_s1", [10], [10, 20, 30]),
            _item(2, "greedy_cover", [20, 40], [20, 40, 50]),
            _item(3, "acquisition_repeat", [60], [60, 70]),
        ],
        "total_due_words": 100,
    }
    summary = summarize(result, limit=5)
    assert summary["distinct_due_words_covered"] == 4
    assert summary["distinct_all_words_presented"] == 7
    assert summary["base_distinct_due_words_covered"] == 3
    assert summary["base_distinct_all_words_presented"] == 5
    assert summary["opening_cards"] == 1


def test_summary_reports_established_lapse_recovery():
    summary = summarize(
        {
            "items": [
                _item(
                    1,
                    "established_lapse_recovery_v1",
                    [10],
                    [10, 20],
                    recovery_lemma_id=10,
                ),
            ],
        },
        limit=5,
    )
    assert summary["established_lapse_recovery_cards"] == 1
    assert summary["established_lapse_recovery_lemma_ids"] == [10]


def test_summary_counts_canonical_creditable_content_only():
    result = {
        "items": [{
            **_item(1, "greedy_cover", [10], [10]),
            "words": [
                {
                    "lemma_id": 10,
                    "canonical_lemma_id": 1,
                    "is_function_word": False,
                    "is_proper_name": False,
                },
                {
                    "lemma_id": 11,
                    "canonical_lemma_id": 1,
                    "is_function_word": False,
                    "is_proper_name": False,
                },
                {
                    "lemma_id": 12,
                    "canonical_lemma_id": None,
                    "is_function_word": True,
                    "is_proper_name": False,
                },
            ],
        }],
    }

    summary = summarize(result, limit=5)

    assert summary["distinct_all_words_presented"] == 1


def test_public_summary_removes_stable_content_identifiers():
    summary = summarize(
        {
            "items": [
                _item(
                    99,
                    "established_lapse_recovery_v1",
                    [10],
                    [10, 20],
                    recovery_lemma_id=10,
                ),
            ],
        },
        limit=5,
    )

    public = public_summary(summary)

    assert "sentence_ids" not in public
    assert "due_lemma_ids" not in public
    assert "established_lapse_recovery_lemma_ids" not in public
    assert public["established_lapse_recovery_cards"] == 1


def test_comparison_reports_coverage_regression():
    s0 = summarize(
        {"items": [_item(1, "greedy_cover", [1, 2], [1, 2, 3])]},
        limit=5,
    )
    s1 = summarize(
        {"items": [_item(2, "frequency_due_first_s1", [1], [1, 4])]},
        limit=5,
    )
    comparison = compare(s0, s1)
    assert comparison["due_coverage_delta"] == -1
    assert comparison["all_word_breadth_delta"] == -1
