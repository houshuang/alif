# Learning-system baseline

- Window: `2026-09-06T00:00:00Z` to `2026-09-15T07:22:55Z`
- Session window: `2026-09-06T00:00:00Z` to `2026-09-15T07:22:55Z`
- Git commit: `2d859666042aa02054f1ff6434ac7cf68ba949db`
- Database SHA-256: `4f63984a41554ec81c895f38fe4b1f2b0083f4e2de9410af266f865ca1309f05`
- FSRS library: `6.3.1`

## Review activity

| Metric | Value |
|---|---:|
| Valid word reviews | 963 |
| Distinct canonical words | 645 |
| Sentence-word reviews | 856 |
| Sentence reading reviews | 856 |
| Primary sentence rows | 254 |
| Collateral sentence rows | 602 |
| Graduations | 14 |

Primary/collateral is a diagnostic split only; both are equally valid word outcomes.

## Current recovery pressure

| Gate | Current | Trigger | Tripped |
|---|---:|---:|:---:|
| box1_actionable | 19 | 5 | yes |
| box2_due | 13 | 30 | no |
| strict_main_fsrs_due | 718 | 750 | no |

Recovery active: **yes**.

## Matched FSRS calibration

| Stability | Reviews | Predicted | Observed | Brier | Median late |
|---|---:|---:|---:|---:|---:|
| <7d | 294 | 79.7% | 71.8% | 0.1752 | 7.0 d |
| 7-30d | 193 | 89.7% | 82.4% | 0.1417 | 6.2 d |
| >=30d | 116 | 93.2% | 80.2% | 0.1733 | 7.6 d |

Predictions use the installed FSRS library at each actual review time; historical scheduler-version mixing remains a caveat.

## Session behavior

| Metric | Value |
|---|---:|
| Analyzable sessions | 33 |
| Approximately complete | 19 |
| Completion rate | 57.6% |
| Complete-session median distinct rating-1 | 6 |
| Auto-wrap sizes | 1, 1, 2, 3, 3, 4, 4, 5, 5, 5, 5, 5, 6, 7, 8, 9, 11, 13 |
| Protocol-v2 first telemetry | 2026-09-07T18:06:45.877810Z |

Completion is an interaction-log approximation; see `research/learning-metrics-spec.md`.

## Warnings and limitations

- 1 active FSRS rows had missing/invalid card JSON
- Current-library calibration does not authorize FSRS retuning.
- This report does not reconstruct historical daily state or causal retry effects.
