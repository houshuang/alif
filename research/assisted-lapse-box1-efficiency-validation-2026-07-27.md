# Assisted-lapse and Box-1 efficiency validation — 2026-07-27

## Decision

Ship two prospective, independently reversible policy changes:

1. Treat UI rating 2 on an FSRS card as an **assisted lapse**. The learner did
   not retrieve the word before reveal, so FSRS receives `Again`; recognition
   after reveal distinguishes it from rating 1 by suppressing the immediate
   relearning step. The user event remains `ReviewLog.rating=2`.
2. Reduce the planned Box-1 sentence exposure target from four to two. This
   does not cap failure practice: the rating-1 checkpoint/wrap-up queue still
   retries every failed word independently.

Every non-function word on a reviewed sentence contributes equally to all
analyses and updates. Primary/collateral labels describe why a sentence was
selected, not evidence quality.

## Before and after

| Area | Before | After | Intended benefit |
|---|---|---|---|
| FSRS rating 2 | Apply FSRS `Hard`; a mature card could receive another long interval | Store 2, apply `Again` with retention 0.90 and no relearning steps | Revisit failed unaided retrieval in roughly days rather than weeks, without an immediate duplicate |
| FSRS rating 1 | Standard `Again` plus retry-all | Unchanged | Preserve intensive recovery for complete misses |
| Box-1 planned repetitions | Four sentence cards per selected word | Two sentence cards per selected word | Spend fewer cards on massed practice and more on distinct due words/sessions |
| Collateral reviews | Full review evidence | Unchanged | Preserve the foundational all-words-equal invariant |

## Production-data evidence

Analysis used the immutable current snapshot at cutoff
`2026-07-26T19:54:29Z` (database SHA-256 prefix `3118a…`) and only review
events at or before that cutoff.

### Rating-2 calibration

There were 1,026 historical FSRS rating-2 events with a later matched outcome.
For 862 events with a usable first subsequent outcome:

| Prospective interpretation | Predicted recall | Actual strict recall | Brier score | Log loss |
|---|---:|---:|---:|---:|
| Existing FSRS Hard | 95.24% | 79.58% | 0.1812 | 1.0416 |
| Assisted lapse (`Again`, no steps) | 84.23% | 79.58% | 0.1544 | 0.8616 |

On representative mature cards, current Hard produced a median interval near
9 days, while the assisted-lapse policy produced about 2 days. Across the last
30 days, prospectively applying the new policy to the observed rating-2 stream
would add about 11 due reviews per day on average (maximum 21). This is a
material but bounded workload increase; it directly targets events where
unaided retrieval failed.

A rolling-origin replay on clean post-acquisition rows also improved strict
outcome calibration. Default FSRS had Brier 0.1764; rating-2 assisted lapses
with unchanged default parameters had 0.1698. Optimized weights improved it to
0.1645, but that incremental gain is not enough to justify a global retune.

### Box-1 replay

The selector was replayed on five historical production states at requested
limits 5 and 10, changing only `BOX1_MIN_EXPOSURES` from 4 to 2.

- Base due-word coverage was identical in every paired request.
- At limit 10, the candidate usually returned 3–5 fewer cards; the current
  state changed from 15 to 12.
- At limit 5, it saved 2–9 cards depending on the acquisition mix.
- Total distinct words shown can fall because fewer cards are shown; efficiency
  per card was similar. The saved capacity is available for another session or
  other due work rather than mandatory massed repetitions.

Observational cold follow-up did not show a positive same-session dose response:
first-session exposure bands had ≥3-day strict recall of 68.5% (one exposure),
60.6% (two), 63.9% (three), and 58.8% (four or more), across 1,311 episodes.
Those groups are confounded by difficulty, so they are evidence against
assuming four is better—not a causal estimate that one is optimal. Two is the
conservative tested reduction.

## Conformance and tests

- `backend/tests/test_fsrs.py` verifies original rating/counters, applied FSRS
  rating, selected retention, empty relearning steps, a bounded short interval,
  standard-policy telemetry, and the one-switch rollback.
- `backend/tests/test_sentence_selector.py` verifies that both box targets are
  two and projected repetition liability reaches zero after two cards.
- Existing sentence-review tests verify every submitted word still receives
  its own unchanged product rating.
- Existing retry state-machine tests protect rating-1-only requeue behavior;
  no retry code changed in this release.

Required release checks:

```bash
backend/.venv/bin/pytest -q backend/tests/test_fsrs.py \
  backend/tests/test_sentence_selector.py::TestWithinSessionRepetition \
  backend/tests/test_sentence_review.py
backend/.venv/bin/pytest -q backend/tests
```

## Telemetry boundary and monitoring

New FSRS rows stamp `fsrs_scheduler_policy_version=2`,
`fsrs_policy`, `fsrs_assisted_lapse`, `fsrs_rating_applied`,
`fsrs_desired_retention`, and `fsrs_relearning_steps_seconds`. Analyses must
segment at this boundary and must retain the original `ReviewLog.rating`.

Monitor immediately and at 3, 7, and 14 days:

1. rating-2 first-follow-up strict recall and elapsed interval;
2. rating-2 daily due arrivals and overall session completion;
3. Box-1 cards returned, acquisition-repeat cards, distinct due/all-word
   breadth, and Box-1→FSRS graduation;
4. rating-1 retry counts and completion, which must not fall because of this
   change.

Do not interpret primary/collateral splits as different validity. They may be
reported only to diagnose selector behavior.

## Risks and rollback

- **Too much rating-2 workload.** Recognition-after-reveal may sometimes be a
  momentary slip. If added due arrivals materially reduce completion or do not
  improve strict follow-up recall, set `FSRS_ASSISTED_LAPSE_ENABLED=False` and
  restart the backend. Existing cards remain valid; no historical rewrite is
  needed.
- **Box-1 under-encoding.** If post-boundary Box-1 graduation or ≥3-day strict
  recall degrades while workload does not improve, restore
  `BOX1_MIN_EXPOSURES` and `MIN_ACQUISITION_EXPOSURES` to 4. Rating-1 retries
  are unaffected either way.
- **Combined-change attribution.** The policies touch disjoint populations and
  have separate telemetry, constants, and rollback switches. Validate each
  outcome against its own exposure boundary.
- **No retrospective repair.** Historical rating-2 cards are not rewritten.
  Snapshot simulation showed that mass repair could disrupt the due set and
  selector breadth; only future reviews receive the new update.

## Deferred changes

No global FSRS parameter optimization, acquisition graduation-spacing gate,
historical card rewrite, or acquisition→FSRS initialization change ships here.
Each needs a version-segmented state/workload replay before activation.
