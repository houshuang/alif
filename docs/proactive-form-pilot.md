# Proactive exact-form retrieval pilot

## Status

Production activation: **enabled 2026-07-31** with
`ALIF_PROACTIVE_FORM_EXPERIMENT=1`.

Aggressive protocol update: **2026-07-31**. New proactive episodes use the
first successful appearance of a form (an earlier miss no longer disqualifies
it) and mature after 7 days. Existing episodes retain their stored 14-day
expiry, and yellow-confusion episodes continue to use 14 days.

The flag expands the existing randomized exact-surface pilot. The original
yellow-confusion trigger remains active independently of this flag. Turning the
flag off stops only new `successful_first_form_exposure` assignments; it does
not delete prior episodes or change their outcomes.

## What the pilot is testing

Alif schedules a canonical lemma, but a learner reads a particular surface
form. Historical modeling found lower success when a familiar lemma appeared
in a new form. The pilot asks whether one intentional retrieval of that same
meaningful form in a different sentence improves form-specific retrieval
without creating more work or changing the lemma scheduler.

The randomized unit is an eligible lemma-and-form episode. The intervention is
sentence representation, not card timing:

- **control:** ordinary sentence selection continues unchanged;
- **treatment:** when that canonical lemma is already due, Alif may use a
  different normal-quality sentence containing the exact form and mark that
  lemma as the sentence's primary retrieval target.

At most one treatment sentence can be reserved in a reading session. It
occupies an existing requested session slot and covers an already-due lemma.
The pilot never creates a card, makes a lemma due, changes a due date, changes
the learner's rating, changes review credit, or increases session length.

## Assignment, exactly

Assignment is evaluated after Alif has recorded a normal word-level reading
review. A proactive episode is created only if all of the following hold:

1. `ALIF_PROACTIVE_FORM_EXPERIMENT` is truthy (`1`, `true`, `yes`, or `on`).
2. The review is reading, not listening, and is not an acquisition review.
3. The word-level rating is 3 or 4, and this is the form's first successful
   review. Earlier misses or confused reviews are allowed; an earlier success
   is not.
4. All displayed instances credited to the canonical lemma normalize to one
   unambiguous surface form.
5. The established surface-form counters contain exactly one success after
   subtracting misses and confused reviews from displays.
6. The normalized form differs meaningfully from the citation form. Bare
   citation forms and pure article/conjunction/preposition prefixes are
   excluded.
7. Morphology classification is one of `verb_present`, `verb_other`,
   `derived_form`, `enclitic`, or `inflection`.
8. The same canonical lemma has no unresolved exact-form episode.
9. This lemma/form pair has never previously received an episode.
10. A different, reviewable, non-passage sentence exists whose canonical
    member rows contain this one normalized form and no other form of the same
    lemma. Explicitly rejected LLM sentences are excluded.

The arm is a deterministic 50/50 hash of experiment version, immutable review
identity, canonical lemma ID, and normalized form. Retrying a client request
therefore cannot change assignment. Episode state is stored without a schema
migration under
`UserLemmaKnowledge.variant_stats_json["__exact_surface_v1"]`.

The episode records prior form exposures and `trigger_policy=first_success`, so
first-appearance successes can be separated from recovery after an initial
failure.

If a review is marked confused, the pre-existing `yellow_confusion` trigger can
still assign an episode even when the proactive flag is off. Red failures do
not enter the proactive extension; their established lapse/acquisition
recovery paths are left alone.

## What happens in a later session

Only a reading session can deliver treatment. The selector first builds the
normal pool of due, reviewable sentences and applies the existing mapping,
quality, recency, scaffold-comprehension, unknown-density, and source gates.
A candidate qualifies for an open treatment episode only when:

- the episode's canonical lemma is already due and is not acquiring;
- the sentence is not the trigger sentence and is not a passage;
- every occurrence belonging to that canonical lemma has the episode's exact
  normalized form; and
- the sentence survives all normal selection gates.

The selector orders open treatment opportunities by oldest trigger, then
normal candidate score and stable IDs. It reserves no more than one candidate,
sets the tested lemma as the primary target for that sentence, and removes all
due lemmas covered by the sentence from the remaining due set exactly as a
normal selected sentence would. If no eligible sentence is available, nothing
special is inserted and the episode remains open until its stored expiry: 7
days for new proactive episodes and 14 days for yellow/legacy episodes.

