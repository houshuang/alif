# Distributed-day acquisition graduation

## Status

Production rollout flag: `ALIF_DISTRIBUTED_DAY_GRADUATION=1`.

This policy converts same-day early graduation into one next-day confirmation.
It is deliberately narrow: it changes acquisition graduation only. It does not
change FSRS ordering, sentence selection, review ratings, or session length.

## Why

The longitudinal analysis estimated approximately a 4.6 percentage-point gain
from practice distributed across additional calendar days after adjustment for
the available history (95% interval +2.0 to +7.4). The direction agrees with
controlled vocabulary-learning research. Meanwhile, the acquisition service
still allowed a new word to graduate after one correct review, or after several
correct reviews concentrated on one day.

Those rules treat immediate accessibility as consolidation. The new policy
requires one piece of evidence that working memory cannot supply: another
successful review on a different UTC date.

## Exact behavior

With the flag off, all existing graduation tiers behave as before.

With the flag on:

1. `first_correct`, `perfect_accuracy`, and `high_accuracy` may not graduate a
   word if all acquisition evidence is from one UTC calendar day.
2. A blocked first correct advances Box 1 to Box 2 and becomes due one day
   later. It does not restart or remain in a rapid same-session loop.
3. On a later UTC day, a successful review graduates immediately with reason
   `distributed_confirmation` when the word has at least two correct
   acquisition reviews and at least 80% cumulative acquisition accuracy.
4. A failed second-day review follows the normal failure/demotion rules; the
   policy never graduates on failure.
5. The existing elapsed-interval tier remains valid: a correct answer after a
   real gap of at least three days already proves distributed retention.
6. Intro-card and rapid-retest working-memory guards remain in force.

The intended marginal workload is therefore one spaced successful confirmation
per affected word, not another complete three-box cycle.

## Replay evidence and guardrail

On the frozen database, explicit graduation-reason telemetry contained 43
same-day early graduations over 22 active days:

- 33 `first_correct`;
- 10 `perfect_accuracy`;
- 1.96 affected words per active day on average;
- maximum 16 on the busiest historical day; and
- peak 19 pending confirmations when conservatively using the old scheduler's
  observed later-review timing.

Thirty-nine of 43 had a later successful review on another day by the cutoff.
The old scheduler's median time to that success was 3.27 days; the new policy
makes the confirmation due after one day, so that historical delay is a
conservative deliverability proxy, not the expected new delay.

Monitor Box-2 due count, total acquisition backlog, intro gating, and the
following immutable `ReviewLog.fsrs_log_json` fields:

- `distributed_day_policy_version`;
- `distributed_day_policy_enabled`;
- `distributed_days_confirmed`;
- `early_graduation_blocked`; and
- `graduation_reason=distributed_confirmation`.

Rollback is immediate: set `ALIF_DISTRIBUTED_DAY_GRADUATION=0` or remove it and
restart the backend. Already deferred words remain ordinary acquisition words
and can graduate under the prior tiers on their next review.

Reproducibility:
`backend/scripts/simulate_distributed_day_graduation.py`.
