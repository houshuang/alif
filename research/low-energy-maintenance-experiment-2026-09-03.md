# Low-energy maintenance experiment v1

**Preregistered:** 2026-09-03  
**Policy:** `low_energy_maintenance_v1`  
**Planned duration:** 60 days after production activation  
**Rollback:** set `ALIF_LOW_ENERGY_MAINTENANCE_EXPERIMENT=0` and restart the
backend. This restores the legacy intake, collateral scheduling, ordering,
density, and maintenance-passage policy. The policy version is emitted with
every session so the frontend follows the same passage setting.

## Why this experiment exists

The learner can usually sustain about 30 cards per day but often does not have
the energy for long or unusually dense cards. The immediate goal is to avoid
backsliding while making slow progress. Current retention looks broadly real,
but the system has two measurement and workload problems:

1. A sentence can create five or more simultaneous word obligations. Those
   cards are much harder than ordinary contextual reading and dominate misses.
2. Clean collateral appearances of very strong, far-early words update FSRS as
   though they were deliberate retrieval tests. In the recent sample, 63.5% of
   FSRS judgments were early collateral. This can keep moving mature cards
   forward without systematically testing them when actually due.

The learner also reports that many misses are confusions among two or three
plausible candidates. That argues for a different sentence context, not an
isolated extra flashcard or an automatic long passage.

## What the package changes

The visible target remains **30 sentence cards per day**. The experiment changes
what those cards contain and what evidence is allowed to move a schedule.

1. **Four-obligation ceiling.** An automatic ordinary sentence card may contain
   at most four actionable due words. The count includes due collateral outside
   the active cohort or frequency lane. Automatic maintenance passages are
   suspended; longer reading remains available through explicit reading modes.
2. **Exposure-only mature collateral.** A word appearance does not update FSRS
   only when all of these are true: reading mode, clean rating, collateral rather
   than primary, state `known`, card not due, and current retrievability at least
   0.97. The token encounter and exact-form evidence are still recorded.
   Primary words, due words, red/yellow outcomes, acquiring/learning/lapsed
   words, listening, and low/unknown retrievability keep normal scheduling
   credit.
3. **History-risk ordering.** Within eligible due work, the selector gives more
   weight to recent and lifetime failures, lapsed/acquiring state, overdue
   pressure relative to stability, and lack of distributed successful evidence.
   Frequency remains a curriculum signal but no longer outranks clear fragility.
4. **Confusion context rescue.** A recently named lexical confusion may reserve
   at most one existing sentence slot in a reading session. It must test the
   failed due lemma in a different sentence and omit the named confusor. It does
   not create a card, make a word due, or call an LLM during session assembly.
5. **Intake matched to capacity.** The true-new ceiling is two words per UTC day.
   While any existing recovery trigger is active, intake is zero before 40
   same-day primary reading cards, one after 40 cards with at least 80% primary
   accuracy, and two after 100 cards with at least 85% primary accuracy. On a
   healthy low-debt day the ceiling is two.

## What the experiment is testing

This is intentionally a **bundled policy experiment**, because the practical
question is whether the whole daily routine becomes sustainable while old
knowledge holds. It cannot identify the isolated causal effect of each component.

- **Feasibility hypothesis:** thirty cards feel materially less draining when
  dense cards and automatic passages are removed.
- **Retention-validity hypothesis:** converting only trivial mature collateral
  to exposure will make scheduled tests harder but more meaningful, while old
  word retention remains stable.
- **Debt hypothesis:** less far-early rescheduling and almost no new intake will
  let the main due stock and Box-1 backlog fall rather than compound.
- **Prioritization hypothesis:** fragile old words are actually sampled instead
  of being displaced by frequent easy words.
- **Confusion hypothesis:** a different context without the named competitor
  reduces repeated candidate confusion without adding workload.

## Frozen pre-experiment baseline

The latest analysis available on 2026-09-03 found:

| Measure | Baseline |
|---|---:|
| Strict main-lane FSRS due | 749 |
| Raw FSRS due | 890 |
| Actionable Box 1 | 41 |
| Total acquiring | 56 |
| Recent scheduled clean rate | 89.0% |
| Old-word clean after ≥7 days | 85.8% |
| Old-word clean after ≥14 days | 81.5% |
| Old-word clean after ≥30 days | 73.6% |

The retention estimates use scheduled reading evidence. “Old” means first
learning at least 90 days before experiment start. The exact baseline values are
also frozen in `analyze_low_energy_maintenance_experiment.py`.

## Checkpoint schedule and decisions

Run the read-only checkpoint report against a pinned database snapshot and the
matching interaction logs:

```bash
cd backend
.venv/bin/python scripts/analyze_low_energy_maintenance_experiment.py \
  --db /path/to/alif-checkpoint.db \
  --interaction-log-dir /path/to/logs \
  --start 2026-09-03T00:00:00Z \
  --output ../research/baselines/low-energy-maintenance-checkpoint.json
```

