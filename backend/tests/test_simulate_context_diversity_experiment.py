from scripts.simulate_context_diversity_experiment import (
    context_jaccard,
)


def test_context_jaccard_distinguishes_redundant_and_distinct_contexts():
    assert context_jaccard({1, 2, 3}, {1, 2, 3}) == 1.0
    assert context_jaccard({1, 2}, {3, 4}) == 0.0
    assert context_jaccard({1, 2}, {2, 3}) == 1 / 3


def test_empty_contexts_are_not_claimed_as_diverse():
    assert context_jaccard(set(), set()) == 1.0
