import json
import sqlite3
from datetime import datetime, timedelta, timezone

from scripts.analyze_lapse_recovery import (
    LapseEvent,
    load_current_lapsed_stock,
    load_events,
    summarize_events,
)


def _dt(day: int, minute: int = 0):
    return datetime(2026, 7, day, tzinfo=timezone.utc) + timedelta(
        minutes=minute
    )


def test_followup_summary_is_right_censored_and_rating2_is_not_spontaneous():
    events = [
        LapseEvent(1, _dt(1), 30.0, "primary", _dt(1, 5), 3),
        LapseEvent(2, _dt(2), 20.0, "collateral", _dt(2, 6), 2),
        LapseEvent(3, _dt(9), 40.0, "primary", None, None),
    ]

    result = summarize_events(
        events,
        cutoff=_dt(10),
        period_start=_dt(1),
        horizon=timedelta(days=2),
    )

    assert result["eligible_lapses"] == 2
    assert result["followed_up"] == 2
    assert result["followup_rate_pct"] == 100.0
    assert result["assisted_recognition_rating2_pct_of_followups"] == 50.0
    assert (
        result["spontaneous_retrieval_rating3_or_4_pct_of_followups"]
        == 50.0
    )


def _analysis_connection():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE review_log (
            id INTEGER PRIMARY KEY,
            lemma_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            reviewed_at TEXT NOT NULL,
            fsrs_log_json TEXT,
            credit_type TEXT,
            is_acquisition INTEGER
        );
        CREATE TABLE user_lemma_knowledge (
            lemma_id INTEGER PRIMARY KEY,
            knowledge_state TEXT NOT NULL,
            fsrs_card_json TEXT
        );
        """
    )
    return connection


def test_first_followup_includes_acquisition_reintroduction():
    connection = _analysis_connection()
    connection.executemany(
        """
        INSERT INTO review_log
            (id, lemma_id, rating, reviewed_at, fsrs_log_json, is_acquisition)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1,
                10,
                1,
                "2026-07-01 10:00:00",
                json.dumps({"pre_card": {"stability": 30}}),
                0,
            ),
            (2, 10, 3, "2026-07-01 10:05:00", None, 1),
            (3, 10, 4, "2026-07-03 10:00:00", None, 0),
        ],
    )

    events = load_events(connection, _dt(1), _dt(4))

    assert len(events) == 1
    assert events[0].next_reviewed_at == _dt(1, 10 * 60 + 5)
    assert events[0].next_rating == 3


def test_current_lapsed_stock_uses_latest_lapse_before_analysis_window():
    connection = _analysis_connection()
    connection.execute(
        """
        INSERT INTO user_lemma_knowledge
            (lemma_id, knowledge_state, fsrs_card_json)
        VALUES (?, ?, ?)
        """,
        (
            10,
            "lapsed",
            json.dumps({"due": "2026-07-01T00:00:00+00:00"}),
        ),
    )
    connection.execute(
        """
        INSERT INTO review_log
            (id, lemma_id, rating, reviewed_at, fsrs_log_json, is_acquisition)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            10,
            1,
            "2026-02-28 10:00:00",
            json.dumps({"pre_card": {"stability": 45}}),
            0,
        ),
    )

    stock = load_current_lapsed_stock(connection, _dt(2))

    assert stock["lapsed_words"] == 1
    assert stock["lapsed_due_now"] == 1
    assert stock["established_pre_stability_ge_7d"] == 1
    assert stock["older_pre_stability_ge_30d"] == 1