| Checkpoint | What it can decide |
|---|---|
| Day 3 | Instrumentation, passage suspension, density ceiling, intake ceiling, exposure invariants, crashes, and subjective burden. Not retention. |
| Day 7 | Direction of daily volume and debt; gross old-word retention damage; whether risk ordering is servicing useful work. |
| Day 14 | First interpretable ≥7-day old-word retention and confusion-service readout. Tune a component only if the failure mode is localized. |
| Day 30 | Whether the routine is sustainable and main/Box-1 debt is burning down without retention loss. |
| Day 60 | Keep, adjust, or roll back the package. This is the primary decision point. |

At each checkpoint also record two subjective answers: **How many minutes did
30 cards take?** and **How many cards felt overwhelming because too many words
were uncertain at once?** A technically safe policy that causes the learner to
stop doing the reps has failed.

## Success criteria at day 60

Keep the package if all of the following are broadly true:

- the learner normally completes about 30 cards on active days without greater
  perceived effort;
- no automatic card breaches the four-obligation ceiling;
- strict main FSRS debt trends down from 749 rather than stabilizing in the
  750–900 range, and actionable Box 1 moves toward fewer than 10;
- old-word clean rate after ≥7 days remains at least 80% and preferably near the
  85.8% baseline;
- old-word clean rate after ≥14 days remains at least 78% and preferably improves
  from 81.5%;
- new intake stays at zero during recovery and never exceeds two per day;
- named-confusion rescue is delivered when eligible inventory exists, without
  extra cards. If inventory is the limiting factor, decide after day 14 whether
  a bounded background two-sentence contrast generator is warranted.

Do **not** require the raw scheduled clean percentage to remain at 89%. The new
denominator deliberately removes the easiest high-retrievability collateral
events; a modest drop can mean that the measurement became more honest.

## Danger signals

### Stop and roll back immediately

- any automatic card with five or more actionable scheduled words;
- any exposure-only event that is primary, due, red/yellow, not `known`, in
  listening mode, or below 0.97 retrievability;
- more than two true-new acquisitions in one UTC day;
- an automatic maintenance-passage card while the policy is active;
- strict main FSRS due reaching 1,000 after the first week;
- errors that prevent sessions or reviews from being built or saved.

### Warn and investigate before changing the package

- after day 7, strict main due is at least 100 higher than baseline or 15% higher;
- with at least 20 qualifying tests, old-word ≥7-day clean rate falls below 80%,
  or ≥14-day clean rate falls below 78%;
- after four or more active days in the first week, median volume is below 24
  cards per active day;
- p90 card time rises materially or the learner reports more overwhelming cards;
- Box 1 fails to decline despite intake remaining zero;
- confusion rescues repeatedly use the trigger context, contain the named
  confusor, or expand session length.

Warnings are diagnostic, not automatic proof that the whole experiment failed.
For example, low throughput with clean density telemetry would point to card
content or UX; falling retention with stable workload would point first to the
exposure threshold or risk prioritization.

## Lifecycle and gate audit

The experiment does not add a knowledge state or a new transition. It narrows
automatic `encountered → acquiring` volume through the existing
`start_acquisition()` chokepoint and skips an FSRS update only for the explicit
mature-collateral predicate. Token evidence and `total_encounters` still record
the appearance.

- **Comprehensibility gates (main and fill):** unchanged state semantics;
  acquiring/learning/lapsed scaffolds retain their current treatment.
- **Unknown-scaffold cap:** unchanged; the new due-density ceiling is an
  additional independent rejection.
- **Pipeline backlog and recovery permission:** unchanged triggers, smaller
  earned budgets.
- **Focus cohort/frequency lanes:** unchanged; density counts the full due stock
  before these filters so off-lane collateral cannot escape the ceiling.
- **Variant resolution:** exposure classification happens after canonical
  resolution and uses the canonical card.
- **Intro cards:** existing eligibility remains; fewer promotions create fewer
  cards, and leech reintroduction still bypasses the true-new cap.
- **Listening readiness:** unchanged; exposure-only is reading-only.
- **Function words/proper names:** remain inert and never schedule.
- **Mapping/quality gates and material generation:** unchanged; confusion rescue
  selects only existing reviewable verified inventory and session build remains
  free of LLM calls.

## Expected observable difference

In the first days, the learner should still see roughly 30 cards but fewer
“everything in this sentence is due” piles, no long automatic passage cards,
and essentially no new vocabulary. Some familiar collateral words will still be
clickable and counted as encounters but will not have their FSRS dates pushed
forward. Fragile/due/failed words will keep behaving normally.

After roughly two months, the intended outcome is a smaller Box-1 queue and a
declining main due stock, with the old-word delayed clean rates still near their
current bands. Progress in distinct new words will be much slower—initially
zero, later at most one or two per day—but the old vocabulary evidence should be
more trustworthy because validation is concentrated on actual due and risky
items rather than easy early collateral.
