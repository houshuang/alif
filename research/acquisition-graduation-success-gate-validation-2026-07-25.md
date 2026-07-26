# Acquisition graduation must end on success

## Decision

Add a success gate to tiered acquisition graduation:

```text
rating_int >= 3
```

This is a correctness fix, not a threshold experiment. A rating-2
Hard/confused answer may preserve enough cumulative accuracy to satisfy a
graduation tier, but it does not demonstrate successful retrieval on the
graduating review.

The change was merged and deployed in PR #224 on 2026-07-26. It does not
modify historical learner data.

## Before

Tier E already required rating ≥3. Tier 0 first-correct inherently required
rating ≥3. But the shared Tier 1/2/3 block ran after any rating:

- Tier 1: 100% cumulative accuracy, 3+ reviews;
- Tier 2: ≥80% cumulative accuracy, 4+ reviews, Box ≥2;
- Tier 3: ≥60% cumulative accuracy, 5+ reviews, Box ≥3, due, two calendar days.

Rating 1 resets to Box 1 and usually prevents the box-gated tiers. Rating 2
leaves the word in its box. Consequently a due Box-3 word with 3/4 prior
successes could receive rating 2, become 3/5 = 60%, and graduate through Tier
3 on the confused response.

## Verified production evidence

Across 2,106 stored acquisition graduation events:

| Graduating rating | Events |
|---|---:|
| rating ≥3 | 2,104 |
| rating 2 | **2** |
| rating 1 | 0 |

The two rating-2 graduations:

| Lemma | Time | Credit | Reason | Pre-state |
|---:|---|---|---|---|
| 2204 | 2026-02-26 22:44 UTC | primary | legacy/null | Box 3, 4/4 |
| 4254 | 2026-07-25 16:48 UTC | collateral | standard | Box 3, 3/4 |

Lemma 2204 later had 16 FSRS reviews with 15 successes. That downstream result
does not make the graduation evidence valid; it only shows that this particular
false-positive pathway did not harm that word. Lemma 4254 is in the July 15
bookifier cohort and had no post-graduation review by the pinned cutoff.

Primary versus collateral is irrelevant to the fix. Both reviews are equally
valid word outcomes; rating 2 has the same non-success meaning in either role.

## After

Every tier now requires the current review to have rating ≥3 before evaluating
its cumulative/spacing criteria. A rating-2 word:

- remains acquiring;
- stays in its current box according to existing Hard behavior;
- keeps/resets its due interval according to existing logic;
- records the full review and counters normally;
- can graduate on a later successful review.

No thresholds, box intervals, credit roles, or rapid-retest rules change.
Guarded protocol-v2 re-test successes remain blocked separately by
`retest_gate`.

New acquisition rows also carry additive replay telemetry:
`graduation_policy_version = 2`, due-at-review, elapsed-since-last-review, and
post-counters. This does not affect scheduling; it creates an explicit
prospective boundary for validating the gate and later candidates without
backfilling historical rows.

## Regression test

The test recreates the observed recent pathway:

```text
Box 3
4 prior reviews / 3 correct
due
reviews span at least two calendar days
current rating = 2
```

Expected:

- `graduated` is false;
- state remains `acquiring`;
- box remains 3;
- no FSRS card is created.

Focused acquisition suite: `53 passed in 0.89s`.

## Risk and rollback

The behavioral surface is extremely narrow: historically 2/2,106 graduation
events (0.095%) used a non-success rating. The cost is one or more additional
acquisition reviews for such words; the benefit is that graduation never
asserts mastery on a confused answer.

Rollback is one condition in `submit_acquisition_review`; no schema or data
rollback is required.

No retroactive state repair is recommended from this evidence alone. Resetting
already-graduated words would mix a data intervention into ongoing recovery
and protocol-v2 measurement. Future FSRS outcomes should handle those two
existing cards normally.
