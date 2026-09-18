# A longitudinal N-of-1 study of Arabic word learning in Alif

**Date:** 2026-07-30  
**Status:** Research protocol and analysis plan; no analysis results are claimed here  
**Population:** One learner, thousands of word-level observations, 100+ days  
**Primary objective:** Learn what predicts durable, unaided word recognition for this
learner, and convert the findings into safer scheduling, intake, sentence-selection,
morphology, and tashkīl decisions.

## Executive design

This dataset can support a serious study, but its strength is longitudinal depth rather
than population breadth. The defensible target is not “how people learn Arabic.” It is:

> Under the Alif learning environment, what exposure histories and presentation
> conditions have predicted durable recognition for this learner, and which prospective
> changes improve that outcome without increasing time or workload?

The analysis should use four nested evidence levels:

1. **Prospective randomized micro-experiments** for causal product decisions.
2. **Natural experiments**, especially the two-week break and sharp policy changes.
3. **Self-controlled longitudinal comparisons** within the same word and stable policy
   epoch.
4. **Descriptive associations** for discovery, diagnosis, and hypothesis generation.

The core historical model will predict the result of the **next genuine retrieval
opportunity** from everything known *before* that opportunity: elapsed gap, number and
distribution of prior encounters, distinct study days and sessions, sentence/context
diversity, form diversity, word properties, sentence load, session position, recent
failures, and algorithm epoch. The principal outcome will be **clean unaided
recognition**: product rating 3 or 4, with no yellow/confusion mark. Scheduler recall
(`rating >= 2`) will be reported separately because it answers a different question.

The study will not pretend that all review rows are independent. Words, sentences,
sessions, and days create repeated correlated observations. Models and uncertainty
estimates must account for these clusters, serial dependence, and the fact that the
algorithm preferentially shows difficult or due items.

The result should be a research-paper-style report plus a shorter blog narrative. The
most valuable deliverable is not one global coefficient; it is a set of decision curves:

- retention as a function of demonstrated gap;
- the marginal value of another repetition versus another study day;
- the cost of mass introduction;
- the effect of novel contexts and novel surface forms;
- when sentence length/load becomes harmful;
- how much of the post-break loss was predictable;
- which word classes are systematically fragile;
- and, prospectively, whether fading tashkīl improves unvocalized reading without
  damaging retention.

## 1. What can and cannot be learned

### 1.1 Strongly learnable for this learner

- The learner's empirical forgetting curve, with heterogeneity by word and learning
  phase.
- How retention changes with the count, spacing, and distribution of prior exposures.
- Whether five repetitions across five days are more valuable than five repetitions
  across one or two sessions.
- Whether varied sentences and varied inflected forms predict transfer to a new context.
- Which word, form, sentence, session, and calendar conditions precede failure.
- How a two-week interruption affected first retrieval and how recovery unfolded.
- Whether large admission cohorts create first-review delay, lower acquisition success,
  suspension, or later retention problems.
- How much bias is introduced by conditioning on the scheduler's primary/collateral
  label, even though every displayed word is equally valid learning evidence.
- How accurately FSRS probabilities describe actual recall within clean policy cohorts.
- Which prospective algorithm changes causally improve the learner's outcomes.

### 1.2 Only cautiously learnable

- The effect of a historical algorithm version. Algorithm changes coincide with time,
  learner growth, vocabulary composition, content changes, and workload.
- The effect of sentence length or difficulty. The selector chose sentences based on
  word state and available inventory, so easy and hard sentences were not assigned at
  random.
- The effect of introduction batch size. Large batches often came from deliberate book
  imports and different vocabulary sources.
- The historical effect of faded tashkīl. Actual display state was not recorded until
  the token-evidence protocol began on 2026-07-27.

These can yield useful within-learner associations and motivate experiments, but should
not be described as settled causal effects.

### 1.3 Not learnable from this dataset alone

- Population-average effects or claims about other learners.
- The outcome of words never presented.
- The exact reason a historical selector chose an item when the full candidate set and
  scores were not logged.
- Actual historical tashkīl visibility when no presentation snapshot exists.
- True active reading time for old reviews whose timer continued while the phone was
  backgrounded.
- Counterfactual retention under a policy that was never run, without additional
  assumptions or a prospective experiment.

## 2. Research questions and pre-specified hypotheses

The confirmatory report should designate a small number of primary hypotheses and treat
the rest as exploratory. This prevents thousands of rows and dozens of features from
producing a persuasive but accidental story.

