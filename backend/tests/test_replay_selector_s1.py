from scripts.replay_selector_s1 import compare, summarize


def _item(sentence_id, reason, due_ids, word_ids, quality=1.0):
    return {
        "sentence_id": sentence_id,
        "sentence_ids": [sentence_id],
        "words": [{"lemma_id": lemma_id} for lemma_id in word_ids],
        "selection_info": {
            "reason": reason,
            "due_lemma_ids": due_ids,
            "components": {"quality_multiplier": quality},
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
