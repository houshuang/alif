# Selector S1b bounded replay — coverage gain, workload failure

## Decision

**Do not shadow or deploy S1b.**

S1b fixes S1's coverage regression. Across every paired request, its base
selection covers more due words than S0, with no quality regression observed.
However, it changes which acquisition words enter the base set, causing the
existing acquisition-repetition phase to add substantially more cards. That
hidden workload violates experiment isolation and is unsafe against the
current 32.2% approximate session-completion baseline.

The result is promising evidence for a better set-cover seed, not evidence for
a live selector policy.

## Candidate

S1b:

1. scans past unserviceable priority obligations;
2. selects at most three protected cards;
3. accepts a protected candidate only when its marginal due-word coverage
   equals the best currently available candidate (zero allowed coverage loss);
4. when the priority obligation is maintenance/FSRS, rejects opening
   candidates containing due acquisition words;
5. otherwise preserves existing scoring, quality, recency, diversity,
   passages, acquisition repetition, and all-word credit.

It is available only through the explicit
`selector_policy="s1b_coverage_budgeted"` argument. The default remains S0.

## Pinned replay

The replay contract matches the rejected S1 analysis:

```text
snapshot cutoff:  2026-07-25T17:30:44.409771Z
database SHA-256: 3b8ba1d566185ebe908139e24851f4f02214dbd6d6334a3638b6da5f30ef0069
limits:           5, 10, 20
rounds/limit:     4
paired requests:  12
```

Each pair receives identical state and exclusions. Successive rounds exclude
the union of prior S0/S1b sentence IDs. Intro mutation, review submission, and
interaction logging are disabled. SQLite is immutable and query-only.

The replay now separates:

- **base coverage:** selected cards before acquisition repetitions;
- **total coverage:** all returned cards, including acquisition repetitions;
- **returned workload:** actual card count after repetitions.

This separation was necessary: counting total due words without total cards
made the first S1b result look better than it really was.

## Aggregate results

| Metric | Result |
|---|---:|
| Mean base due-word coverage delta | **+7.75** |
| Base due-coverage regressions | **0/12** |
| Mean total due-word coverage delta | **+16.25** |
| Total due-coverage gains | **12/12** |
| Mean returned-card delta | **+6.75** |
| Requests with more returned cards | **11/12** |
| S1b three-card opening filled | **12/12** |

At the undepleted normal ten-card request:

| Policy | Base due words | Total due words | Returned cards | All-word breadth |
|---|---:|---:|---:|---:|
| S0 | 23 | 34 | 17 | 97 |
| S1b | 30 | 51 | 23 | 141 |
| Delta | +7 | +17 | **+6** | +44 |

At the undepleted five-card request, S1b returns 17 cards rather than S0's 6.
The additional coverage is therefore not workload-neutral.

## Why the workload changes

S1b's protected opening cards themselves exclude acquisition-due words when
serving maintenance priorities. But removing more maintenance obligations from
`remaining_due` changes the later greedy choices. Those later choices include
more acquisition words, which activate the unchanged Box-1/Box-2 repetition
phase. The selector and acquisition delivery mechanisms are therefore coupled
even though S1b does not directly modify repetition constants.

This is exactly the kind of interaction the replay was intended to expose.

The extra cards may contain useful learning—every word in them counts—but
their value cannot be inferred from breadth alone. Box-1 same-session
repetitions are not equivalent to later cold recall, and completion is already
low. Advancing S1b would combine a selector allocation experiment with an
acquisition breadth/workload experiment.

## Older material-state robustness check

A second replay used the local historical production copy
`backend/data/alif.prod.db`. Although its filesystem mtime is May 19, the
latest review and sentence-verification timestamps show that its logical
cutoff is May 12:

```text
source SHA-256:       27416638272bc26d55b0f279aea22a0380e4728a5f5dfcf80c66d4c07cac53c0
logical cutoff:       2026-05-12T14:55:50.375703Z
mapping gate active:  2026-04-16T00:00:00Z
migrated-copy SHA:    c8d77fe678a50197aa703ecb4a303de3a7a24dbfce146be0040514151a8ac7d6
```

The source was never modified. A temporary copy was migrated to the current
schema, then both arms ran through the current selector with the historical
mapping-verification cutoff explicitly pinned. This is a historical
**material-state robustness check**, not a reconstruction of the May selector
code.

Results:

| Metric | Historical state |
|---|---:|
| Mean base due-word delta | +1.25 |
| Base due regressions | 0/12 |
| Mean returned-card delta | -0.83 |
| Requests with more cards | 4/12 |
| Mean all-word breadth delta | **-5.00** |
| All-word breadth regressions | **8/12** |
| Mean base all-word breadth delta | **-0.92** |
| Base all-word breadth regressions | **6/12** |

S1b's current-snapshot breadth gain therefore does not generalize. On the older
material state it trades away collateral learning even while targeted due
coverage rises. Since those collateral reviews are fully valid, the automatic
verdict now treats both total and base all-word breadth regressions as hard
failures.

Artifacts:
`research/baselines/selector-s1b-historical-material-2026-05-12/`.

## Next candidate: workload-neutral S1c

Do not cap or silently drop required retries. Instead, calculate a selector
candidate under an explicit **base-selection acquisition-cost constraint**:

1. preserve S0's existing acquisition-repetition policy;
2. estimate the repetition liability of each base candidate from the due
   acquiring words and their Box-1/Box-2 targets;
3. optimize marginal due coverage per projected returned card;
4. require paired returned-card count to be no higher than S0 in bounded
   replay;
5. report maintenance and acquisition coverage separately;
6. reject any five-card completion-risk regression even if total breadth rises.

This remains selector work. Changing the 4×/2× repetition targets belongs to
the separately versioned acquisition deep lane.

## Verification

- `108 passed in 3.23s` across selector, cohort, replay, and baseline tests.
- Two complete S1b replay runs were byte-identical.
- Database SHA-256 remained unchanged.
- Artifacts:
  `research/baselines/selector-s1b-bounded-2026-07-25/`.

## Scope limits

S1b is rejected from one bounded snapshot without needing historical
reconstruction. Historical replay would not repair the identified confound.
S1c must first pass the same workload guardrail before historical session
reconstruction or shadow mode is worth the cost.