| ID | Question | Pre-specified hypothesis | Primary estimand |
|---|---|---|---|
| H1 | How does recall decay with time? | Clean recall falls nonlinearly with demonstrated gap, with substantial word-level heterogeneity. | Change in probability of clean recall across gap bands and a continuous gap curve. |
| H2 | Does distributed practice matter beyond repetition count? | At a fixed number of prior successful exposures, more distinct study days and sessions predict better delayed recall. | Difference in next clean recall at a fixed landmark exposure count and test horizon. |
| H3 | Does contextual diversity create transfer? | More distinct sentences before a delayed test improve recall in a new sentence more than repeated exposure to the same sentence. | Difference in new-context recall for histories with equal exposure count but different context diversity. |
| H4 | Does surface-form diversity help or hurt? | Form diversity initially raises difficulty but improves later recognition of a new inflection or derivation. | Immediate cost and delayed transfer benefit of novel-form exposure. |
| H5 | What is the cost of bulk introduction? | Larger same-day admission cohorts increase time to first retrieval and lower durable acquisition, conditional on source and word difficulty. | Change in first-review latency, acquisition success, and ≥7-day recall per cohort-size band. |
| H6 | What happened during the break? | First post-break recall is predictable from pre-break spacing, stability, recent success, and word class; distributed pre-break histories are more resilient. | Word-level first-post-break recall and recovery-time event study. |
| H7 | When do sentences become too demanding? | Unknown density, multiple fragile words, unfamiliar forms, and excessive length slow responses and reduce clean recall; raw length alone has a smaller effect. | Within-word change in recall and active time across sentence-load values. |
| H8 | Do sessions show fatigue or warm-up? | Performance improves over the first few cards, then degrades with accumulated cards/time, especially in bursty sessions. | Within-session position curve with session fixed effects. |
| H9 | Which word properties predict fragility? | Lower frequency, longer forms, derivational competition, dense orthographic neighborhoods, and some morphological classes require more distributed evidence. | Partial effect on acquisition time, forgetting rate, and repeated-confusion risk. |
| H10 | Does faded tashkīl improve independent reading? | Prospectively, controlled fading increases unvocalized recognition/reduces reveal dependence without materially reducing delayed clean recall. | Randomized intention-to-treat effect on clean recall, reveal rate, and latency. |

Secondary questions:

- Are function/content classification, canonicalization, and variant credit masking
  meaningful form-specific failures?
- How much evidence would be discarded, and how would estimates change, if analysis
  incorrectly conditioned on the scheduler's primary-word label?
- Are repetitions within minutes mostly working-memory rehearsal, or do they contribute
  to ≥1-day retention?
- Does sentence-source authenticity (`book`/`corpus` versus generated material) improve
  transfer after matching on word, length, and load?
- Do connected maintenance passages improve words maintained per minute without reducing
  delayed retention?
- Which captured confusions recur: same root, same pattern, near-rasm, phonetic, semantic,
  or inflectional?
- Does time of day matter after controlling for session and word difficulty?
- Is response latency predictive of the next failure even when the current rating is
  correct?
- Does a two-week gap merely reveal weak traces, or permanently alter later trajectories?

## 3. Units of analysis

The study needs separate tables at separate grains. Collapsing them into one “reviews”
table will create double counting and ambiguous denominators.

### 3.1 Token-presentation table

One row per displayed sentence token, available prospectively from
`word_review_evidence`.

Key fields:

- `client_review_id`, `sentence_word_id`, position;
- original and canonical lemma IDs;
- exact surface and rendered front form;
- initial/ever/at-answer tashkīl visibility;
- reveal and toggle behavior;
- token rating and rating-2 causes;
- sentence, session, and timestamp.

This is the gold table for form and tashkīl questions, but begins only on 2026-07-27.
Multiple occurrences of one canonical lemma in a sentence remain multiple token rows.

### 3.2 Canonical word-opportunity table

One row per canonical lemma credited by one sentence review, based primarily on
`review_log`. This is the principal historical modeling table.

It must contain:

- event and client review identity;
- timestamp, session, sentence, mode;
- product rating, `was_confused`, acquisition flag, primary/collateral credit;
- pre-review knowledge state and FSRS card snapshot;
- scheduler-applied rating/policy fields where available;
- demonstrated gap features derived only from prior events;
- cumulative prior exposure summaries;
- sentence and word covariates;
- policy epoch and evidence-quality flags.

When multiple physical rows map to one canonical lemma in one client review, preserve
the physical rows for audit but define a deterministic canonical aggregation rule for
modeling. The aggregation must never turn one sentence into several independent tests
of the same word.

### 3.3 Sentence-card table

One row per answered sentence or passage card, from `sentence_review_log` reconciled
with `sentence_review` interactions.

Fields include:

- sentence IDs and card type;
- sentence comprehension;
- response time, lookup count, audio plays;
- number of credited words, primary/collateral composition;
- token count, content-word count, fragile/due/unknown composition at that time;
- card position and cumulative session effort;
- completion/abandonment context.

### 3.4 Acquisition-episode table

One row per reconstructed acquisition or leech-reintroduction episode:

- episode start and reason;
- word source and cohort/admission batch;
- every acquisition outcome in order;
- distinct sessions and days;
- inter-exposure gaps;
- graduation route and policy version;
- suspension, censoring, or still-acquiring outcome;
- first post-graduation tests at ≥1, ≥3, ≥7, ≥14, and ≥30 days.

Historical nullable `acquisition_episode_kind` must remain “unknown/legacy”; it must not
be silently backfilled.

### 3.5 Session and day tables

Session table:

- planned and shown cards;
- answered cards;
- start/end/duration;
- time of day;
- completion or abandonment;
- intro/retry/wrap-up burden;
- within-session failures and repetitions.

Day table:

- active minutes where estimable;
- sessions and cards;
- word outcomes;
- introductions, graduations, lapses;
- due-stock checkpoints where available;
- burstiness and inter-session spacing;
- policy epoch and special events such as bulk imports or the vacation.

### 3.6 Word table

Static or slowly changing attributes:

- canonical identity and variant family;
- frequency-core rank and source evidence;
- part of speech, root, wazn, CEFR, register, dialect;
- citation length and diacritic-independent length;
- root-family size already known at first exposure;
- number and type of stored forms;
- orthographic-neighborhood size and nearest-neighbor distance;
- homograph/variant ambiguity;
- content source and introduction provenance;
- confusion-pair features.

Never use the current final `user_lemma_knowledge` state as a predictor of a historical
outcome. That leaks future information.

