"""Tests for the deterministic, strict read-only WP0/WP1 analysis command."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND_DIR / "scripts" / "analyze_learning_system.py"
INTAKE_SCRIPT = BACKEND_DIR / "scripts" / "preview_intake_impact.py"
COHORT_SCRIPT = BACKEND_DIR / "scripts" / "analyze_intake_cohort.py"
GRADUATION_SCRIPT = BACKEND_DIR / "scripts" / "analyze_graduation_retention.py"
ACQUISITION_REPLAY_SCRIPT = (
    BACKEND_DIR / "scripts" / "replay_acquisition_evidence.py"
)
FSRS_SEGMENT_SCRIPT = (
    BACKEND_DIR / "scripts" / "analyze_fsrs_calibration_segments.py"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_snapshot(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE lemmas (
            lemma_id INTEGER PRIMARY KEY,
            lemma_ar_bare TEXT,
            canonical_lemma_id INTEGER,
            word_category TEXT,
            frequency_rank INTEGER,
            source TEXT
        );
        CREATE TABLE user_lemma_knowledge (
            lemma_id INTEGER PRIMARY KEY,
            knowledge_state TEXT,
            fsrs_card_json TEXT,
            times_seen INTEGER,
            times_correct INTEGER,
            acquisition_box INTEGER,
            acquisition_next_due TEXT,
            acquisition_started_at TEXT,
            entered_acquiring_at TEXT,
            graduated_at TEXT,
            leech_suspended_at TEXT,
            source TEXT,
            generation_backoff_until TEXT
        );
        CREATE TABLE frequency_core_entries (
            id INTEGER PRIMARY KEY,
            lemma_id INTEGER,
            core_rank INTEGER,
            excluded_reason TEXT
        );
        CREATE TABLE review_log (
            id INTEGER PRIMARY KEY,
            lemma_id INTEGER,
            rating INTEGER,
            reviewed_at TEXT,
            review_mode TEXT,
            sentence_id INTEGER,
            credit_type TEXT,
            is_acquisition INTEGER,
            fsrs_log_json TEXT,
            client_review_id TEXT,
            session_id TEXT
        );
        """
    )
    connection.executemany(
        """
        INSERT INTO lemmas
            (lemma_id, lemma_ar_bare, canonical_lemma_id, word_category,
             frequency_rank, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "كلمة", None, None, 100, "frequency_core"),
            (2, "كتاب", None, None, 200, "frequency_core"),
            (3, "زيد", None, "proper_name", None, "book"),
        ],
    )
    fsrs_card = json.dumps(
        {
            "card_id": 2,
            "state": 2,
            "step": None,
            "stability": 30.0,
            "difficulty": 5.0,
            "due": "2026-07-20T12:00:00+00:00",
            "last_review": "2026-06-20T12:00:00+00:00",
        }
    )
    connection.executemany(
        """
        INSERT INTO user_lemma_knowledge
            (lemma_id, knowledge_state, fsrs_card_json, times_seen, times_correct,
             acquisition_box, acquisition_next_due, acquisition_started_at,
             entered_acquiring_at, graduated_at, source, generation_backoff_until)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1, "acquiring", None, 0, 0, 1, "2026-07-26 00:00:00",
                "2026-07-20 00:00:00", "2026-07-20 00:00:00", None,
                "frequency_core", None,
            ),
            (
                2, "known", fsrs_card, 10, 9, None, None,
                "2026-07-01 00:00:00", "2026-07-01 00:00:00",
                "2026-07-10 00:00:00", "frequency_core", None,
            ),
            (
                3, "known", fsrs_card, 10, 10, None, None,
                None, None, None, "book", None,
            ),
        ],
    )
    connection.execute(
        "INSERT INTO frequency_core_entries (id, lemma_id, core_rank) VALUES (1, 2, 200)"
    )
    pre_card = json.dumps({"pre_card": json.loads(fsrs_card)})
    connection.executemany(
        """
        INSERT INTO review_log
            (id, lemma_id, rating, reviewed_at, review_mode, sentence_id,
             credit_type, is_acquisition, fsrs_log_json, client_review_id, session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1, 1, 1, "2026-07-25 16:00:00", "reading", 10,
                "primary", 1, None, "r1", "s1",
            ),
            (
                2, 2, 3, "2026-07-25 16:00:00", "reading", 10,
                "collateral", 0, pre_card, "r2", "s1",
            ),
            (
                3, 1, 3, "2026-07-25 16:10:00", "quiz", None,
                None, 1, None, "r3", "s1",
            ),
            (
                4, 3, 3, "2026-07-25 16:00:00", "reading", 10,
                "collateral", 0, pre_card, "r4", "s1",
            ),
        ],
    )
    connection.commit()
    connection.close()


def _make_logs(path: Path) -> None:
    path.mkdir()
    events = [
        {
            "ts": "2026-07-25T15:50:00+00:00",
            "event": "card_shown",
            "session_id": "s1",
            "card_type": "sentence",
            "card_index": 0,
            "total_cards": 2,
        },
        {
            "ts": "2026-07-25T15:55:00+00:00",
            "event": "card_shown",
            "session_id": "s1",
            "card_type": "sentence",
            "card_index": 1,
            "total_cards": 2,
        },
        {
            "ts": "2026-07-25T16:00:00+00:00",
            "event": "sentence_review",
            "session_id": "s1",
            "word_ratings": {"1": 1, "2": 1},
        },
        {
            "ts": "2026-07-25T16:01:00+00:00",
            "event": "card_shown",
            "session_id": "s1",
            "card_type": "wrapup",
            "card_index": 0,
            "total_cards": 2,
            "detail": {"variant": "wrapup_auto", "v": 2},
        },
        {
            "ts": "2026-07-25T16:02:00+00:00",
            "event": "card_shown",
            "session_id": "s2",
            "card_type": "sentence",
            "card_index": 0,
            "total_cards": 2,
        },
        {
            "ts": "2026-07-25T16:03:00+00:00",
            "event": "sentence_review",
            "session_id": "s2",
            "word_ratings": {"1": 2},
        },
    ]
    (path / "interactions_2026-07-25.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )


def _run(snapshot: Path, logs: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(BACKEND_DIR / ".venv" / "bin" / "python"),
            str(SCRIPT),
            "--db",
            str(snapshot),
            "--interaction-log-dir",
            str(logs),
            "--window-start",
            "2026-07-01T00:00:00Z",
            "--session-window-start",
            "2026-07-05T00:00:00Z",
            "--cutoff",
            "2026-07-25T17:00:00Z",
            "--output-dir",
            str(output),
            "--strict-read-only",
        ],
        cwd=BACKEND_DIR,
        text=True,
        capture_output=True,
    )


def test_analysis_is_read_only_deterministic_and_preserves_credit_semantics(tmp_path):
    snapshot = tmp_path / "snapshot.db"
    logs = tmp_path / "logs"
    _make_snapshot(snapshot)
    _make_logs(logs)
    before_hash = _sha256(snapshot)
    before_mtime = snapshot.stat().st_mtime_ns

    first = tmp_path / "first"
    second = tmp_path / "second"
    result1 = _run(snapshot, logs, first)
    result2 = _run(snapshot, logs, second)
    assert result1.returncode == 0, result1.stderr
    assert result2.returncode == 0, result2.stderr

    assert _sha256(snapshot) == before_hash
    assert snapshot.stat().st_mtime_ns == before_mtime
    assert not any(Path(str(snapshot) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))

    files1 = {path.name: path.read_bytes() for path in first.iterdir()}
    files2 = {path.name: path.read_bytes() for path in second.iterdir()}
    assert files1 == files2

    summary = json.loads(files1["summary.json"])
    assert summary["reviews"]["valid_word_reviews"] == 3
    assert summary["reviews"]["distinct_canonical_words"] == 2
    assert summary["reviews"]["sentence_word_reviews"] == 2
    assert summary["reviews"]["sentence_credit"] == {"collateral": 1, "primary": 1}
    assert summary["reviews"]["invalid_rows"] == {"inert_category": 1}

    # Primary and collateral are both valid sentence outcomes. Quiz stays a
    # separate valid review and does not silently become a sentence outcome.
    assert summary["reviews"]["review_modes"] == {"quiz": 1, "reading": 2}
    assert summary["current_state"]["recovery"]["values"]["box1_actionable"] == 1
    assert summary["current_state"]["recovery"]["values"]["strict_main_fsrs_due"] == 1
    assert summary["fsrs_calibration"]["by_stability"][">=30d"]["reviews"] == 1

    assert summary["sessions"]["analyzable_sessions"] == 2
    assert summary["sessions"]["approximately_complete"] == 1
    assert summary["sessions"]["complete_distinct_rating1_median"] == 2
    assert summary["sessions"]["auto_wrap_card_sizes"] == [2]
    assert summary["sessions"]["protocol_v2_first_telemetry"] == "2026-07-25T16:01:00Z"


def test_strict_mode_rejects_sqlite_sidecars(tmp_path):
    snapshot = tmp_path / "snapshot.db"
    logs = tmp_path / "logs"
    _make_snapshot(snapshot)
    _make_logs(logs)
    Path(str(snapshot) + "-wal").write_bytes(b"not-a-stable-snapshot")

    output = tmp_path / "output"
    result = _run(snapshot, logs, output)
    assert result.returncode != 0
    assert "without sidecars" in result.stderr
    assert not output.exists()


def test_intake_preview_classifies_without_mutating_snapshot(tmp_path):
    snapshot = tmp_path / "snapshot.db"
    logs = tmp_path / "logs"
    _make_snapshot(snapshot)
    _make_logs(logs)
    baseline_dir = tmp_path / "baseline"
    baseline = _run(snapshot, logs, baseline_dir)
    assert baseline.returncode == 0, baseline.stderr

    candidates = tmp_path / "candidates.json"
    candidates.write_text(
        json.dumps([1, 2, 3, 999, {"arabic": "new unresolved word"}]),
        encoding="utf-8",
    )
    before = _sha256(snapshot)
    outputs = []
    for name in ("preview1", "preview2"):
        output = tmp_path / name
        result = subprocess.run(
            [
                str(BACKEND_DIR / ".venv" / "bin" / "python"),
                str(INTAKE_SCRIPT),
                "--db", str(snapshot),
                "--cutoff", "2026-07-25T17:00:00Z",
                "--candidate-file", str(candidates),
                "--current-baseline-summary", str(baseline_dir / "summary.json"),
                "--output-dir", str(output),
            ],
            cwd=BACKEND_DIR,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        outputs.append({path.name: path.read_bytes() for path in output.iterdir()})

    assert outputs[0] == outputs[1]
    assert _sha256(snapshot) == before
    preview = json.loads(outputs[0]["preview.json"])
    assert preview["classification"]["categories"] == {
        "already_in_training": 1,
        "already_learned": 1,
        "inert_not_eligible": 1,
        "unresolved_or_not_yet_created": 2,
    }
    assert preview["classification"]["projected_box1_additions"] == 2
    assert preview["scenarios"][0]["scenario"] == "immediate"
    assert preview["limitations"][-1] == "does not apply or stage intake"

    mismatched_summary = tmp_path / "mismatched-summary.json"
    summary = json.loads((baseline_dir / "summary.json").read_text(encoding="utf-8"))
    summary["provenance"]["database"]["sha256"] = "0" * 64
    mismatched_summary.write_text(json.dumps(summary), encoding="utf-8")
    mismatch = subprocess.run(
        [
            str(BACKEND_DIR / ".venv" / "bin" / "python"),
            str(INTAKE_SCRIPT),
            "--db", str(snapshot),
            "--cutoff", "2026-07-25T17:00:00Z",
            "--candidate-file", str(candidates),
            "--current-baseline-summary", str(mismatched_summary),
            "--output-dir", str(tmp_path / "mismatched-preview"),
        ],
        cwd=BACKEND_DIR,
        text=True,
        capture_output=True,
    )
    assert mismatch.returncode != 0
    assert "baseline summary and database snapshot do not match" in mismatch.stderr


def test_intake_cohort_audit_is_deterministic_and_counts_all_credit(tmp_path):
    snapshot = tmp_path / "snapshot.db"
    logs = tmp_path / "logs"
    _make_snapshot(snapshot)
    _make_logs(logs)
    baseline_dir = tmp_path / "baseline"
    baseline = _run(snapshot, logs, baseline_dir)
    assert baseline.returncode == 0, baseline.stderr

    before = _sha256(snapshot)
    outputs = []
    for name in ("cohort1", "cohort2"):
        output = tmp_path / name
        result = subprocess.run(
            [
                str(BACKEND_DIR / ".venv" / "bin" / "python"),
                str(COHORT_SCRIPT),
                "--db", str(snapshot),
                "--cutoff", "2026-07-25T17:00:00Z",
                "--start", "2026-07-01T00:00:00Z",
                "--end", "2026-07-21T00:00:00Z",
                "--source", "frequency_core",
                "--current-baseline-summary", str(baseline_dir / "summary.json"),
                "--output-dir", str(output),
            ],
            cwd=BACKEND_DIR,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        outputs.append({path.name: path.read_bytes() for path in output.iterdir()})

    assert outputs[0] == outputs[1]
    assert _sha256(snapshot) == before
    cohort = json.loads(outputs[0]["cohort.json"])
    assert cohort["summary"]["cohort_size"] == 2
    assert cohort["summary"]["all_review_rows"] == 3
    assert cohort["summary"]["review_credit_types"] == {
        "collateral": 1,
        "primary": 1,
        "unknown": 1,
    }
    assert cohort["limitations"][-1] == (
        "all primary and collateral word reviews count equally"
    )

    preview_dir = tmp_path / "preview-with-reference"
    preview = subprocess.run(
        [
            str(BACKEND_DIR / ".venv" / "bin" / "python"),
            str(INTAKE_SCRIPT),
            "--db", str(snapshot),
            "--cutoff", "2026-07-25T17:00:00Z",
            "--anonymous-candidate-count", "2",
            "--current-baseline-summary", str(baseline_dir / "summary.json"),
            "--reference-cohort-json", str(tmp_path / "cohort1" / "cohort.json"),
            "--output-dir", str(preview_dir),
        ],
        cwd=BACKEND_DIR,
        text=True,
        capture_output=True,
    )
    assert preview.returncode == 0, preview.stderr
    preview_result = json.loads(
        (preview_dir / "preview.json").read_text(encoding="utf-8")
    )
    assert preview_result["empirical_reference_cohort"]["summary"]["cohort_size"] == 2


def test_graduation_retention_audit_is_deterministic_with_empty_window(tmp_path):
    snapshot = tmp_path / "snapshot.db"
    logs = tmp_path / "logs"
    _make_snapshot(snapshot)
    _make_logs(logs)
    baseline_dir = tmp_path / "baseline"
    baseline = _run(snapshot, logs, baseline_dir)
    assert baseline.returncode == 0, baseline.stderr

    before_hash = _sha256(snapshot)
    before_mtime = snapshot.stat().st_mtime_ns
    outputs = []
    for name in ("graduation1", "graduation2"):
        output = tmp_path / name
        result = subprocess.run(
            [
                str(BACKEND_DIR / ".venv" / "bin" / "python"),
                str(GRADUATION_SCRIPT),
                "--db", str(snapshot),
                "--cutoff", "2026-07-25T17:00:00Z",
                "--window-start", "2026-07-21T00:00:00Z",
                "--current-baseline-summary", str(baseline_dir / "summary.json"),
                "--output-dir", str(output),
            ],
            cwd=BACKEND_DIR,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        outputs.append({path.name: path.read_bytes() for path in output.iterdir()})

    assert outputs[0] == outputs[1]
    assert _sha256(snapshot) == before_hash
    assert snapshot.stat().st_mtime_ns == before_mtime
    audit = json.loads(outputs[0]["graduation_retention.json"])
    assert audit["summary"]["graduations"] == 0
    assert audit["by_reason"] == []
    assert outputs[0]["by_reason.csv"].startswith(b"reason,graduations,")


def test_acquisition_evidence_replay_is_deterministic_and_spacing_aware(tmp_path):
    snapshot = tmp_path / "snapshot.db"
    logs = tmp_path / "logs"
    _make_snapshot(snapshot)
    _make_logs(logs)
    connection = sqlite3.connect(snapshot)
    connection.executemany(
        """
        INSERT INTO review_log
            (id, lemma_id, rating, reviewed_at, review_mode, sentence_id,
             credit_type, is_acquisition, fsrs_log_json, client_review_id,
             session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                5, 1, 3, "2026-07-22 10:00:00", "reading", 11,
                "collateral", 1,
                json.dumps({
                    "pre_times_seen": 1,
                    "pre_times_correct": 1,
                    "acquisition_box_before": 1,
                    "acquisition_box_after": 2,
                    "graduated": False,
                    "retest_credit_blocked": False,
                }),
                "a:1", "prior-session",
            ),
            (
                6, 1, 3, "2026-07-22 10:20:00", "reading", 12,
                "primary", 1,
                json.dumps({
                    "pre_times_seen": 2,
                    "pre_times_correct": 2,
                    "acquisition_box_before": 2,
                    "acquisition_box_after": None,
                    "graduated": True,
                    "graduation_reason": "perfect_accuracy",
                    "retest_credit_blocked": False,
                }),
                "b:1", "graduating-session",
            ),
        ],
    )
    connection.commit()
    connection.close()

    baseline_dir = tmp_path / "baseline"
    baseline = _run(snapshot, logs, baseline_dir)
    assert baseline.returncode == 0, baseline.stderr
    before = _sha256(snapshot)
    outputs = []
    for name in ("replay1", "replay2"):
        output = tmp_path / name
        result = subprocess.run(
            [
                str(BACKEND_DIR / ".venv" / "bin" / "python"),
                str(ACQUISITION_REPLAY_SCRIPT),
                "--db", str(snapshot),
                "--cutoff", "2026-07-25T17:00:00Z",
                "--window-start", "2026-07-21T00:00:00Z",
                "--current-baseline-summary", str(baseline_dir / "summary.json"),
                "--output-dir", str(output),
            ],
            cwd=BACKEND_DIR,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        outputs.append({path.name: path.read_bytes() for path in output.iterdir()})

    assert outputs[0] == outputs[1]
    assert _sha256(snapshot) == before
    replay = json.loads(outputs[0]["acquisition_evidence_replay.json"])
    assert replay["integrity"]["counter_mismatches"] == 0
    policies = {
        row["policy"]: row for row in replay["policy_results"]
    }
    assert policies["logged_current"]["would_defer_at_logged_decision"] == 0
    assert (
        policies["prior_success_10m_other_session"][
            "would_defer_at_logged_decision"
        ]
        == 0
    )
    assert policies["prior_success_12h"]["would_defer_at_logged_decision"] == 1
    assert (
        policies["prior_success_prior_utc_day"][
            "would_defer_at_logged_decision"
        ]
        == 1
    )


def test_segmented_fsrs_calibration_is_deterministic_and_read_only(tmp_path):
    snapshot = tmp_path / "snapshot.db"
    logs = tmp_path / "logs"
    _make_snapshot(snapshot)
    _make_logs(logs)
    baseline_dir = tmp_path / "baseline"
    baseline = _run(snapshot, logs, baseline_dir)
    assert baseline.returncode == 0, baseline.stderr

    before = _sha256(snapshot)
    outputs = []
    for name in ("calibration1", "calibration2"):
        output = tmp_path / name
        result = subprocess.run(
            [
                str(BACKEND_DIR / ".venv" / "bin" / "python"),
                str(FSRS_SEGMENT_SCRIPT),
                "--db", str(snapshot),
                "--cutoff", "2026-07-25T17:00:00Z",
                "--window-start", "2026-07-01T00:00:00Z",
                "--current-baseline-summary", str(baseline_dir / "summary.json"),
                "--output-dir", str(output),
            ],
            cwd=BACKEND_DIR,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        outputs.append({path.name: path.read_bytes() for path in output.iterdir()})

    assert outputs[0] == outputs[1]
    assert _sha256(snapshot) == before
    calibration = json.loads(outputs[0]["fsrs_calibration_segments.json"])
    assert calibration["overall"]["reviews"] == 1
    assert calibration["overall"]["strict_successes_rating_ge_3"] == 1
    assert (
        calibration["overall"]["fsrs_recall_successes_applied_rating_ge_2"]
        == 1
    )
    assert calibration["overall"]["raw_product_rating_ge_2"] == 1
    assert calibration["limitations"][-2] == (
        "credit type is diagnostic; primary and collateral outcomes are equally valid"
    )


def test_segmented_fsrs_calibration_uses_stamped_applied_rating(tmp_path):
    """Policy-v2 rating 2 is an FSRS failure even though the product stores 2."""
    snapshot = tmp_path / "snapshot.db"
    logs = tmp_path / "logs"
    _make_snapshot(snapshot)
    _make_logs(logs)
    connection = sqlite3.connect(snapshot)
    payload = json.loads(
        connection.execute(
            "SELECT fsrs_log_json FROM review_log WHERE id = 2"
        ).fetchone()[0]
    )
    payload.update({
        "fsrs_scheduler_policy_version": 2,
        "fsrs_rating_applied": 1,
        "fsrs_policy": "assisted_lapse_v1",
    })
    connection.execute(
        "UPDATE review_log SET rating = 2, fsrs_log_json = ? WHERE id = 2",
        (json.dumps(payload),),
    )
    connection.commit()
    connection.close()

    baseline_dir = tmp_path / "baseline"
    baseline = _run(snapshot, logs, baseline_dir)
    assert baseline.returncode == 0, baseline.stderr
    output = tmp_path / "calibration"
    result = subprocess.run(
        [
            str(BACKEND_DIR / ".venv" / "bin" / "python"),
            str(FSRS_SEGMENT_SCRIPT),
            "--db", str(snapshot),
            "--cutoff", "2026-07-25T17:00:00Z",
            "--window-start", "2026-07-01T00:00:00Z",
            "--current-baseline-summary", str(baseline_dir / "summary.json"),
            "--output-dir", str(output),
        ],
        cwd=BACKEND_DIR,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr

    calibration = json.loads(
        (output / "fsrs_calibration_segments.json").read_bytes()
    )
    assert calibration["schema_version"] == 2
    assert calibration["overall"]["reviews"] == 1
    assert calibration["overall"]["strict_successes_rating_ge_3"] == 0
    assert (
        calibration["overall"]["fsrs_recall_successes_applied_rating_ge_2"]
        == 0
    )
    assert calibration["overall"]["raw_product_rating_ge_2"] == 1
    policy_segments = {
        row["group"]: row
        for row in calibration["segments"]
        if row["dimension"] == "scheduler_policy_version"
    }
    assert policy_segments["2"]["reviews"] == 1
