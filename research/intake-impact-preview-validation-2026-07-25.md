# Intake-impact preview validation — 2026-07-25

## Decision

WP8 now has a safe read-only first layer: it can classify a proposed lemma
cohort against a pinned learner snapshot, expose the immediate Box-1 and
maximum eventual FSRS additions, and express staged-admission scenarios in
recent acquisition-capacity units.

It is suitable for **intake risk disclosure**, but it is not yet a calibrated
forecast of graduation time, retention, or goal displacement. The July 15
backtest is useful precisely because it demonstrates that distinction.

No production data, intake state, selector behavior, counters, or scheduler
state was changed.

## Implementation

- Script: `backend/scripts/preview_intake_impact.py`
- Test coverage: `backend/tests/test_analyze_learning_system.py`
- Pinned backtest artifacts:
  `research/baselines/intake-preview-july15-backtest/`
- Required current-state input:
  `research/baselines/learning-system-2026-07-25-v2-foundation/summary.json`

The script has no apply mode. SQLite is opened with
`mode=ro&immutable=1`; the database, candidate file, and baseline-summary
identities are checked around the run. It refuses a baseline whose database
hash or cutoff does not match the requested snapshot.

For resolved candidates it distinguishes:

- already learned;
- already in training;
- existing pending;
- suspended and requiring an explicit decision;
- existing but untracked;
- inert/not eligible;
- duplicate candidates that collapse to the same canonical lemma.

Unresolved candidates are counted conservatively as possible new Box-1
additions, but must still pass the normal quality-gated lemma-creation path
before any real intake.

## Reproducible historical check

The backtest asks what the lightweight preview would have shown immediately
before the July 15 bookifier cohort:

```text
snapshot cutoff:       2026-07-25T17:30:44.409771Z
snapshot SHA-256:      3b8ba1d566185ebe908139e24851f4f02214dbd6d6334a3638b6da5f30ef0069
capacity cutoff:       2026-07-15T00:00:00Z
capacity history:      previous 30 calendar days
anonymous candidates: 202
```

The generated preview reports:

| Quantity | Result |
|---|---:|
| Projected immediate Box-1 additions | 202 |
| Maximum eventual FSRS arrivals | 202 |
| Recent acquisition accuracy | 76.7% |
| Mean acquisition word reviews/calendar day | 22.07 |
| Successful graduation episodes used for review reference | 119 |
| Median reviews in those successful episodes | 4 |
| Success-conditioned review reference | 808 |
| Success-conditioned capacity-days reference | 36.6 |
| Admission duration at 8/day | 26 days |
| Admission duration at 30/day | 7 days |

The cohort's observed state at the snapshot cutoff, approximately 10.7 days
after intake, was independently reconciled with direct SQL:

| Current state | Lemmas |
|---|---:|
| acquiring | 96 |
| known | 61 |
| lapsed | 1 |
| learning | 20 |
| suspended | 24 |
| **total** | **202** |

Eighty-eight cohort lemmas had a non-null `graduated_at`. Cohort lemmas had
already accumulated 610 acquisition word-review rows across 198 reviewed
lemmas, with 62.5% rating-3/4 outcomes.

## What the backtest validates

It validates:

- exact cohort-size and staged-admission arithmetic;
- conservative Box-1 and maximum-FSRS counts;
- current recovery-gate context from the matching WP1 snapshot;
- deterministic artifacts from identical inputs;
- immutable database use and rejection of mismatched baselines.

It does **not** validate the 808-review or 36.6-day figures as predictions.
Those are success-conditioned reference units: the median of completed
graduations excludes suspended and not-yet-finished episodes. The actual
cohort had already used 610 acquisition word reviews while only 88/202 had
ever graduated. Treating four reviews per candidate as an unconditional
workload forecast would therefore be materially optimistic.

The artifact and UI wording deliberately call these values
“success-conditioned references,” not estimates.

## Open validation gaps

The following parts of the full WP8 proposal still require selector replay:

- reviewable sentence coverage for genuinely new, not-yet-created lemmas;
- time/sessions to first exposure;
- time/sessions to acquisition graduation;
- maintenance-lane displacement;
- active-goal displacement;
- retention differences between immediate and staged admission;
- homograph resolution for candidates supplied without existing lemma IDs.

Aggregate calendar-day capacity cannot answer these honestly. The next
bounded selector replay should model candidate admission, sentence coverage,
the user's actual session sequence, all-word credit, and the three recovery
gates. Until then, these values should remain unavailable rather than be
filled with fabricated precision.

## Verification record

- Focused tests: `3 passed in 0.49s`
- The tests cover deterministic double-runs, primary/collateral equal-validity
  semantics in the shared baseline, immutable snapshot hashing, sidecar
  rejection, candidate classification, and mismatched-baseline rejection.
- Two independent runs against the pinned production snapshot were
  byte-identical; the database SHA-256 remained
  `3b8ba1d566185ebe908139e24851f4f02214dbd6d6334a3638b6da5f30ef0069`.
- An earlier broader backend run was stopped after 5m20s because an existing
  test entered a live HTTPS crawl; at interruption it had `511 passed,
  9 deselected`, with no failures. This is partial evidence, not a claimed
  full-suite pass.

## Next decision

Proceed to the bounded S1 replay foundation, and use it to complete WP8's
predictive fields before considering S1 rollout or S2 risk ordering. Keep
protocol-v2 retry behavior frozen during this work.
