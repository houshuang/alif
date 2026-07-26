# July 15 intake cohort — observed trajectory validation

## Outcome

WP8 now has observed first-exposure and graduation trajectories for the
202-word July 15 bookifier intake. These are cohort facts, not staged-intake
counterfactuals.

The principal finding is that immediate admission created a long first-teach
queue:

- median time from acquisition start to first stored review: **51.67 hours**;
- p90 time to first review: **155.17 hours** (6.47 days);
- median first-review session ordinal after intake: **12.5**;
- p90 first-review session ordinal: **27**;
- four words still had no stored review after 10.46 days.

This makes “imported immediately” and “learned immediately” explicitly
different quantities.

## Pinned inputs

```text
snapshot cutoff:  2026-07-25T17:30:44.409771Z
database SHA-256: 3b8ba1d566185ebe908139e24851f4f02214dbd6d6334a3638b6da5f30ef0069
source:           bookifier
start window:     2026-07-15T00:00:00Z
end window:       2026-07-16T00:00:00Z
cohort size:      202
```

Implementation:
`backend/scripts/analyze_intake_cohort.py`.

Artifacts:
`research/baselines/intake-cohort-bookifier-2026-07-15/`.

The script opens SQLite with `mode=ro&immutable=1`, requires the matching WP1
baseline hash/cutoff, verifies the database identity after analysis, and has no
write/apply path.

## Fixed-horizon progress

Each horizon is measured relative to each lemma's own acquisition start:

| Horizon | First reviewed | Cohort fraction | Graduated | Cohort fraction |
|---:|---:|---:|---:|---:|
| 1 day | 74 | 36.6% | 16 | 7.9% |
| 3 days | 120 | 59.4% | 26 | 12.9% |
| 5 days | 155 | 76.7% | 35 | 17.3% |
| 7 days | 185 | 91.6% | 58 | 28.7% |
| 10 days | 194 | 96.0% | 70 | 34.7% |

At the 10.46-day snapshot:

- 198/202 had at least one review;
- 88/202 had ever graduated;
- 24 were currently suspended;
- 96 remained acquiring;
- 61 were known, 20 learning, and one lapsed.

Graduation timing among the 88 successes had median 5.63 days and p90 10.24
days. These quantiles are explicitly **success-conditioned** and must not be
used as unconditional time-to-graduation forecasts.

## Review workload and equal credit

The cohort accumulated:

- 780 stored word-review rows;
- 610 acquisition review rows;
- 62.46% acquisition rating-3/4 accuracy;
- 166 primary rows;
- **614 collateral rows**;
- 776 reading rows and four quiz rows.

Collateral rows are 78.7% of observed evidence. Excluding or discounting them
would destroy most of the cohort signal and violate the foundational equal
word-credit rule.

## Implications for intake preview

The earlier aggregate WP8 preview estimated 808 reviews from four reviews ×
202 candidates. The observed cohort had already consumed 610 acquisition
reviews while only 88 had graduated and 96 still remained acquiring. The new
trajectory adds the missing queueing evidence:

- bulk admission delays first teaching for days;
- completed-graduate medians understate censored/suspended work;
- calendar acquisition capacity is shared with recovery and maintenance;
- session order, not only review count, determines when a candidate receives
  its first actual retrieval.

A future staged-intake comparison should therefore optimize:

1. time to first review;
2. fraction first-reviewed within one/three days;
3. acquisition reviews per retained graduate;
4. Box-1 waiting stock;
5. maintenance/recovery displacement;
6. completion and abandonment;
7. first cold recall after graduation.

Staging cannot yet be declared superior from this single immediate cohort.
Words admitted later might simply wait outside Box 1 rather than learn sooner.
The causal question is whether controlled admission improves time from
**admission to first high-quality exposure and retained graduation** without
reducing valuable goal progress.

## Verification

- Two full runs were byte-identical.
- Database SHA-256 remained unchanged.
- `123 passed, 1 deselected in 4.26s` across selector, cohort, replay,
  baseline, and API-focused tests.
- The cohort test explicitly verifies that primary, collateral, and
  non-sentence review rows are retained as separate diagnostics rather than
  silently applying a primary-only validity filter.

## Next validation

Add this trajectory output to the WP8 preview as an empirical reference cohort,
not a point forecast. A staged scenario must be replayed with an admission
queue and session-capacity model, then validated on a future independently
versioned intake. Do not infer retention benefit from queue reduction alone.