## 4. Outcome definitions

No report should use the word “accuracy” without naming the outcome definition.

### 4.1 Primary learning outcome: clean unaided recognition

`rating >= 3 AND was_confused = false`

For protocol-v1 token rows, optionally require:

- no reveal-dependent rating cause;
- no `mixed_up`, `unfamiliar_form`, or `missing_tashkeel` cause;
- front condition explicitly known.

This is the best operational definition of “I recognized this word in context without
help.”

### 4.2 Secondary outcomes

- **Strict product success:** `rating >= 3`, regardless of confusion metadata.
- **FSRS recall:** scheduler semantics, normally `rating >= 2`; never substitute this
  for strict learning success.
- **Failure type:** red/Again, yellow/assisted recognition, mixed-up, unfamiliar form,
  missing tashkīl, retrieval lapse.
- **Sentence comprehension:** understood / partial / no idea.
- **Latency:** active milliseconds per content token where valid; otherwise censored or
  robustly clipped, never interpreted from the raw mean.
- **Behavioral assistance:** lookup, translation reveal, audio, tashkīl reveal/toggle.
- **Acquisition efficiency:** successful delayed outcome per presentation, session, day,
  and estimated minute—not graduation count alone.
- **Durability:** first qualifying clean recall after ≥1/3/7/14/30 days.
- **Transfer:** clean recall in a different sentence and/or a previously unseen surface
  form.
- **Failure recurrence:** another red/yellow for the same canonical word or exact form
  within a fixed horizon.
- **Workload/engagement guardrails:** cards per session, active time, session completion,
  abandonment, and subsequent-day return.

### 4.3 Demonstrated gap

Every retention analysis must state which prior event anchors the gap:

1. prior clean sentence-word retrieval;
2. prior sentence-word exposure of any outcome;
3. prior exact-form retrieval;
4. prior instrumented unassisted exact-form retrieval, where display metadata exist.

The scheduler's primary/collateral label never determines whether an appearance resets
the clock: once a sentence is displayed, every rated content word is an exposure.
Clean-outcome and exact-form anchors answer different questions. Same-session and quiz
events should be included or excluded according to the explicit estimand, not according
to scheduling provenance.

## 5. Exposure-history features

All features for event \(t\) must be calculated from events strictly before \(t\).

### 5.1 Quantity

- total prior sentence exposures;
- successful, yellow, and failed prior outcomes;
- prior primary and collateral labels as scheduling-provenance diagnostics only;
- prior acquisition versus FSRS exposures;
- prior passive/listening exposures, reported separately;
- prior exact-surface exposures.

### 5.2 Distribution

- distinct prior UTC days;
- distinct prior sessions;
- elapsed acquisition age;
- mean, median, minimum, and maximum prior gap;
- recency-weighted exposure count;
- burstiness: fraction of exposures occurring within the same hour/day;
- expanding-spacing index;
- lag since the most recent clean retrieval.

The central spacing comparison should condition on total exposure count. Otherwise “more
spaced” simply means “older and more practiced.”

### 5.3 Diversity and transfer

- distinct sentence IDs;
- proportion of exposures in the modal sentence;
- semantic/context diversity from sentence-level lemma-set distance;
- sentence-source diversity;
- exact-surface diversity;
- morphology-category diversity;
- whether the current sentence or surface is new for the word;
- whether the current inflection was previously seen only collaterally.

For semantic embeddings, use a frozen, versioned representation and confirm the result
with an embedding-free measure such as lemma-set Jaccard distance. Do not let an opaque
embedding be the only evidence.

### 5.4 Difficulty and interference

- word frequency and length;
- root and pattern family size;
- count of known near-neighbors;
- edit/rasm/phonetic distance to the nearest known word;
- captured-confusion history;
- surface-to-citation edit and morphology category;
- number of other fragile words in the sentence;
- number of due words sharing the card;
- sentence token count, content-token count, dependency/grammar proxies;
- token position;
- target versus collateral status.

### 5.5 Learner and workload state

- card position in session;
- cumulative answered cards and estimated active minutes;
- failures in the preceding 3/10 cards;
- sessions already completed that day;
- time since previous session;
- UTC and Oslo local time;
- day of week;
- daily backlog and recent workload;
- pre/post-break and recovery phase;
- policy epoch.

## 6. Data acquisition, freezing, and audit

The first analysis phase is a reproducibility exercise, not a model.

### 6.1 Freeze exact inputs

1. Take a consistent production SQLite online backup.
2. Copy all relevant interaction logs without modifying production.
3. Record byte size and SHA-256 of the database and every log.
4. Record the exact application commit, deployed revision, Alembic revision, FSRS
   package version, parameter hash, desired retention, and timezone.
5. Open analysis inputs only with SQLite `mode=ro&immutable=1`,
   `PRAGMA query_only=ON`.
6. Reject snapshots with WAL/journal sidecars in strict mode.
7. Use explicit half-open UTC windows.
8. Hash inputs before and after the run and require byte-identical outputs across two
   runs.

The checked-in `backend/data/alif.prod.db` is only an old local snapshot ending
2026-05-12 and must not be treated as the current study database.

### 6.2 Reconcile the event streams

Produce a data-quality report before substantive analysis:

