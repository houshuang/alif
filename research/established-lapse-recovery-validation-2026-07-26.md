# Established-lapse recovery — immediate bounded activation

## Decision

Activate only the targeted `r1_established_lapse` selector policy.

Do not activate the broader S1c/general debt optimizer. It improved coverage
on some current requests but increased returned workload on historical states.
The deployed candidate is deliberately narrower: it can spend one existing
later maintenance slot on an older lapse only when the full returned workload
remains non-regressing.

This is an immediate delivery improvement, not a promise that 30 days must pass
before learning can improve. The next eligible session can benefit. Retention
effects still require prospective observation and must not be invented from a
selector replay.

## What the history says

Pinned cutoff: `2026-07-26T07:43:00Z`.

The right-censored audit contains 2,208 non-acquisition rating-1 word outcomes
since March across 1,103 lemmas. Of those events:

- 1,088 had pre-card stability ≥7 days;
- 435 had pre-card stability ≥30 days;
- 1,545 were labelled collateral and 663 primary, but the label changes no
  counting, validity, eligibility, or outcome.

Current Relearning stock at the cutoff:

| Stock | All | Due now |
|---|---:|---:|
| All lapsed words | 90 | 79 |
| Latest pre-lapse stability ≥7d | 37 | 37 |
| Latest pre-lapse stability ≥30d | 21 | 21 |

The latest failure is authoritative. During implementation, replay exposed a
bug that could scan backward past a recent low-stability repeat failure and
reuse an older high-stability lapse. The final code records the lemma as seen
before applying the threshold, so a fragile current failure cannot be
misclassified as established.

### Few-minute follow-up

In the last seven days, 79 established-lapse events had a complete ten-minute
observation window. Fourteen (17.7%) received a follow-up inside ten minutes,
at a median 4.16 minutes; 12/14 were spontaneous rating-3 retrievals and 2/14
failed again. This window mixes old and new retry protocols, so the delivery
fraction is not a clean retry-v2 effect estimate. It does confirm that the
recent few-minute mechanism is landing at its intended delay when it fires.

Across the last 30 days, only 24/251 established lapses had a follow-up within
two hours, versus 136/172 within seven days. The evidence supports keeping the
rapid retry protocol and adding a later selector lane; it does not justify
changing FSRS parameters or acquisition repetition.

Rating 2 is not counted as spontaneous retrieval. It means the word was not
recognized before the flip but felt known after reveal, and is reported
separately as assisted recognition.

Artifacts:
`research/baselines/lapse-followup-2026-03-01-to-07-26/`.

## Before and after

Before:

1. rating-1 retry-v2 queues the failed sentence for a checkpoint/wrap-up retry;
2. after that session, lapsed words receive the global `LAPSED_BOOST = 3.0`;
3. once-established and fragile repeated failures compete in the same greedy
   pool;
4. no slot is explicitly protected for an older lapse.

After:

1. retry-v2 is unchanged and remains responsible for the few-minute retest;
2. S0 selection and mandatory acquisition repetitions run unchanged;
3. the selector identifies currently due lapsed words whose **latest**
   non-acquisition rating-1 pre-card stability was ≥7 days;
4. it sorts eligible targets by prior stability, then overdue age;
5. it may make one one-for-one swap in a later maintenance slot;
6. all existing word outcomes in every selected sentence remain equally valid.

## Hard activation guards

A recovery swap is rejected unless all of these hold:

- the target is not already served;
- the target has a serviceable non-passage sentence;
- neither replacement nor victim contains a cold word that the shared
  canonical intro-state helper would teach first;
- neither replacement nor victim covers a due acquiring word;
- the victim is not in the five-card priority opening, an exact-surface pilot,
  an acquisition repeat, or a generated/maintenance passage;
- the replacement is not a near duplicate;
- returned and base due-word counts do not fall;
- returned and base **canonical, creditable all-word** breadth do not fall
  (function words, proper names, and duplicate variants cannot inflate it);
- both the full live selection and the cold-filtered speculative selection
  satisfy the due/breadth guards;
- projected Box-1/Box-2 acquisition-repeat liability does not rise;
- sentence quality does not fall;
- returned card count stays unchanged by construction.

