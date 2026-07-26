# Learning-system baseline

- Window: `2026-03-27T00:00:00Z` to `2026-07-25T17:30:44.409771Z`
- Session window: `2026-07-05T00:00:00Z` to `2026-07-25T17:30:44.409771Z`
- Git commit: `0dff93402b2449795f60eaa3f1f0625e906256c9`
- Database SHA-256: `3b8ba1d566185ebe908139e24851f4f02214dbd6d6334a3638b6da5f30ef0069`
- FSRS library: `6.3.0`

## Review activity

| Metric | Value |
|---|---:|
| Valid word reviews | 40,845 |
| Distinct canonical words | 2,873 |
| Sentence-word reviews | 40,724 |
| Sentence reading reviews | 40,724 |
| Primary sentence rows | 7,731 |
| Collateral sentence rows | 32,993 |
| Graduations | 1,329 |

Primary/collateral is a diagnostic split only; both are equally valid word outcomes.

## Current recovery pressure

| Gate | Current | Trigger | Tripped |
|---|---:|---:|:---:|
| box1_actionable | 127 | 5 | yes |
| box2_due | 39 | 30 | yes |
| strict_main_fsrs_due | 805 | 750 | yes |

Recovery active: **yes**.

## Matched FSRS calibration

| Stability | Reviews | Predicted | Observed | Brier | Median late |
|---|---:|---:|---:|---:|---:|
| <7d | 5,224 | 88.9% | 78.0% | 0.1709 | 1.4 d |
| 7-30d | 2,284 | 91.3% | 78.8% | 0.1771 | 2.8 d |
| >=30d | 809 | 92.9% | 77.0% | 0.2011 | 4.3 d |

Predictions use the installed FSRS library at each actual review time; historical scheduler-version mixing remains a caveat.

## Session behavior

| Metric | Value |
|---|---:|
| Analyzable sessions | 87 |
| Approximately complete | 28 |
| Completion rate | 32.2% |
| Complete-session median distinct rating-1 | 16.0 |
| Auto-wrap sizes | 8, 10 |
| Protocol-v2 first telemetry | not present |

Completion is an interaction-log approximation; see `research/learning-metrics-spec.md`.

## Warnings and limitations

- No input-integrity warnings.
- Current-library calibration does not authorize FSRS retuning.
- This report does not reconstruct historical daily state or causal retry effects.
