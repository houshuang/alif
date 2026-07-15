"""Tests for the recovery/Momo stats-panel additions (2026-07-15).

Covers: recovery_status snapshot, recovery-aware daily goal target,
token-weighted book coverage, and the due-backlog history parser.
"""

import json
from datetime import datetime, timezone, timedelta

from app.models import Lemma, UserLemmaKnowledge, ReviewLog
from app.routers.stats import _get_daily_goal, _get_due_backlog_history
from app.services.acquisition_service import (
    DAILY_INTRO_CAP,
    RECOVERY_BOX1_UNREVIEWED_LIMIT,
    recovery_status,
)
from app.services.book_coverage import compute_book_coverage


def _lemma(db, bare, **kwargs):
    lemma = Lemma(lemma_ar=bare, lemma_ar_bare=bare, gloss_en=f"gloss {bare}", **kwargs)
    db.add(lemma)
    db.flush()
    return lemma


def _ulk(db, lemma_id, state, box=None, source="study", times_seen=0, due_offset_h=-1):
    now = datetime.now(timezone.utc)
    ulk = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        acquisition_box=box,
        acquisition_next_due=(
            now + timedelta(hours=due_offset_h) if box is not None else None
        ),
        acquisition_started_at=now if box is not None else None,
        source=source,
        times_seen=times_seen,
    )
    db.add(ulk)
    db.flush()
    return ulk


class TestRecoveryStatus:
    def test_quiet_db_not_active_full_budget(self, db_session):
        status = recovery_status(db_session)
        assert status["active"] is False
        assert status["intro_budget_today"] == DAILY_INTRO_CAP
        assert status["box1_trigger_limit"] == RECOVERY_BOX1_UNREVIEWED_LIMIT
        assert status["main_fsrs_limit"] == 750

    def test_box1_debt_activates_and_gates_intros(self, db_session):
        for i in range(RECOVERY_BOX1_UNREVIEWED_LIMIT + 1):
            lemma = _lemma(db_session, f"debt{i}")
            _ulk(db_session, lemma.lemma_id, "acquiring", box=1)
        db_session.commit()

        status = recovery_status(db_session)
        assert status["active"] is True
        assert status["box1_actionable"] >= RECOVERY_BOX1_UNREVIEWED_LIMIT
        # No primary reading cards today -> earn-in not met -> zero budget.
        assert status["reading_cards_today"] == 0
        assert status["intro_budget_today"] == 0


class TestRecoveryAwareDailyGoal:
    def test_zero_target_is_trivially_met_and_flagged(self, db_session):
        goal = _get_daily_goal(db_session, new_word_target=0)
        assert goal.new_words_target == 0
        assert goal.intake_gated is True
        assert goal.new_words_pct == 100.0
        # Headline must reflect maintenance, not the gated intro target.
        assert goal.overall_pct == goal.maintenance_pct

    def test_full_target_not_flagged(self, db_session):
        goal = _get_daily_goal(db_session, new_word_target=DAILY_INTRO_CAP)
        assert goal.new_words_target == 30
        assert goal.intake_gated is False

    def test_default_target_comes_from_recovery_budget(self, db_session):
        # Quiet DB: budget is the full cap, so behavior matches the old static
        # target and nothing is flagged as gated.
        goal = _get_daily_goal(db_session)
        assert goal.new_words_target == 30
        assert goal.intake_gated is False