The cold-state comparison originally used a conservative state label. Replay
showed that this did not exactly match speculative response filtering for
mature encountered words. The final implementation calls the same canonical,
counter-aware cold-state helper as the response pipeline.

## Five-snapshot replay

Each snapshot ran paired S0/candidate requests at limits 5, 10, and 20 across
four union-depletion rounds: 12 requests per snapshot, 60 total. The June/July
historical databases were copied, migrated to the current schema, checkpointed
into new pinned derivatives, and matched to newly generated WP1 baseline
identities. Original snapshots were not modified.

| Snapshot | Requests | Recovery served | Distinct recovery lemmas | Card increases | Due regressions | All-word regressions | Mean due delta | Mean all-word delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-06-28 | 12 | 5 | 4 | 0 | 0 | 0 | +0.08 | +0.67 |
| 2026-07-12 | 12 | 9 | 4 | 0 | 0 | 0 | +0.17 | +1.00 |
| 2026-07-18 | 12 | 3 | 2 | 0 | 0 | 0 | +0.08 | +0.75 |
| 2026-07-25 | 12 | 6 | 3 | 0 | 0 | 0 | +0.17 | +1.50 |
| 2026-07-26 | 12 | 12 | 3 | 0 | 0 | 0 | 0.00 | +1.25 |
| **Total** | **60** | **35** | — | **0** | **0** | **0** | — | — |

Base due coverage, base all-word breadth, mean quality, and opening-priority
also had zero regressions in all 60 requests.

Artifacts:
`research/baselines/established-lapse-recovery-2026-07-26/`.

Because the repository is public, committed replay rows retain aggregate
coverage/workload evidence but omit sentence IDs, lemma IDs, due-word ID lists,
and production-database hashes. The replay still verifies exact input hashes
locally before execution and rejects any mismatch.

## Performance

On the pinned current snapshot, repeated 20-card speculative builds were
already above the historical sub-second aspiration. In the final paired
measurement, S0 median was 1.67s and the recovery policy median was 1.37s
(eight runs each, warm process). This is not evidence that recovery speeds the
selector, but it rules out a material latency regression in that run. Session
build latency remains a separate existing optimization target.

## Expected learning benefit

The lane should improve retention by shortening the tail between the rapid
same-session retry and the next ordinary appearance of words that had once
survived meaningful spacing. It directs an existing card—not an extra
card—toward a high-value reconsolidation opportunity. It also tends to increase
collateral breadth in replay, but that gain is a guardrail side effect, not the
primary claim.

It will not:

- clear all 37 currently established lapses immediately;
- cap or discard retries;
- change the meaning of rating 2;
- tune FSRS globally;
- accelerate Box 1 by pretending rapid recognition is durable retention;
- distinguish primary from collateral learning validity.

## Rollback and prospective checks

Rollback is one constant/default change:
`build_session(... selector_policy=SELECTOR_POLICY_S0)`. The change has no
schema migration, state rewrite, counter rewrite, or historical repair.
Response/session telemetry includes `selector_policy`, recovery reason, and
recovery count. Exact recovery lemma and lapse evidence stay in the private
server interaction log rather than the wildcard-CORS response.

Do not wait 30 days for the first safety read:

1. after 48 hours or the first 20 recovery-served sessions, whichever comes
   later, verify card count, completion, cold-card omissions, and recovery
   delivery;
2. after the first 50 recovered-lemma outcomes, compare next rating-1 rate and
   spontaneous rating≥3 rate with version-matched eligible lapses;
3. retain the four-week readout for retention, but roll back immediately on
   workload/completion regression.

## Open questions and risks

- The 7-day pre-stability threshold is a conservative heuristic, not an
  optimized causal cutoff.
- Snapshot replay validates serviceability and workload, not future answers.
- Union depletion stresses material availability but is not a historical
  session-state reconstruction.
- Only one lane slot is attempted; some requests have no safe swap.
- Retry-v2 evidence is version-mixed until enough v2-only events accumulate.
- FSRS remains overconfident in Relearning; this lane improves delivery, not
  calibration.
- Current session building is still slower than the documented sub-second
  aspiration and deserves a separately profiled optimization.
