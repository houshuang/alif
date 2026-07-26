# Acquisition evidence decision replay

## Decision

Do not tighten the `perfect_accuracy` or `high_accuracy` graduation routes yet.

The bounded replay finds a plausible signal: requiring a prior success on an
earlier UTC day would defer 9 of 23 `perfect_accuracy` graduations and none of
17 `high_accuracy` graduations. Among the tiny delivered 3-day follow-up
sample, the deferred group recalled 2/4 while qualifying perfect/high events
recalled 13/18.

That is not sufficient for a behavioral change. This replay tests whether an
event met a proposed evidence gate at the historical decision point; it cannot
simulate when the selector would next deliver a deferred word or how nine
extra acquisition residents would affect the congested queue.

## Tool and provenance

`backend/scripts/replay_acquisition_evidence.py`:

- opens the pinned SQLite snapshot with `mode=ro&immutable=1`;
- verifies database SHA and cutoff against the WP0/WP1 baseline;
- reconstructs acquisition evidence episodes from logged pre-counters;
- treats primary and collateral reviews identically;
- segments rather than repairs counter discontinuities;
- applies candidate evidence gates only to `perfect_accuracy` and
  `high_accuracy`;
- writes deterministic JSON and CSV.

Inputs:

- database SHA-256:
  `3b8ba1d566185ebe908139e24851f4f02214dbd6d6334a3638b6da5f30ef0069`;
- cutoff: `2026-07-25T17:30:44.409771Z`;
- decision window: `2026-07-08T00:00:00Z` through cutoff;
- baseline:
  `baselines/learning-system-2026-07-25-v2-foundation/summary.json`.

Outputs:

- `baselines/acquisition-evidence-replay-2026-07-08-to-25/`.

Two fresh runs were byte-identical. The database remained byte-identical.
The automated fixture proves that a success 20 minutes earlier in another
session passes the 10-minute/session candidate but fails the 12-hour and
prior-day candidates.

## Counter and episode reconciliation

The snapshot contains 12,899 acquisition events with logged pre-counters.
Across 10,331 adjacent transitions not separated by a logged graduation, the
replay found 90 counter discontinuities:

| Classification | All history | In decision window |
|---|---:|---:|
| intervening non-acquisition episode | 70 | 11 |
| pre-v2 rapid-retest counter semantics | 1 | 1 |
| unexplained or legacy state change | 19 | 0 |

The 11 recent intervening-episode cases are not assumed to be corruption.
They are words that had non-acquisition/FSRS reviews between acquisition
episodes, such as a later leech reintroduction with lifetime counters
preserved. The replay starts a new evidence episode at that boundary.

The remaining recent discontinuity is lemma 3810:

1. rating-1 acquisition failure at 13:18;
2. guarded quiz success at 13:23 under protocol v1;
3. reading success at 13:37 whose pre-counters show the quiz had incremented
   both lifetime counters.

That is exactly the pre-v2 behavior corrected by PR #223. The replay detects
and segments it as a version boundary instead of silently applying v2
counter-neutral semantics to v1 history.

The 19 unexplained historical discontinuities are outside the decision window
and concentrated in legacy routing/state behavior. They are retained in the
artifact as a warning against whole-history counter replay without explicit
version and canonical-identity handling.

## Candidate policies

All candidates preserve:

- first-correct graduation;
- elapsed-interval graduation;
- standard Box-3 graduation;
- every failure;
- equal primary/collateral evidence.

They modify only perfect/high-accuracy graduation:

| Policy | Extra evidence required |
|---|---|
| logged current | no additional gate |
| 10m + other session | prior successful acquisition review ≥10 minutes earlier, with both reviews carrying different non-null session IDs |
| 12h | prior successful acquisition review ≥12 hours earlier |
| prior UTC day | prior successful acquisition review on an earlier UTC date |

## Results at the historical decision point

| Policy | Total qualifying | Deferred | Perfect deferred | High deferred | Qualifying perfect/high 3d recall | Deferred 3d recall |
|---|---:|---:|---:|---:|---:|---:|
| logged current | 187 | 0 | 0 | 0 | 15/22 (68.2%) | — |
| 10m + other session | 180 | 7 | 7 | 0 | 13/19 (68.4%) | 2/3 (66.7%) |
| 12h | 178 | 9 | 9 | 0 | 13/18 (72.2%) | 2/4 (50.0%) |
| prior UTC day | 178 | 9 | 9 | 0 | 13/18 (72.2%) | 2/4 (50.0%) |

The 12-hour and prior-day rules identify the same nine decisions in this
window. They would reduce total graduation flow by 4.8% (9/187) at those
historical decision points and perfect-accuracy flow by 39.1% (9/23).

The observed recall split is directionally compatible with a spacing gate,
but four delivered reviews in the deferred cell cannot establish benefit.
Nor can the replay count the additional cards required: historical FSRS
delivery after graduation is not valid counterfactual acquisition delivery.

## Tradeoffs

Potential benefit:

- prevents three rapid successes from being treated as consolidated evidence;
- targets only the route with the weakest observed 1/3-day recall;
- leaves high-accuracy, standard, first-correct, and long-gap decisions
  untouched in this window.

Potential cost:

- adds at least nine acquisition residents in this 17-day window before
  replacement flow;
- may worsen Box-1/Box-2 congestion and first-review latency;
- could withhold FSRS from genuinely easy words;
- depends on session IDs or wall-clock definitions that have changed over
  historical versions.

## Required validation before behavior changes

1. Prospectively stamp an explicit acquisition/graduation policy version and
   log due state, elapsed interval, and post-counter evidence on every
   acquisition review — implemented in the workspace as additive v2 telemetry,
   not deployed.
2. Use selector diagnostics to measure the actual cards and distinct due words
   displaced by retained acquisition residents.
3. Run a full state replay from a clean version boundary that advances boxes,
   due dates, counters, and candidate graduation decisions.
4. Replay at least one historical-material checkpoint and current material.
5. Reject any candidate that improves observed-retention selection by simply
   starving difficult deferred words of follow-up.
6. Only then consider shadow/prospective randomization with retained sentence
   recall, learner minutes, completion, and pipeline debt as co-primary
   outcomes/guardrails.

## Rollback

The replay is read-only and has no production rollback.

Any future gate must be behind one policy switch and stamped per review.
Rollback must stop future use without deleting legitimate review history or
rewriting lifetime counters.
