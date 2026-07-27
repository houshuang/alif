import json
import sqlite3

from scripts.analyze_word_review_evidence import (
    summarize_interactions,
    summarize_rows,
)


def _row(**overrides):
    values = {
        "rating": 3,
        "client_review_id": "review-1",
        "sentence_word_id": 1,
        "canonical_lemma_id": 10,
        "review_log_id": 100,
        "front_initial_tashkeel_visible": 1,
        "front_ever_tashkeel_visible": 1,
        "failure_causes_json": None,
        "created_at": "2026-07-27 10:00:00",
        "fsrs_log_json": json.dumps({
            "pre_card": {"stability": 20.0},
        }),
    }
    values.update(overrides)
    return values


def _as_rows(items):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE evidence (
            rating INTEGER,
            client_review_id TEXT,
            sentence_word_id INTEGER,
            canonical_lemma_id INTEGER,
            review_log_id INTEGER,
            front_initial_tashkeel_visible INTEGER,
            front_ever_tashkeel_visible INTEGER,
            failure_causes_json TEXT,
            created_at TEXT,
            fsrs_log_json TEXT
        )
        """
    )
    for item in items:
        connection.execute(
            """
            INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(item.values()),
        )
    rows = connection.execute("SELECT * FROM evidence").fetchall()
    connection.close()
    return rows


def test_summary_keeps_success_denominator_and_cause_combinations():
    rows = _as_rows([
        _row(),
        _row(
            rating=2,
            client_review_id="review-2",
            sentence_word_id=2,
            front_initial_tashkeel_visible=0,
            front_ever_tashkeel_visible=1,
            failure_causes_json=json.dumps([
                "unfamiliar_form",
                "missing_tashkeel",
            ]),
            fsrs_log_json=json.dumps({
                "pre_card": {"stability": 40.0},
            }),
        ),
        _row(
            rating=1,
            client_review_id="review-3",
            sentence_word_id=3,
            front_initial_tashkeel_visible=0,
            front_ever_tashkeel_visible=0,
            review_log_id=None,
            fsrs_log_json=None,
        ),
    ])

    summary = summarize_rows(rows)

    assert summary["all_tokens"]["token_rows"] == 3
    assert summary["all_tokens"]["strict_unaided_success_rating3_pct"] == 33.33
    assert summary["initial_render"]["unvocalized"]["token_rows"] == 2
    assert (
        summary["front_vowel_reveal_after_initially_unvocalized"]["token_rows"]
        == 1
    )
    assert summary["cause_counts"]["missing_tashkeel"] == 1
    assert summary["integrity"]["rows_without_linked_review_log"] == 1


def test_interaction_summary_reports_dropped_rows_without_changing_reviews():
    summary = summarize_interactions(
        [
            {
                "ts": "2026-07-27T10:00:00Z",
                "event": "sentence_review",
                "word_evidence_protocol_version": 1,
                "word_evidence_count": 5,
                "word_evidence_saved": 4,
            },
            {
                "ts": "2026-07-27T11:00:00Z",
                "event": "sentence_review",
                "word_evidence_protocol_version": 2,
                "word_evidence_count": 5,
                "word_evidence_saved": 5,
            },
        ],
        since=None,
        cutoff=None,
    )

    assert summary == {
        "review_events": 1,
        "submitted_token_rows": 5,
        "saved_token_rows": 4,
        "dropped_token_rows": 1,
        "saved_pct_of_submitted": 80.0,
    }
