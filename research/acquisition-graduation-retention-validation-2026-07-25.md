# Acquisition graduation evidence and observed retention

## Decision

Do **not** change acquisition thresholds from this readout.

Keep the narrow correctness fix that requires the graduating review to be a
success (`rating >= 3`). The observed reason-level retention differences are a
useful warning—especially for `perfect_accuracy`—but are not causal or
decision-grade. Any broader graduation change remains behind versioned replay
and prospective measurement.

## Reproducible audit

`backend/scripts/analyze_graduation_retention.py` reads the pinned production
snapshot with SQLite `mode=ro&immutable=1`, verifies the database identity and
cutoff against the WP0/WP1 baseline, and writes stable JSON/CSV.

Pinned inputs:

- database: `/tmp/alif-wp01-019f9a48/alif.db`;
- database SHA-256:
  `3b8ba1d566185ebe908139e24851f4f02214dbd6d6334a3638b6da5f30ef0069`;
- window: `2026-07-08T00:00:00Z` through
  `2026-07-25T17:30:44.409771Z`;
- baseline:
  `baselines/learning-system-2026-07-25-v2-foundation/summary.json`;
- output:
  `baselines/graduation-retention-2026-07-08-to-25/`.

Two fresh runs were byte-identical. The database hash and modification time
were unchanged and no SQLite sidecars were created. A regression test also
exercises an empty graduation window so a zero-event audit cannot fail or
invent rows.

## Definitions

The audit starts from stored acquisition review rows whose `fsrs_log_json`
records `graduated = true`.

For each event it derives:

- acquisition review rows from the current recorded acquisition episode start
  through graduation;
- distinct non-null session IDs contributing those rows;
- episode elapsed time;
- the first delivered reading/sentence review at or after 1, 3, and 7 days
  following graduation.

Recall is `rating >= 3` among delivered follow-ups. Primary and collateral word
reviews are counted identically. `credit_type` remains descriptive metadata
only.

The denominator is deliberately reported in two stages:

1. events old enough to be eligible at the cutoff;
2. eligible events that actually received a qualifying follow-up.

This prevents non-delivery from being silently classified as a memory failure,
but it also means recall-among-delivered is not an unconditional retention
estimate.

## Results

There were 187 graduations in the audit window.

| Reason | N | Median evidence rows | Median sessions | One-session | Median episode | Recall ≥1d | Recall ≥3d | Recall ≥7d |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| elapsed interval | 104 | 3 | 3 | 19 | 427.01 h | 38/49 (77.6%) | 33/42 (78.6%) | 13/20 (65.0%) |
| first correct | 31 | 1 | 1 | 31 | 11.87 h | 18/22 (81.8%) | 13/18 (72.2%) | 6/8 (75.0%) |
| high accuracy | 17 | 5 | 3 | 0 | 173.05 h | 10/12 (83.3%) | 8/10 (80.0%) | 4/5 (80.0%) |
| perfect accuracy | 23 | 3 | 2 | 7 | 144.15 h | 10/15 (66.7%) | 7/12 (58.3%) | 3/3 (100%) |
| standard | 12 | 6 | 4 | 0 | 685.57 h | 6/6 (100%) | 5/5 (100%) | 2/2 (100%) |

The 7-day cells are particularly sparse. The perfect-accuracy 7-day result is
three delivered reviews and must not outweigh its weaker 1- and 3-day cells.

Follow-up delivery was incomplete. Depending on reason and horizon, only
33.3%–75.0% of eligible events received a qualifying sentence review. The
selector therefore affects which graduations enter the observed-recall
denominator.

## What the audit supports

### Supported now: success-gate correctness fix

Across all 2,106 stored acquisition graduation events, two graduated on rating
2. One is the recent `standard` event included above. A confused/Hard answer is
valid negative evidence, but it cannot be the successful retrieval that
asserts graduation. The separate success-gate validation records the code
change and exact regression fixture.

### Signal only: perfect-accuracy route

`perfect_accuracy` has the weakest 1- and 3-day observed recall in this window,
and 7/23 events used only one recorded session. This is consistent with the
proposal's concern that cumulative success can include evidence that is too
closely spaced.

It does **not** establish that the route causes worse retention:

- reason groups differ in word difficulty and prior knowledge;
- follow-up delivery is selected by the current scheduler;
- groups are small and heavily censored;
- current `acquisition_started_at` may blur older re-acquisition episodes;
- reviews after graduation use a different scheduling state;
- the audit does not yet segment all historical algorithm versions.

Changing the rule now could retain more words in an already congested
acquisition queue without proving a retention benefit.

## Next validation

Before altering perfect/high-accuracy graduation:

1. Build version-segmented, event-sourced acquisition episodes from
   `ReviewLog`, with explicit same-session and spaced-success features.
2. Reproduce current graduation decisions from those derived events and
   reconcile every mismatch.
3. Replay candidates requiring at least one spacing-eligible success while
   preserving first-correct and long-interval routes as separate arms.
4. Measure the counterfactual extra reviews, Box-1 age, acquiring stock, and
   downstream FSRS arrivals—not retention alone.
5. Prospectively log a graduation-policy version and the evidence vector used
   by the decision.
6. Use retained sentence recall at ≥1 and ≥3 days as the primary outcome, with
   delivery probability and session completion as guardrails.

No retrospective state rewrite, deployment, or production-data mutation is
justified by this audit.

## Rollback

The audit itself is read-only. Delete its generated output directory to remove
the artifact.

The success gate is one condition in `submit_acquisition_review`; reverting it
requires no schema or data migration. Historical legitimate reviews remain
valid under either implementation.
