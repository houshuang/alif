from datetime import datetime, timezone

from scripts.simulate_distributed_day_graduation import _peak_pending


def test_peak_pending_orders_completions_before_additions():
    first = datetime(2026, 7, 1, tzinfo=timezone.utc)
    second = datetime(2026, 7, 2, tzinfo=timezone.utc)
    assert _peak_pending([
        (first, 1),
        (second, 1),
        (second, -1),
    ]) == 1