- database review counts by day, mode, phase, credit type, and rating;
- interaction counts by day and event;
- join rates by `client_review_id`, session, sentence, and timestamp;
- duplicate and null client IDs;
- interaction-only and database-only submissions;
- sync-path versus direct-path coverage;
- timestamps outside plausible order;
- reviews created after offline delay;
- undo behavior and deleted rows;
- word-evidence saved count versus submitted count;
- orphaned word evidence;
- sentence mappings that changed after historical reviews;
- invalid/inert/function/proper-name rows;
- canonical-chain cycles or ambiguous roots;
- response-time outliers and backgrounded-card signatures;
- missing days/log files and checksum coverage.

Every derived row gets an `evidence_grade`:

- **A:** immutable per-token snapshot plus canonical review;
- **B:** canonical review plus stable sentence mapping and interaction;
- **C:** canonical review only or historically reconstructed presentation;
- **D:** uncertain mapping/epoch/presentation; descriptive only or excluded.

### 6.3 Build a policy-epoch ledger

Git commit time is not deployment time. Construct a table using deployment records,
CHANGELOG, experiment log, service start time, and production telemetry:

| Field | Examples |
|---|---|
| Epoch start/end | Exact UTC when policy was live |
| Scheduler | FSRS version, parameters, desired retention, rating translation |
| Acquisition | Boxes, due intervals, graduation routes, success gates |
| Selection | session size, intro reserves, repetitions, due/scaffold scoring |
| Credit | primary/collateral semantics, auto-skip behavior |
| Presentation | intro cards, passages, retries, wrap-ups, tashkīl mode |
| Content | sentence gate, corpus/source changes |
| Instrumentation | fields available and their semantics |
| Confidence | verified deployment / inferred date / unknown |

Analyses should:

- include epoch effects;
- repeat core estimates within long stable epochs;
- exclude a short deployment-transition buffer;
- never compare rating 2 across epochs without accounting for its changed scheduler
  treatment;
- label historical rows whose scheduler parameters cannot be recovered.

## 7. Analysis program

### Analysis 1: Descriptive longitudinal atlas

Purpose: establish what happened before explaining why.

Produce:

- daily and weekly cards, sessions, active-time estimates, introductions, graduations,
  lapses, and suspensions;
- state stocks and flows, with explicit identities where possible;
- outcome rates by mode, phase, credit, and policy epoch;
- distribution of gap, exposure count, distinct days, contexts, and forms;
- introduction cohort sizes and first-review latency;
- session size, completion, and within-day burstiness;
- sentence length/load and source mix;
- missingness and instrumentation coverage over time.

Key visualization: a calendar heatmap plus a policy timeline, with the vacation and bulk
imports annotated. A second “subway map” should follow a stratified sample of words from
first encounter through acquisition, graduation, lapses, and recovery.

No inferential claim should be made until the descriptive counts reconcile.

### Analysis 2: Empirical forgetting curves

Dataset: all genuine sentence-word reading retrievals, with separate curves for:

- clean post-acquisition cards;
- acquisition and relearning;
- exact same form versus different form;
- stable policy epochs;
- pre-break, first-post-break, and recovered periods.

Model:

```text
logit(P(clean recall at event t)) =
  smooth(log demonstrated gap) +
  phase/state +
  lateness +
  prior failures +
  policy epoch +
  word effect +
  day/session dependence
```

Use a hierarchical logistic generalized additive model or a Bayesian equivalent.
Allow word-specific intercepts and, where data support it, word-specific gap slopes.
Report marginal recall curves and intervals, not just odds ratios.

Sensitivity analyses:

- clean-outcome anchor versus any-outcome exposure anchor;
- quantify the exclusion cost and estimate distortion from an intentionally
  primary-only diagnostic, without treating it as the preferred analysis;
- exclude same-session events;
- exclude quiz/checkpoint/wrap-up/retry events;
- only evidence grades A/B;
- clean post-acquisition origin;
- on-time versus overdue;
- fixed effects for frequently observed words;
- day-block and word-cluster bootstrap.

The output is this learner's observed forgetting curve under delivered Alif practice,
not an innate memory constant.

### Analysis 3: Repetitions versus distributed study

This is the central pedagogical analysis.

Use landmark cohorts. After the \(k\)-th qualifying exposure (for example k = 2, 3, 5,
8), predict the first genuine test after a pre-specified horizon (≥1, ≥3, ≥7, or ≥14
days). At each landmark compare histories with:

- the same total exposure count;
- different numbers of distinct sessions/days;
- different minimum gaps;
- different burstiness;
- similar word difficulty, source, phase, and policy epoch.

Primary contrasts:

- 5 exposures on 1 day versus 5 across ≥3 days;
- 5 exposures in 1–2 sessions versus ≥4 sessions;
- an additional same-day repetition versus an additional later-day retrieval;
- expanding gaps versus massed gaps.

Avoid conditioning on a future graduation or eventual success; that creates survivor
bias. Use all landmark-eligible words and treat absent follow-up as non-delivery, not
failure. Report both:

1. probability that a delayed test is delivered;
2. clean recall conditional on delivery.

If enough overlap exists, use propensity weighting or matching based only on pre-landmark
history. Check overlap explicitly; do not extrapolate into unsupported histories.

### Analysis 4: Acquisition as a multi-state process

Reconstruct state transitions:

```text
encountered → acquiring box 1 → box 2 → box 3 → graduated
                              ↘ suspended
graduated/known → lapsed → relearning or leech reintroduction
```

Use multi-state survival analysis with competing risks:

- time to first review;
- time to first clean success;
- time to graduation;
- time to suspension;
- first clean follow-up after graduation;
- lapse within 30 days.

Predictors:

