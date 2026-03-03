"""Tests for the due-date-tiered sentence pipeline allocation."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.services.pipeline_tiers import (
    DEFAULT_TIER,
    TIER_CONFIGS,
    WordTier,
    _classify_tier,
    _extract_due_datetime,
    build_tier_lookup,
    compute_word_tiers,
    tier_summary,
)


NOW = datetime(2026, 3, 3, 12, 0, 0, tzinfo=timezone.utc)


class TestClassifyTier:
    def test_overdue_is_tier_1(self):
        due = NOW - timedelta(hours=5)
        result = _classify_tier(due, NOW)
        assert result.tier == 1
        assert result.backfill_target == 3
        assert result.cap_floor == 2

    def test_due_now_is_tier_1(self):
        result = _classify_tier(NOW, NOW)
        assert result.tier == 1

    def test_due_in_6h_is_tier_1(self):
        due = NOW + timedelta(hours=6)
        result = _classify_tier(due, NOW)
        assert result.tier == 1

    def test_due_in_12h_is_tier_1(self):
        due = NOW + timedelta(hours=12)
        result = _classify_tier(due, NOW)
        assert result.tier == 1

    def test_due_in_13h_is_tier_2(self):
        due = NOW + timedelta(hours=13)
        result = _classify_tier(due, NOW)
        assert result.tier == 2
        assert result.backfill_target == 2
        assert result.cap_floor == 1

    def test_due_in_36h_is_tier_2(self):
        due = NOW + timedelta(hours=36)
        result = _classify_tier(due, NOW)
        assert result.tier == 2

    def test_due_in_37h_is_tier_3(self):
        due = NOW + timedelta(hours=37)
        result = _classify_tier(due, NOW)
        assert result.tier == 3
        assert result.backfill_target == 1
        assert result.cap_floor == 0

    def test_due_in_72h_is_tier_3(self):
        due = NOW + timedelta(hours=72)
        result = _classify_tier(due, NOW)
        assert result.tier == 3

    def test_due_in_73h_is_tier_4(self):
        due = NOW + timedelta(hours=73)
        result = _classify_tier(due, NOW)
        assert result.tier == 4
        assert result.backfill_target == 0
        assert result.cap_floor == 0

    def test_due_in_2_weeks_is_tier_4(self):
        due = NOW + timedelta(days=14)
        result = _classify_tier(due, NOW)
        assert result.tier == 4

    def test_none_due_is_tier_4(self):
        result = _classify_tier(None, NOW)
        assert result.tier == 4
        assert result == DEFAULT_TIER


class TestExtractDueDatetime:
    def _make_ulk(self, state, acq_due=None, fsrs_json=None):
        ulk = MagicMock()
        ulk.knowledge_state = state
        ulk.acquisition_next_due = acq_due
        ulk.fsrs_card_json = fsrs_json
        return ulk

    def test_acquiring_with_due(self):
        due = datetime(2026, 3, 3, 14, 0, 0, tzinfo=timezone.utc)
        ulk = self._make_ulk("acquiring", acq_due=due)
        assert _extract_due_datetime(ulk) == due

    def test_acquiring_naive_datetime(self):
        due = datetime(2026, 3, 3, 14, 0, 0)  # naive
        ulk = self._make_ulk("acquiring", acq_due=due)
        result = _extract_due_datetime(ulk)
        assert result.tzinfo == timezone.utc
        assert result == due.replace(tzinfo=timezone.utc)

    def test_acquiring_no_due(self):
        ulk = self._make_ulk("acquiring", acq_due=None)
        assert _extract_due_datetime(ulk) is None

    def test_fsrs_with_due_string(self):
        card = {"due": "2026-03-05T10:00:00+00:00", "stability": 5.0}
        ulk = self._make_ulk("known", fsrs_json=card)
        result = _extract_due_datetime(ulk)
        assert result == datetime(2026, 3, 5, 10, 0, 0, tzinfo=timezone.utc)

    def test_fsrs_with_z_suffix(self):
        card = {"due": "2026-03-05T10:00:00Z", "stability": 5.0}
        ulk = self._make_ulk("learning", fsrs_json=card)
        result = _extract_due_datetime(ulk)
        assert result == datetime(2026, 3, 5, 10, 0, 0, tzinfo=timezone.utc)

    def test_fsrs_json_string(self):
        card_str = json.dumps({"due": "2026-03-05T10:00:00+00:00", "stability": 5.0})
        ulk = self._make_ulk("known", fsrs_json=card_str)
        result = _extract_due_datetime(ulk)
        assert result == datetime(2026, 3, 5, 10, 0, 0, tzinfo=timezone.utc)

    def test_fsrs_no_card(self):
        ulk = self._make_ulk("known", fsrs_json=None)
        assert _extract_due_datetime(ulk) is None

    def test_fsrs_corrupted_json(self):
        ulk = self._make_ulk("known", fsrs_json="not json")
        assert _extract_due_datetime(ulk) is None

    def test_fsrs_empty_due(self):
        card = {"stability": 5.0}
        ulk = self._make_ulk("known", fsrs_json=card)
        assert _extract_due_datetime(ulk) is None


class TestBuildTierLookup:
    def test_lookup(self):
        tiers = [
            WordTier(lemma_id=1, due_dt=NOW, tier=1, backfill_target=3, cap_floor=2),
            WordTier(lemma_id=2, due_dt=NOW + timedelta(days=1), tier=2, backfill_target=2, cap_floor=1),
        ]
        lookup = build_tier_lookup(tiers)
        assert lookup[1].tier == 1
        assert lookup[2].tier == 2
        assert 3 not in lookup


class TestTierSummary:
    def test_summary_counts(self):
        tiers = [
            WordTier(lemma_id=1, due_dt=NOW, tier=1, backfill_target=3, cap_floor=2),
            WordTier(lemma_id=2, due_dt=NOW, tier=1, backfill_target=3, cap_floor=2),
            WordTier(lemma_id=3, due_dt=NOW + timedelta(hours=20), tier=2, backfill_target=2, cap_floor=1),
            WordTier(lemma_id=4, due_dt=NOW + timedelta(days=10), tier=4, backfill_target=0, cap_floor=0),
        ]
        result = tier_summary(tiers)
        assert result == {1: 2, 2: 1, 3: 0, 4: 1}


class TestTierConfigs:
    def test_tiers_are_ordered(self):
        for i, config in enumerate(TIER_CONFIGS):
            assert config.tier == i + 1

    def test_tier_1_most_generous(self):
        assert TIER_CONFIGS[0].backfill_target == 3
        assert TIER_CONFIGS[0].cap_floor == 2

    def test_tier_4_zero(self):
        assert TIER_CONFIGS[3].backfill_target == 0
        assert TIER_CONFIGS[3].cap_floor == 0

    def test_tier_boundaries_ascending(self):
        prev = 0
        for config in TIER_CONFIGS:
            if config.max_hours is not None:
                assert config.max_hours > prev
                prev = config.max_hours