class TestBookCoverage:
    def _write_tokenmap(self, tmp_path, mapped, unmapped, total, function):
        payload = {
            "title": "TestBook",
            "target_pct": 95.0,
            "total": total,
            "function": function,
            "mapped": {str(k): v for k, v in mapped.items()},
            "unmapped_freq": unmapped,
        }
        (tmp_path / "book_test_tokenmap.json").write_text(
            json.dumps(payload, ensure_ascii=False)
        )

    def test_token_weighted_buckets(self, db_session, tmp_path):
        known = _lemma(db_session, "knownword")
        _ulk(db_session, known.lemma_id, "known")
        acquiring = _lemma(db_session, "acqword", )
        _ulk(db_session, acquiring.lemma_id, "acquiring", box=1)
        gap = _lemma(db_session, "gapword")  # in vocab, never started
        resolves = _lemma(db_session, "lateimport")  # imported after the scan
        db_session.commit()

        # 7 function + 10 known + 5 acquiring + 3 gap + 2 late-import + 4 OOV
        self._write_tokenmap(
            tmp_path,
            mapped={known.lemma_id: 10, acquiring.lemma_id: 5, gap.lemma_id: 3},
            unmapped={"lateimport": 2, "neverheard": 4},
            total=31,
            function=7,
        )

        results = compute_book_coverage(db_session, benchmarks_dir=tmp_path)
        assert len(results) == 1
        book = results[0]
        assert book.title == "TestBook"
        assert book.covered_tokens == 17  # function 7 + known 10
        assert book.in_progress_tokens == 5
        # gap lemma (3) + late import resolved to a lemma with no ULK (2)
        assert book.gap_tokens == 5
        assert book.unmapped_tokens == 4
        assert book.covered_pct == round(17 / 31 * 100, 1)
        assert book.in_progress_pct == round(22 / 31 * 100, 1)

        gap_displays = {g.display for g in book.top_gaps}
        assert "gapword" in gap_displays
        assert "lateimport" in gap_displays
        assert "neverheard" in gap_displays  # unresolved OOV padding

    def test_inert_lemma_counts_as_covered(self, db_session, tmp_path):
        name = _lemma(db_session, "propername", word_category="proper_name")
        db_session.commit()
        self._write_tokenmap(
            tmp_path, mapped={name.lemma_id: 5}, unmapped={}, total=10, function=5
        )
        book = compute_book_coverage(db_session, benchmarks_dir=tmp_path)[0]
        assert book.covered_tokens == 10
        assert book.gap_tokens == 0

    def test_bookifier_cohort_funnel(self, db_session, tmp_path):
        box1 = _lemma(db_session, "cohort1")
        _ulk(db_session, box1.lemma_id, "acquiring", box=1, source="bookifier")
        graduated = _lemma(db_session, "cohort2")
        _ulk(db_session, graduated.lemma_id, "learning", source="bookifier")
        other = _lemma(db_session, "other")
        _ulk(db_session, other.lemma_id, "acquiring", box=1, source="study")
        db_session.commit()

        self._write_tokenmap(
            tmp_path, mapped={box1.lemma_id: 1}, unmapped={}, total=2, function=1
        )
        book = compute_book_coverage(db_session, benchmarks_dir=tmp_path)[0]
        assert book.cohort is not None
        assert book.cohort.source == "bookifier"
        assert book.cohort.total == 2
        assert book.cohort.box_1 == 1
        assert book.cohort.learning == 1

    def test_no_tokenmaps_returns_empty(self, db_session, tmp_path):
        assert compute_book_coverage(db_session, benchmarks_dir=tmp_path) == []


class TestDueBacklogHistory:
    def test_parses_daily_peak_from_session_start(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "log_dir", tmp_path)
        today = datetime.now(timezone.utc).date().isoformat()
        lines = [
            {"event": "session_start", "total_due_words": 900},
            {"event": "card_shown", "card_type": "sentence"},
            {"event": "session_start", "total_due_words": 950},
            {"event": "session_start"},  # legacy event without the field
        ]
        (tmp_path / f"interactions_{today}.jsonl").write_text(
            "\n".join(json.dumps(l) for l in lines) + "\n"
        )

        history = _get_due_backlog_history(days=3)
        assert history == {today: 950}

    def test_missing_files_are_skipped(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "log_dir", tmp_path)
        assert _get_due_backlog_history(days=3) == {}
