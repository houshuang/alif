# Learning-system baseline

- Window: `2026-02-01T00:00:00Z` to `2026-05-12T14:55:50.375703Z`
- Session window: `2026-02-01T00:00:00Z` to `2026-05-12T14:55:50.375703Z`
- Git commit: `0dff93402b2449795f60eaa3f1f0625e906256c9`
- Database SHA-256: `c8d77fe678a50197aa703ecb4a303de3a7a24dbfce146be0040514151a8ac7d6`
- FSRS library: `6.3.0`

## Review activity

| Metric | Value |
|---|---:|
| Valid word reviews | 38,912 |
| Distinct canonical words | 1,968 |
| Sentence-word reviews | 36,035 |
| Sentence reading reviews | 35,986 |
| Primary sentence rows | 7,384 |
| Collateral sentence rows | 28,651 |
| Graduations | 1,813 |

Primary/collateral is a diagnostic split only; both are equally valid word outcomes.

## Current recovery pressure

| Gate | Current | Trigger | Tripped |
|---|---:|---:|:---:|
| box1_actionable | 11 | 5 | yes |
| box2_due | 21 | 30 | no |
| strict_main_fsrs_due | 158 | 750 | no |

Recovery active: **yes**.

## Matched FSRS calibration

| Stability | Reviews | Predicted | Observed | Brier | Median late |
|---|---:|---:|---:|---:|---:|
| <7d | 3,712 | 90.1% | 83.5% | 0.1338 | 0.9 d |
| 7-30d | 863 | 90.6% | 80.2% | 0.1635 | 2.0 d |
| >=30d | 104 | 91.3% | 75.0% | 0.2120 | 2.1 d |

Predictions use the installed FSRS library at each actual review time; historical scheduler-version mixing remains a caveat.

## Session behavior

| Metric | Value |
|---|---:|
| Analyzable sessions | 0 |
| Approximately complete | 0 |
| Completion rate | — |
| Complete-session median distinct rating-1 | — |
| Auto-wrap sizes | — |
| Protocol-v2 first telemetry | not present |

Completion is an interaction-log approximation; see `research/learning-metrics-spec.md`.

## Warnings and limitations

- No interaction-log directory supplied; session metrics are empty
- Current-library calibration does not authorize FSRS retuning.
- This report does not reconstruct historical daily state or causal retry effects.
