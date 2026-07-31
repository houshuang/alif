import pytest

from scripts.analyze_exact_surface_experiment import (
    _clustered_risk_difference_interval,
    _fisher_two_sided,
)


def test_fisher_two_sided_known_table():
    assert _fisher_two_sided(8, 10, 2, 10) == pytest.approx(
        0.023014137565221155
    )


def test_clustered_interval_resamples_lemmas_not_rows():
    episodes = []
    for lemma_id in range(1, 21):
        episodes.extend([
            {
                "lemma_id": lemma_id,
                "arm": "control",
                "exact_itt_success": False,
            },
            {
                "lemma_id": lemma_id,
                "arm": "treatment",
                "exact_itt_success": True,
            },
        ])

    interval = _clustered_risk_difference_interval(
        episodes,
        simulations=500,
        seed=7,
    )

    assert interval == (1.0, 1.0)