- admission cohort size;
- source and frequency;
- distinct days/sessions;
- early success sequence;
- early response latency;
- sentence/context diversity;
- surface diversity;
- word morphology and neighborhood;
- policy epoch.

Report total presentations and learner time as costs. A faster graduation route is not
better if it merely moves weak traces into FSRS and produces more later lapses.

### Analysis 5: Bulk-introduction cohorts

Define every introduction day/batch, including manual book imports. Plot:

- cohort size;
- source composition;
- time-to-first-review distribution;
- share never reviewed within 1/3/7 days;
- acquisition clean-recall curve;
- graduation and suspension;
- first ≥7-day post-graduation recall;
- downstream Box-1/Box-2/FSRS debt;
- workload displacement from pre-existing words.

Historical model:

```text
outcome ~ smooth(cohort size) + source + frequency + word difficulty +
          pre-existing backlog + daily study volume + policy epoch
```

This remains partly confounded. Use the result to choose safe prospective batch-size
arms, not to assert a universal threshold. A future stepped randomized admission queue
could compare small daily tranches (for example 5 versus 10, only when recovery gates
permit) while holding the approved vocabulary queue fixed.

### Analysis 6: Contextual diversity and transfer

For each word opportunity, classify the current presentation as:

- repeated sentence;
- new sentence with previously seen surface;
- new surface in familiar sentence;
- new sentence and new surface.

Match or model within canonical word, prior exposure count, gap, phase, and epoch.

Questions:

- Is repeating the same sentence efficient for early encoding?
- At what point does repetition stop helping?
- Does prior context diversity improve first performance in a new sentence?
- Does prior form diversity improve recognition of a new inflection?
- Is authentic book/corpus transfer different from generated-sentence transfer?

Define a context-concentration index such as:

```text
1 - (exposures in the modal sentence / all prior exposures)
```

and complement it with distinct contexts and lemma-set distance. The delayed endpoint,
not the immediate novel-context penalty, determines whether diversity helped learning.

### Analysis 7: Sentence cognitive load

Reconstruct sentence load at the moment of review, not from current final word states:

- surface/content token count;
- number and proportion already cleanly known before the card;
- acquiring/lapsed/due words;
- unfamiliar forms;
- number of simultaneous misses/yellows;
- grammatical-feature count;
- passage versus single sentence;
- target position and number of due targets.

Use within-word and within-session comparisons where possible:

```text
clean recall / log active time ~
  token count + fragile-word count + unknown density + unfamiliar-form count +
  interactions with acquisition phase + word + session + epoch
```

Use splines to find thresholds rather than assuming linear effects. Compare models with
raw sentence length alone against richer load measures. The practical output is a
frontier: combinations of length and fragile-word density that preserve recall and
reasonable reading time.

### Analysis 8: Sessions, spacing within a day, and fatigue

Within-session position is less confounded than comparisons across sessions because
the learner and day are held nearly constant.

Model clean recall and latency against:

- planned and actual card position;
- cumulative cards;
- cumulative estimated active time;
- preceding failures;
- intro/retry/wrap-up burden;
- session time of day;
- gap since the previous session.

Use session fixed effects and flexible position curves. Separate:

- warm-up (first cards);
- steady state;
- late-session fatigue;
- sessions interrupted/backgrounded;
- planned versus appended retry/wrap-up cards.

For daily distribution, compare equal-volume days where work is concentrated in one
burst versus multiple sessions. Because the learner chooses when to study, call this
associational unless prospectively randomized.

### Analysis 9: The two-week break as a natural experiment

The break is an unusually valuable gap intervention, but not a randomized vacation.

#### Word-level first-post-break cohort

Include words with a qualifying pre-break history and observe their first genuine
post-break reading opportunity. Predict clean recall from:

- exact break gap;
- pre-break stability/retrievability;
- last pre-break outcome and latency;
- total exposures;
- distinct days/sessions;
- context and form diversity;
- acquisition/FSRS/relearning state;
- frequency, morphology, and confusion history.

Primary question: which pre-break learning histories were most resilient at the same
gap?

#### Matched non-break gaps

Compare break-spanning gaps with ordinary gaps of similar duration, state, and word
difficulty elsewhere in the record. This tests whether the break effect is mostly
elapsed time or whether interruption/recovery context adds an effect.

#### Recovery event study

Plot days relative to return:

- first-test delivery;
- clean sentence-word recall;
- sentence comprehension and latency;
- due stocks;
- repeated failures;
- graduations and re-suspensions;
- session volume and distribution.

Use segmented time-series models with autocorrelated errors, but treat the single
interruption as a case study. The word-level sample gives precision about which words
failed; it does not create multiple independent vacations.

### Analysis 10: Word-level difficulty and Arabic-specific structure

Fit word-specific forgetting/difficulty estimates, then explain their variation with:

- frequency-core rank and source breadth;
- POS;
- root and wazn;
- word/form length;
- number of forms;
- root-family familiarity at introduction;
- derived versus citation form;
- broken plural, maṣdar, participle, verb form, enclitic, and proclitic categories;
- orthographic/rasm/phonetic neighborhood;
- known confusable competitors;
- source/register.

Use partial pooling. Do not publish raw word rankings for words with only a few
observations as if they were stable traits.

Two valuable derived outputs:

1. **Fragility score:** expected delayed failure after controlling for delivered gap and
   policy.
2. **Interference graph:** nodes are lemmas; edges are captured or inferred confusions,
   weighted by recurrence and mechanism.

Case studies should show several representative trajectories:

- easy/high-frequency durable word;
- slow but eventually durable word;
- repeated morphology failure;
- visual confusable;
- leech/reintroduction;
- break-resilient and break-fragile words with comparable histories.

### Analysis 11: Scheduling-provenance bias diagnostic

Primary/collateral status describes why the selector chose a sentence, not what the
learner experienced after it appeared. Historical pooled accuracy may nevertheless
differ because primary words were selected under different difficulty and due-state
rules. The purpose of this analysis is to measure that selection bias, never to decide
whether a word-level observation counts.

Questions:

- What fraction of sentence-word outcomes and delayed tests would a primary-only rule
  discard?
- How do forgetting, spacing, form, break, and session estimates change under that
  artificial restriction?
- After controlling for word history, predicted recall, lateness, sentence load, and
  epoch, how strongly does scheduling provenance still predict the current outcome?
- Are mixed clean/failure judgments common within one displayed sentence, confirming
  that collateral rows contain distinct word-level information rather than an inherited
  sentence-wide rating?

The default history and every headline analysis include all displayed word outcomes.
Primary/acquisition flags may enter as nuisance controls for historical selection.
Scheduling policy may use those labels prospectively, but memory evidence is never
discounted or deleted because a word was collateral.

### Analysis 12: Response time as signal and outcome

Raw `response_ms` contains idle/background time. Use:

- medians and quantiles;
- log time;
- hard censoring/winsorization justified from the empirical distribution;
- interaction/card-show timestamps to estimate active intervals when available;
- separate passage and sentence thresholds;
- exclusion of backgrounded cards.

Test whether slow-but-correct recognition predicts the next failure after controlling
for word, gap, and sentence load. If so, latency can be an early fragility signal without
changing the learner's explicit rating.

Report speed as milliseconds per content token and per successfully maintained word.
Never use the raw arithmetic mean as the headline.

### Analysis 13: Tashkīl

#### Historical phase

Before 2026-07-27, actual token visibility is missing. Split historical rows into:

- definitely vocalized by policy;
- possibly faded according to reconstructable default policy;
- unknown actual presentation.

The “possibly faded” group is not a treatment group. Stability determined fading, so it
is strongly confounded with word strength. Use historical data only to describe coverage
and generate hypotheses.

#### Prospective observational phase

Use `word_review_evidence` to model:

- initial vocalized versus unvocalized display;
- front reveal and reveal-then-hide;
- exact form and morphology;
- clean recall, latency, and rating-2 causes;
- next delayed outcome.

Always condition on the assignment policy and pre-card strength. Displaying no tashkīl
to strong words will otherwise create a misleading raw advantage.

#### Randomized micro-experiment

Among words inside a safe stability band, randomize initial token display within
pre-specified strata:

- canonical lemma strength;
- morphology/form class;
- sentence load;
- recent reveal history.

Prefer lemma-day or session-level assignment over independent token flips if immediate
carryover is likely. Record assignment even when the card is never delivered.

Primary endpoint:

- clean unaided recognition on a later unvocalized opportunity.

Safety/secondary endpoints:

- immediate clean recall;
- front reveal;
- latency;
- sentence comprehension;
- red/yellow recurrence;
- no increase in cards or workload.

Use intention-to-treat. Predefine a non-inferiority margin for retention and a minimum
useful improvement in unvocalized performance before starting.

### Analysis 14: Calibration and model comparison

For reviews with complete `pre_card` snapshots:

- reproduce predicted retrievability at actual review time;
- report calibration for both strict success and FSRS recall;
- stratify by origin, state, lateness, epoch, primary/collateral, morphology, and
  post-graduation status;
- use reliability curves, calibration intercept/slope, and Brier score.

Compare:

1. current FSRS prediction;
2. a learner-specific forgetting model;
3. FSRS plus Alif-specific features such as form novelty, recent latency, and sentence
   load.

Use forward-chaining evaluation by date. Never evaluate on random row splits, which leak
later observations of the same words into training. A more complex model is useful only
if it improves future calibration and decision utility, not merely in-sample fit.

## 8. Statistical framework

### 8.1 Primary model family

Use hierarchical logistic models for binary recall, with:

- word random intercepts;
- selected word-specific gap slopes;
- session/day random effects or fixed effects for within-session analyses;
- flexible splines for gap, exposure count, and sentence load;
- policy-epoch effects and key interactions;
- robust uncertainty checked by resampling whole words and whole calendar blocks.

For time-to-event outcomes, use multi-state survival or discrete-time hazard models.
For response time, use log-normal/quantile models with censoring.

### 8.2 Effective sample size

Thousands of rows do not imply thousands of independent experimental units.

Report:

- number of physical review rows;
- distinct canonical words;
- distinct sentences;
- distinct sessions;
- distinct active days;
- number of policy epochs;
- number of independent break/intervention events.

Confidence intervals should be checked with:

- word-cluster bootstrap;
- 7-day or 14-day calendar-block bootstrap;
- a two-way resampling sensitivity analysis;
- leave-one-high-volume-day-out;
- leave-one-policy-epoch-out where feasible.

### 8.3 Multiple comparisons

Pre-register:

- primary outcome;
- primary hypotheses H1–H10;
- exact model formulas and major exclusions;
- the small number of decisive contrasts.

For exploratory word classes and interactions, emphasize effect sizes and false-discovery
control. Confirm surprising discoveries on a later time window or with a prospective
experiment.

### 8.4 Missingness and censoring

Separate:

- **no delivery**: no qualifying future opportunity;
- **right censoring**: insufficient elapsed time by cutoff;
- **missing presentation state**: exposure happened but condition unknown;
- **missing active time**: rating exists but latency is invalid;
- **mapping uncertainty**: historical token-to-lemma reconstruction is suspect;
- **log loss**: DB row exists but interaction record is absent.

Do not classify non-delivery as forgetting. Model delivery probability separately and
show how conditional-on-delivery retention may be selected by the scheduler.

### 8.5 Negative controls and falsification checks

- Future exposure count must not predict an earlier outcome after the landmark is fixed;
  if it does, the analysis leaks future information.
- Randomly permuted word identities should destroy word-property effects.
- A “fake break” at several ordinary dates should not routinely produce an effect as
  large as the real return.
- Current final knowledge state should dramatically overpredict historical outcomes;
  this demonstrates why it is forbidden as a historical predictor.
- Effects attributed to sentence length should persist after removing backgrounded
  response-time outliers and controlling for fragile-word density.
- Any policy-epoch effect should be tested with leads; improvement beginning before
  deployment suggests time trend/confounding.

## 9. Algorithm change and causal interpretation

Every result receives one of four labels:

| Label | Meaning |
|---|---|
| A — randomized | Prospective assignment with logged eligibility, assignment, delivery, and outcome. |
| B — quasi-experimental | Break, threshold, or abrupt policy change with credible comparison and diagnostics. |
| C — self-controlled association | Within-word/session comparison with rich pre-outcome adjustment. |
| D — descriptive | Useful pattern with substantial selection or missing-condition uncertainty. |

Rules:

- Never say a selector “caused” an outcome from a pooled before/after chart.
- Candidate selection is part of the data-generating process, not noise.
- Use algorithm epochs to avoid mixing incompatible rating, graduation, and selection
  semantics.
- If the historical candidate set cannot be reconstructed, do not claim an unbiased
  treatment propensity.
- When a policy changed because the learner was struggling, ordinary interrupted
  time-series estimates are biased toward finding the new policy ineffective.
- Favor prospective randomization for reversible display, ordering, and representation
  choices.

## 10. Prospective instrumentation needed

The current data is excellent on outcomes but historically weaker on assignment. Add
append-only telemetry for future research:

1. **Policy snapshot on every review**
   - app commit/policy version;
   - FSRS version, parameters, desired retention;
   - acquisition/graduation policy;
   - rating translation.
2. **Presentation snapshot**
   - immutable sentence text/hash and token mapping;
   - card type and exact form;
   - predicted sentence load at selection;
   - initial and revealed tashkīl;
   - translation/audio/lookup behavior.
3. **Selection record**
   - eligible candidate-set hash and size;
   - chosen item, rank, score components;
   - randomized assignment and arm;
   - reason for non-delivery/skip.
4. **Timing**
   - card shown, foreground/background, answer revealed, submitted;
   - active rather than wall-clock time.
5. **Acquisition batch identity**
   - queue/import ID;
   - approval date;
   - admission date;
   - batch/tranche and policy.
6. **Outcome semantics**
   - product rating;
   - scheduler-applied rating;
   - exact failure causes;
   - whether a result may advance acquisition/graduation.

The candidate-set log need not store every large object. A versioned hash plus top-K
candidates, their feature vectors, and aggregate eligibility counts may be enough for
audit and propensity reconstruction.

## 11. Planned figures and tables

### Main figures

1. **Study timeline:** daily workload, outcomes, policy epochs, imports, and vacation.
2. **Word trajectories:** representative event ribbons from first exposure to durable
   recall/lapse.
3. **Forgetting curve:** clean recall by demonstrated gap with word heterogeneity.
4. **Spacing surface:** delayed recall by exposure count × distinct study days.
5. **Context transfer:** same/new sentence × same/new surface.
6. **Acquisition cohorts:** admission size, first-review latency, graduation, suspension.
7. **Break resilience:** observed versus predicted first-post-break recall and recovery
   event study.
8. **Sentence load frontier:** recall and active time by length × fragile-word density.
9. **Session dynamics:** warm-up/fatigue curve and effect of accumulated effort.
10. **Arabic fragility map:** morphology classes and confusion network.
11. **Calibration:** predicted versus observed strict and FSRS recall by clean epoch.
12. **Tashkīl experiment:** assignment, delivery, reveal, immediate and delayed outcomes.

### Main tables

- input provenance and missingness by era;
- outcome definitions and denominators;
- policy-epoch ledger;
- primary hypothesis estimates with evidence grades;
- robustness/sensitivity matrix;
- algorithm decision table;
- top recurring word/form failure mechanisms, with minimum-observation thresholds.

## 12. From result to product decision

Every recommendation should identify the metric it is optimizing and its cost.

| Finding pattern | Candidate decision | Required evidence before change |
|---|---|---|
| Same-day repetitions add little ≥3-day retention | Reduce massed repeats or move them to later sessions/days | Landmark estimate plus prospective workload-neutral test |
| Distinct days strongly outperform raw count | Graduation requires at least one spaced success | Event-sourced replay, queue-cost estimate, prospective version stamp |
| Large batches delay first review and increase suspension | Stage approved imports through a capacity-aware queue | Source-adjusted cohort analysis and small prospective tranche test |
| New contexts improve later transfer | Increase context diversity after early encoding | Within-word delayed-transfer result; ensure no immediate overload |
| Unfamiliar forms drive yellow/red outcomes | Generate or select form-targeted contrast contexts | Token evidence and form-specific delayed outcome |
| Sentence load, not length, predicts failure | Gate/score on fragile density and form novelty | Stable within-word curve and inventory-impact simulation |
| Late-session fatigue is material | Shorten sessions or stop appending wrap-ups | Within-session fixed-effect result and completion guardrail |
| Break failures are predictable | Activate recovery ordering by predicted fragility | Backtest on break and prospective safe ordering experiment |
| Primary-only analysis materially changes estimates | Enforce all displayed-word history in analytics; retain role only as scheduling provenance | Reconciliation test plus mixed-outcome audit |
| Fading improves unvocalized transfer safely | Expand or personalize fade threshold | Randomized ITT, retention non-inferiority, reveal benefit |