Control episodes never influence sentence selection. Treatment assignment is
therefore intention-to-treat: inability to deliver is part of the measured
effect rather than a reason to remove an episode from analysis.

## What is measured

Primary endpoint:

> A successful word-level review (rating 3 or 4) of the exact normalized form
> in a different sentence within the episode's stored window. If no such review
> occurs, the mature episode is a failure.

Every word credited from the sentence counts, regardless of whether the
scheduler called it primary or collateral. This is essential: reconstructed
history found a later all-word endpoint for 87.3% of eligible episodes but a
later primary-labelled endpoint for only 8.5%.

The episode also records:

- the first later word-level review of any form, including rating, confusion,
  exactness, context repetition, sentence, and credit label;
- the first later exact-form review in a different sentence, with the same
  word-level outcome data;
- legacy first-later-primary and exact-form-primary fields for backward
  compatibility;
- trigger kind, morphology category, candidate count, arm, and timestamps.

Undo is symmetric. Undoing a trigger review deletes its episode; undoing an
outcome clears that endpoint so a later valid review may fill it.

## Reading the results

Use:

```bash
cd backend
.venv/bin/python scripts/analyze_exact_surface_experiment.py \
  --db data/alif.db \
  --cutoff 2026-08-31T00:00:00Z \
  --output /tmp/exact-surface-readout.json
```

The analyzer opens a hash-recorded immutable/query-only database, includes only
episodes past their individually stored expiry in the primary analysis,
reports treatment minus control risk difference, Fisher's exact test, and a
95% bootstrap interval clustered by canonical lemma. Do not analyze only
delivered episodes: delivery is affected by treatment and conditioning on it
would bias the comparison.

Milestones:

- 40 assigned episodes: inspect 40–60% arm balance and operational failures;
- 80 mature episodes: first descriptive read, not an efficacy decision;
- 200 mature episodes: planned efficacy read for an effect around 20
  percentage points.

The original frozen-history simulation reconstructed 504 serviceable
first-appearance opportunities, about 3.04 per active learning day. The
aggressive replay found 597 first-success opportunities with 7-day expiry,
about 3.60 per active day. Historical ordinary scheduling supplied a later
all-word outcome within 7 days for 78.7% and a successful exact-form outcome in
a different sentence for 27.3%. At that rate, 200 assignments take roughly 56
active learning days plus 7 days of maturation, versus about 66 plus 14 under
the original protocol. Simulated power at 200 remained 84.2% for an assumed
20-point effect (83.8% in the final 20,000-trial run).

A second treatment slot was rejected: treatment assignments average fewer than
two per active day, normally spread across multiple sessions, while another
reserved slot would increase displacement of other due material. The one-slot
cap therefore remains. The simulation estimates feasibility and power, not the
treatment effect.

## Safety checks and stopping rules

At each milestone verify:

- at most one exact-form treatment selection per reading session;
- zero acquisition or listening assignments/deliveries;
- no card creation, due-date mutation, rating rewrite, or session-size growth;
- no concurrent open episodes on one canonical lemma;
- no repeat assignment for one lemma/form pair;
- 40–60% treatment allocation after 40 assignments;
- at least 70% first-later-all-word endpoint yield by day 7; and
- treatment does not lower observed next-all-word success by more than five
  percentage points.

This is an N-of-1 policy comparison. Intervals quantify uncertainty across this
learner's items and episodes, not generalization to other learners.

## Operations and rollback

Enable:

```text
ALIF_PROACTIVE_FORM_EXPERIMENT=1
```

Then restart `alif-backend`. Confirm the running process has the variable and
watch the append-only interaction log for:

- `exact_surface_experiment_assigned` with
  `trigger_kind=successful_first_form_exposure`;
- `exact_surface_experiment_all_word_outcome`; and
- `exact_surface_experiment_exact_all_word_outcome`.

Rollback is immediate and data-preserving: set the variable to `0` (or remove
it) and restart `alif-backend`. No new proactive episodes will be assigned.
Existing episodes stay available for audit and can still receive outcomes; the
older yellow-confusion pilot is unaffected.

Implementation:
`backend/app/services/surface_form_experiment.py`,
`backend/app/services/sentence_selector.py`, and
`backend/app/services/sentence_review_service.py`.

Reproducibility and rationale:
`research/learning-policy-deployment-candidates-2026-07-31.md`.
