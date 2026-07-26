# WP0/WP1 validation record — learning-system baseline

Date: 2026-07-25  
Behavioral changes: none  
Production learning-data writes: none

## Outcome

The lightweight WP0/WP1 foundation is complete:

- frozen definitions: `research/learning-metrics-spec.md`;
- deterministic command: `backend/scripts/analyze_learning_system.py`;
- strict read-only and determinism tests:
  `backend/tests/test_analyze_learning_system.py`;
- pinned output:
  `research/baselines/learning-system-2026-07-25-v2-foundation/`.

This is an observational baseline, not a scheduler replay and not authorization for
FSRS, acquisition, selector, intake, or retry behavior changes.

## Pinned inputs

- Application base commit:
  `0dff93402b2449795f60eaa3f1f0625e906256c9` (PR #223 merged).
- Transaction-consistent production SQLite backup captured:
  `2026-07-25T17:30:44.409771Z`.
- Database SHA-256:
  `3b8ba1d566185ebe908139e24851f4f02214dbd6d6334a3638b6da5f30ef0069`.
- Historical outcome window:
  `[2026-03-27T00:00:00Z, 2026-07-25T17:30:44.409771Z)`.
- Session-log window:
  `[2026-07-05T00:00:00Z, 2026-07-25T17:30:44.409771Z)`.
- FSRS library: `6.3.0`.
- Each selected interaction log's size and SHA-256 are in `manifest.json`.

The backup used a read transaction plus SQLite's online backup API. The temporary
server copy was deleted after local verification. The production database and services
were not modified or restarted.

## Independent reconciliation

Direct SQLite queries, separate from the analysis aggregation, returned:

| Check | Direct SQL | Analysis |
|---|---:|---:|
| Raw review rows in window | 41,025 | 41,025 before validity exclusions |
| Function-word rows excluded | 106 | 106 |
| Proper-name/onomatopoeia rows excluded | 74 | 74 |
| Valid word reviews | 40,845 | 40,845 |
| Raw sentence rows | 40,902 | 40,902 before validity exclusions |
| Valid primary sentence rows | — | 7,731 |
| Valid collateral sentence rows | — | 32,993 |
| Valid sentence rows | 40,724 after exclusions | 40,724 |
| Acquisition Box 1 | 138 total / 132 due | 138 / 132 |
| Acquisition Box 2 | 53 total / 39 due | 53 / 39 |
| Acquisition Box 3 | 8 total / 5 due | 8 / 5 |
| Graduations in window | 1,329 | 1,329 |

No duplicate non-null `client_review_id` values were found.

The production recovery snapshot at the cutoff was:

- actionable Box 1: 127, trigger 5;
- due Box 2: 39, trigger 30;
- strict main-lane FSRS: 805, trigger 750.

All three gates were independently active. The strict main-lane value is one above an
earlier live API observation because this is a later pinned snapshot.

## Reproducibility and read-only validation

Two complete production runs with identical inputs emitted byte-identical directories.
The generated `SHA256SUMS` covers every generated artifact other than the checksum file
itself.

The first validation run exposed a SQLite edge case: opening a copied database in
ordinary `mode=ro` can create empty `-wal/-shm` sidecars when its persisted journal mode
is WAL. The command was hardened to `mode=ro&immutable=1`; strict mode now:

1. rejects pre-existing database WAL/SHM/journal sidecars;
2. hashes the database and selected logs before analysis;
3. re-hashes them after analysis and fails on any change;
4. creates no SQLite sidecars.

Tests cover:

- byte-identical outputs across two runs;
- unchanged database bytes and modification time;
- absence of SQLite sidecars;
- rejection of a snapshot with a sidecar;
- equal validity of primary and collateral sentence rows;
- separation of quiz from sentence outcomes;
- canonical distinct-word counts;
- recovery-stock calculations;
- matched FSRS calibration;
- `no_idea` collateral fan-out in session rating-1 counts;
- auto-wrap identification and protocol-v2 telemetry.

Test results:

- `tests/test_analyze_learning_system.py`: 2 passed.
- Full backend suite attempt: 511 passed, 9 deselected, no failures before manual
  interruption at 5m20s. It was stopped because an existing later test opened a live
  HTTPS connection and advanced extremely slowly. This is recorded as a partial suite,
  not a full-suite pass.

## Baseline findings

### Review activity

- 40,845 valid word reviews across 2,873 canonical lemmas.
- 40,724 were sentence-word reviews:
  - 7,731 primary;
  - 32,993 collateral.
- Diagnostic accuracy:
  - primary: 79.3%;
  - collateral: 90.0%.

The accuracy difference describes selection difficulty, not unequal credit validity.

### Acquisition

- 199 acquiring words:
  - Box 1: 138;
  - Box 2: 53;
  - Box 3: 8.
- Box-1 median age: 10.46 days; p90: 40.51; maximum: 59.83.
- 19 Box-1 words had zero correct reviews.

### Matched FSRS calibration

| Stability | Reviews | Predicted | Observed | Median late |
|---|---:|---:|---:|---:|
| <7d | 5,224 | 88.9% | 78.0% | 1.4 d |
| 7–30d | 2,284 | 91.3% | 78.8% | 2.8 d |
| ≥30d | 809 | 92.9% | 77.0% | 4.3 d |

Predictions are evaluated at the actual review timestamp, so lateness is included.
Historical scheduler-version mixing remains unresolved; no retuning follows from this
baseline alone.

### Session behavior

- 87 analyzable sessions;
- 28 approximately complete (32.2%);
- completed-session median: 16 distinct rating-1 lemmas;
- completed-session range: 7–37;
- observed automatic wrap-up sizes: 8 and 10.

No protocol-v2 learner telemetry occurred before this snapshot cutoff. PR #223's code
boundary is pinned, and future retry readout must start with telemetry carrying `v: 2`.

## Open risks for the next validating agent

1. Reproduce strict main-lane FSRS count through the production service function
   against the same pinned database, not only independent SQL.
2. Decide whether the 34 function/inert active rows whose JSON value is `null` warrant
   a separate integrity report. They are excluded correctly here and do not affect
   actionable FSRS debt.
3. Segment FSRS calibration by application/scheduler version before drawing parameter
   conclusions.
4. Confirm whether passage and offline-sync metadata loss biases the session and
   modality slices.
5. Build daily stock snapshots before claiming stock-flow reconciliation; current
   historical ULK state cannot reconstruct past stocks.
6. Keep four Box-1 appearances as A0 until actual delivery displacement and retained
   graduation are replayed.
7. Treat protocol-v2's first observed learner telemetry—not the contaminated v1
   window—as the retry analysis start.

## Next authorized package

The next package in the agreed sequence is WP8: a read-only intake-impact preview. It
should consume these pinned definitions and forecast staged versus immediate intake
without applying either option.