No change should be recommended solely because it increases graduations, decreases a due
stock, or improves same-session success. The north-star endpoint is durable recognition
per sustainable learner minute, with authentic reading transfer as an external validity
check.

## 13. Execution phases

### Phase 0 — Protocol freeze

- Confirm the primary hypotheses, outcomes, break dates, and policy boundaries.
- Freeze the analysis plan before inspecting all subgroup results.
- Separate confirmatory and exploratory output directories.

### Phase 1 — Reproducible data foundation

- Capture and hash a fresh immutable production snapshot and logs.
- Build the epoch ledger.
- Reconcile DB, interaction, and token-evidence streams.
- Produce a missingness/integrity report.
- Materialize versioned token, word-opportunity, sentence-card, episode, session, day,
  and word tables.

Exit criterion: repeated runs are byte-identical and headline counts reconcile.

### Phase 2 — Descriptive paper

- Longitudinal atlas.
- Word-trajectory examples.
- Break and batch timelines.
- Outcome/missingness tables.

This phase alone can produce an honest and interesting blog post without causal
overreach.

### Phase 3 — Core historical models

- forgetting;
- count versus distributed days/sessions;
- acquisition multi-state model;
- context/form transfer;
- sentence load;
- session dynamics;
- word fragility;
- FSRS calibration.

Exit criterion: core estimates survive pre-specified sensitivity analyses and forward
validation.

### Phase 4 — Natural experiments and policy synthesis

- break resilience and recovery;
- stable policy-epoch analyses;
- bulk-intake cohorts;
- primary/collateral landmark analysis;
- linked decision table with evidence grades.

### Phase 5 — Prospective experiments

Prioritize reversible, workload-neutral experiments:

1. tashkīl presentation;
2. context/surface representation of already-due words;
3. within-session placement/re-exposure delay;
4. staged admission of an already-approved vocabulary queue;
5. only later, scheduling-credit or graduation changes.

Each experiment needs:

- eligibility and exclusion rules;
- deterministic/random assignment recorded before delivery;
- delivery and non-delivery;
- one primary endpoint;
- safety and workload guardrails;
- minimum sample/information rule;
- stopping and rollback rules;
- no retrospective state rewrite.

### Phase 6 — Final outputs

1. Full reproducible research report.
2. Short blog version centered on five or six robust findings.
3. Interactive word-trajectory explorer.
4. Machine-readable result tables and input manifest.
5. Product recommendation memo: adopt / test / observe / reject.
6. Follow-up measurement schedule for every deployed change.

## 14. Proposed implementation layout

Follow the repository's existing immutable-baseline conventions:

```text
backend/scripts/analyze_longitudinal_learning.py
backend/app/analysis/longitudinal/
    extract.py
    epochs.py
    event_history.py
    outcomes.py
    features.py
    forgetting.py
    acquisition.py
    context_transfer.py
    break_analysis.py
    sessions.py
    robustness.py
baselines/longitudinal-learning-<cutoff>/
    manifest.json
    data_quality.json
    policy_epochs.csv
    descriptive/
    confirmatory/
    exploratory/
    figures/
research/longitudinal-learning-report-<date>.md
```

Tests should cover:

- strict read-only behavior;
- hash verification;
- half-open window boundaries;
- no future-data leakage;
- canonical-chain resolution;
- same-client-review aggregation;
- undo/deletion behavior;
- acquisition episode reconstruction;
- gap-anchor definitions;
- missing follow-up versus failure;
- offline sync timestamp handling;
- stable deterministic outputs.

## 15. The likely publishable story

The most compelling eventual narrative is not “I did thousands of flashcards.” It is:

> A sentence-based Arabic system recorded the trajectory of every word across more than
> one hundred days. The data show which repetitions became durable knowledge, which were
> merely massed rehearsal, how context and morphology affected transfer, what a two-week
> interruption exposed, and how an adaptive algorithm can learn from one person's
> history without mistaking its own selection policy for a law of memory.

That story remains interesting even if several initial hypotheses fail. Negative results
such as “sentence length did not matter after fragile-word density,” “more contextual
diversity did not improve retention,” or “tashkīl fading increased difficulty without
transfer” would directly improve the product and be more credible than a collection of
uncontrolled correlations.

## 16. Minimum viable first pass

If the full program is too large for one pass, do these first:

1. Freeze and reconcile the data and policy epochs.
2. Build the canonical word-opportunity and acquisition-episode tables.
3. Produce the timeline and empirical forgetting curves.
4. Run the exposure-count versus distinct-days landmark analysis.
5. Analyze first-post-break retrieval and recovery.
6. Analyze introduction cohort size versus first-review latency and durable outcome.
7. Produce word/form fragility case studies.
8. Begin the prospective tashkīl experiment only after enough token-evidence baseline
   has accumulated.

These eight components are sufficient for a strong personal research report and are the
most likely to generate actionable algorithm insights without overclaiming.
