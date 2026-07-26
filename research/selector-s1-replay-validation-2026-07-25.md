# Selector S1 bounded replay — validation and decision

## Decision

**Reject S1 as currently specified. Do not shadow or deploy it.**

S1 successfully scans past unserviceable high-priority obligations, increasing
the opening block from two to four serviceable cards on the pinned snapshot.
But reserving those extra cards changes downstream set cover and reduces total
due-word coverage. The regression is largest in short sessions, where learning
capacity is most constrained.

The production default remains S0. No review, scheduler, counter, acquisition,
retry, or production database state changed.

## Candidate under test

- **S0:** inspect only the first `min(5, limit)` frequency-priority obligations;
  unserviceable/vetoed entries silently reduce the opening block.
- **S1:** preserve the same frequency ordering and sentence scoring, but continue
  scanning lower-ranked obligations until five opening cards are found, the
  base limit is reached, or the candidate set is proven unserviceable.

The implementation is isolated behind an explicit `selector_policy` argument.
`build_session()` defaults to `s0`; S1 is invoked only by tests and replay.

## Replay contract

Inputs:

```text
snapshot cutoff:  2026-07-25T17:30:44.409771Z
database SHA-256: 3b8ba1d566185ebe908139e24851f4f02214dbd6d6334a3638b6da5f30ef0069
limits:           5, 10, 20
rounds/limit:     4
paired requests:  12
```

Each S0/S1 pair receives identical database state, cutoff, limit, and excluded
sentence IDs. After each pair, the next stress round excludes the union of
sentences returned by both arms. This tests progressively depleted serviceable
material without advantaging either arm.

SQLite uses `mode=ro&immutable=1` plus `PRAGMA query_only=ON`. Intro mutations,
interaction logging, and review submission are disabled. Every word present in
a selected sentence contributes equally to the all-word breadth metric.

This is a bounded end-state replay, not historical state reconstruction. Its
depletion rounds are stress cases, not simulated learner sessions.

## Results

| Base limit | Round | S0 due | S1 due | Δ due | S0 all words | S1 all words | Δ all words | S0 opening | S1 opening |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | 0 | 15 | 8 | -7 | 40 | 27 | -13 | 2 | 4 |
| 5 | 1 | 12 | 7 | -5 | 42 | 26 | -16 | 2 | 4 |
| 5 | 2 | 18 | 8 | -10 | 46 | 27 | -19 | 2 | 4 |
| 5 | 3 | 8 | 8 | 0 | 18 | 27 | +9 | 2 | 4 |
| 10 | 0 | 34 | 32 | -2 | 97 | 93 | -4 | 2 | 4 |
| 10 | 1 | 21 | 21 | 0 | 48 | 57 | +9 | 2 | 4 |
| 10 | 2 | 26 | 26 | 0 | 78 | 86 | +8 | 2 | 4 |
| 10 | 3 | 31 | 25 | -6 | 81 | 72 | -9 | 2 | 4 |
| 20 | 0 | 67 | 66 | -1 | 164 | 170 | +6 | 2 | 4 |
| 20 | 1 | 67 | 68 | +1 | 183 | 192 | +9 | 2 | 4 |
| 20 | 2 | 65 | 65 | 0 | 187 | 191 | +4 | 2 | 4 |
| 20 | 3 | 57 | 58 | +1 | 163 | 170 | +7 | 2 | 4 |

Aggregate:

- mean due-word coverage delta: **-2.42 words/request**;
- coverage regressions: **6/12** paired requests;
- coverage gains: **2/12**;
- S0 opening block: two cards in every request;
- S1 opening block: four cards in every request;
- neither policy reached five: the fifth opening obligation was genuinely
  unserviceable under current gates.

## Interpretation

The proposal's premise was correct: S0 underfills the opening block because it
stops scanning after five obligations rather than five serviceable cards.

The implied solution was not correct. Cards are the scarce resource, not
obligations. Filling four mostly single-obligation priority cards displaces
multi-word sentences selected by the greedy optimizer. At limit five, S1 loses
7–10 due words in three of four stress states. Equal collateral validity makes
that loss especially important: displaced collateral obligations are genuine
learning opportunities, not secondary credit.

The problem should therefore be reframed from “fill five opening cards” to
“guarantee service for a small number of priority obligations subject to a
global coverage-loss budget.”

## Proposed S1b for calculation, not rollout

The next replay candidate should:

1. reserve at most **two** priority cards initially;
2. scan past unserviceable obligations, as S1 does;
3. score a priority candidate by both priority rank and marginal due-word
   coverage;
4. add a third protected card only if its replacement cost is at most one
   distinct due obligation versus the greedy counterfactual;
5. record every unserved priority obligation and reason;
6. preserve current quality, diversity, recency, acquisition repetition, and
   all-word credit semantics.

Limits 5, 10, and 20 must all pass independently. A candidate with a mean gain
but any large short-session regression does not pass.

## Verification

- `107 passed in 3.42s` across selector, cohort, replay, and WP0/WP1/WP8 tests.
- Two full replay runs were byte-identical.
- Database SHA-256 was unchanged after both runs.
- Generated artifacts:
  `research/baselines/selector-s1-bounded-2026-07-25/`.

## Remaining limits

This result is sufficient to reject S1, but not to approve S1b:

- only one end-state snapshot is represented;
- historical ULK/FSRS state has not yet been reconstructed at each session;
- no answer/state transition or retained-learning outcome is simulated;
- active-book displacement is not yet measurable from this replay;
- the selector's post-selection acquisition repetition can change total
  returned card count and must remain a separate guardrail.

S1b should first pass this same bounded matrix, then historical reconstruction,
then shadow mode. No live experiment is justified yet.
