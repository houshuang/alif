# Aggressive learning-policy update — 2026-07-31

## Decisions

Three changes passed the implementation gate:

1. Accelerate the exact-form experiment by enrolling the first successful form
   review after an earlier failure and using a seven-day prospective endpoint.
2. Record complete presentation evidence for every mapped token, while keeping
   function words and names exposure-only.
3. Require a second-day successful acquisition confirmation before early
   graduation.

The changes address different problems. The form pilot tests a causal sentence
selection policy. Complete token evidence repairs future measurement. The
graduation rule directly changes learning now based on the strongest adjusted
historical lever and a bounded workload replay.

## Why the form experiment was accelerated this way

| Protocol | Assignments | Per active day | Endpoint window |
|---|---:|---:|---:|
| Original first-appearance success | 504 | 3.04 | 14 days |
| First successful appearance | 582 | 3.51 | 14 days |
| First success + shorter expiry | 597 | 3.60 | 7 days |

At seven days, historical ordinary scheduling yielded a later all-word outcome
for 78.7% of reconstructed episodes, an exact-form outcome in a different
sentence for 29.8%, and a successful exact-form ITT outcome for 27.3%. For an
assumed +20-point treatment effect, Monte Carlo power at 200 assignments was
83.8%. That moves the projected mature 200-episode read from roughly 80 elapsed
active/maturation days to about 63.

Increasing treatment to two reserved cards per session was not selected.
Treatment assignments average under two per active day and normally span
multiple sessions, so the additional reserved slot is unlikely to be the trial
bottleneck. It would, however, double the maximum immediate displacement of
ordinary due sentences.

## Distributed-day workload replay

The explicit-reason cohort contained 43 same-day early graduations over 22
active days: 33 first-correct and 10 perfect-accuracy. That is 1.96 deferred
graduates per active day. A stress-shaped historical day contained 16; using
the old scheduler's later review times produced a peak pending proxy of 19.
The new policy schedules Box 2 for the following day, so it should resolve
faster than that proxy.

Thirty-nine of the 43 eventually had a successful review on another day by the
cutoff. This establishes deliverability, not benefit. The benefit estimate
comes from the separate longitudinal spacing analysis and supporting
experimental literature; prospective outcome telemetry now carries an explicit
policy version.

## Is break analysis useful without another long break?

Yes, but its scope is limited. The existing two-week break can estimate one
within-learner shock: which pre-break histories survived, how recovery varied by
stability/form/word class, and whether absolute due thresholds failed. It does
not require another break to produce those diagnostics.

Another long break would be needed for strong prospective replication of that
specific shock. Smaller natural gaps—weekends, travel days, and 2–5-day pauses—
can test whether the same slope generalizes, but they are not interchangeable
with a two-week interruption. Therefore this is useful research and recovery
planning, not the next immediate learning intervention.

## Can ranking-regret analysis improve learning?

Potentially, but the analysis itself does not. Its value is a decision gate:

- if FSRS probability errors preserve the top-k items and do not move cards
  across due/introduction thresholds, recalibration changes no learning;
- if they cause fragile words to miss limited sessions or cause safe words to
  crowd out acquisition, then a threshold/ranking correction can materially
  improve retention per card.

Thus opportunity 4 is mostly analytics until it identifies actual selection
regret. It is important because it prevents deploying mathematically prettier
probabilities that do not change the learner's experience.

## Can contextual diversity improve learning?

Yes in principle: controlled studies report transfer benefits from encounters
across different texts. But Alif's adjusted estimate was only +0.6 points with
an interval including zero. A global diversity multiplier would currently be a
speculative intervention and can displace more urgent material.

The useful next step is a workload-matched randomization after the exact-form
pilot: for a due form with two equally safe sentences, choose repeated versus
new context and use a later common assessment. If that confirms a meaningful
effect, context diversity becomes a real selector feature. Until then,
opportunity 5 is an experiment with plausible learning value, not merely
analytics—but it is not yet a justified global policy.
