# Alif learning-system proposal

**Date:** 2026-07-25  
**Status:** Discussion and validation proposal only  
**Objective:** Maximize durable Arabic word learning and retention, subject to sustainable learner effort.  
**Authorization boundary:** This document does not authorize application, database, configuration, experiment, or deployment changes.

## 1. Executive conclusion

Alif is producing substantial learning. The apparently immovable review-debt headline is not evidence that study is futile or that more reviews do not help. It is the result of several stocks and flows being collapsed into one number:

- previously scheduled FSRS arrivals;
- acquisition words becoming newly scheduled FSRS cards;
- large deliberate imports entering Box 1;
- overdue words being served but not necessarily removed from the headline immediately;
- raw, main-lane, acquisition, and session-total debt being counted differently in different places.

The largest opportunities are not a single relearning tweak. In expected order of system-level leverage they are:

1. Make measurement coherent and reproducible.
2. Improve selector coverage and calibrated risk allocation.
3. Improve acquisition breadth, spacing, and the acquisition-to-FSRS handoff.
4. Recalibrate FSRS and overdue urgency on clean, modality-aware data.
5. Control large intake cohorts with a capacity preview.
6. Retain rapid retry after failure as a supporting mechanism, but make it persistent, capped, spacing-aware, and reversible.

The immediate recommendation is **not** to retune FSRS, relax graduation, or expand the new retry behavior. The immediate recommendation is to build the read-only measurement and replay foundation needed to test those changes honestly.

## 2. Binding learning and evidence principles

These are design invariants, not hypotheses.

### 2.1 Every reviewed sentence word is equally valid

Once a sentence is seen and answered, every eligible word in it is a valid word review. A word is not weaker evidence because it was collateral rather than the sentence-selection target.

`primary` is still useful for:

- explaining why the selector presented a sentence;
- counting answered sentence cards;
- attributing response time to the intended target;
- diagnosing selector performance;
- measuring independently answered card effort for the recovery intake-permission gate.

It must not be treated as intrinsically stronger retention or graduation evidence.
The recovery `0/8/30` intake-permission gate remains primary-reading-card-only:
collateral rows from one answered sentence cannot manufacture permission to add new
vocabulary. This is an effort/admission unit, not a claim that the primary word's
memory evidence is stronger. Primary-only retention or graduation metrics are not
authorized by this exception.

### 2.2 Debt is a capacity diagnostic, not the goal

The north-star is durable word knowledge, not minimum due count.

The primary learning outcome should be:

> More words successfully recognized in sentences after meaningful spacing, sustained across 7- and 30-day windows.

Efficiency should be reported alongside it:

> Retained word-days gained per learner minute, with sentences, word outcomes, and extra retrieval cards shown separately.

### 2.3 Immediate success is not durable retention

Same-session or sub-10-minute success may represent useful re-encoding, but it cannot by itself establish consolidation.

### 2.4 Graduation is a transition, not a learning outcome

An acquisition word leaving Box 1 or graduating into FSRS is not sufficient evidence of success. It must be followed to cold sentence outcomes after graduation.

### 2.5 A reversible experiment needs explicit versioning

Every candidate behavior must have:

- a configuration version;
- a reproducible baseline snapshot;
- explicit triggering and eligibility definitions;
- separate enable/disable controls;
- logged pre-state and post-state;
- a deterministic replay path;
- a documented rollback decision.

## 3. Evidence base from the read-only audit

All figures below came from repository history, current code, a production database
snapshot captured during the audit, and interaction logs inspected read-only on
2026-07-25. The immediate activity window was 2026-07-25 09:45–16:22 UTC.
Because offline synchronization can later insert reviews whose `reviewed_at` falls
inside an earlier window, reproduction requires the captured database hash and log
hashes, not merely a query against the later live database. WP0 makes those input
identities mandatory.

### 3.1 Current learning activity

In the audited study window:

- 600 valid word reviews were recorded;
- 428 distinct words were reviewed;
- 586 word reviews came from sentences;
- 100 were primary-target reviews;
- 486 were collateral reviews;
- 26 acquisition words graduated.

This is strong evidence that high-volume study reaches many distinct obligations and advances learning.

### 3.2 Acquisition pipeline

Current acquiring stock:

| Box | Words | Due | Important detail |
|---|---:|---:|---|
| Box 1 | 143 | 133 | 20 currently have zero correct reviews |
| Box 2 | 53 | 41 | Consolidation demand is also backlogged |
| Box 3 | 9 | 6 | Small stock but some very old words |
| **Total** | **205** | **180** | 115 of the total are `bookifier` source |

Current Box-1 age:

- median: 10.4 days;
- 90th percentile: 40.0 days;
- maximum: 59.8 days.

This is primarily a delivery and coverage bottleneck. Making graduation easier would reduce the visible Box-1 count but could push insufficiently consolidated words into FSRS.

### 3.3 Acquisition transition history

Across the last 120 days:

- 1,121 acquisition words graduated;
- median acquisition duration overall: 4.3 days;
- words with no failures: median 2.0 days;
- words with at least one failure: median 7.1 days.

In the last 30 days:

- median duration overall: 10.2 days;
- the failed cohort median was 10.4 days.

The recent slowdown is consistent with intake and delivery congestion.

Graduation-reason outcomes in July were small and censored, but directionally important:

| Graduation reason | Count | Median episode sessions | First delivered sentence success ≥1 d | First delivered sentence success ≥3 d |
|---|---:|---:|---:|---:|
| Elapsed interval | 104 | 3 | 77.6% of 49 | 78.6% of 42 |
| First correct | 31 | 1 | 81.8% of 22 | 72.2% of 18 |
| Perfect accuracy | 23 | 2 | 66.7% of 15 | 58.3% of 12 |
| High accuracy | 17 | 3 | 83.3% of 12 | 80.0% of 10 |
| Standard | 12 | 4 | 100% of 6 | 100% of 5 |

These are not causal comparisons. The low and censored sample sizes make them unsuitable for changing graduation rules. They do justify special scrutiny of same-session/perfect-accuracy graduation.
The reproducible audit also found two non-success graduations among all 2,106
stored events; the workspace now contains a narrow success gate, while broader
threshold changes remain deferred.

### 3.4 Failure history

Over 120 days there were 3,757 rating-1 sentence-word failures:

| Population | Failures |
|---|---:|
| Acquisition | 1,727 |
| Recent FSRS | 1,559 |
| FSRS after a prior gap ≥14 days | 471 |

Evidence-source split:

- collateral: 2,384;
- primary: 1,373.

The older-lapse population is large enough to merit a dedicated recovery policy. Its median pre-failure stability was 26.3 days.

Observational re-exposure outcomes:

| Failure population | Next exposure | Immediate next-review success | First later sentence success ≥12 h |
|---|---|---:|---:|
| Acquisition | <10 min | 87.3% of 502 | 57.6% of 469 |
| Acquisition | 10 min–2 h | 66.1% of 192 | 62.4% of 178 |
| Acquisition | 2–24 h | 62.8% of 363 | 58.4% of 356 |
| Old FSRS lapse | 10 min–2 h | 90.0% of 20 | 75.0% of 20 |
| Old FSRS lapse | 2–24 h | 85.0% of 133 | 85.4% of 130 |
| Old FSRS lapse | >3 d | 64.9% of 148 | 64.9% of 148 |

These observations support testing delayed re-exposure. They do not establish a causal effect because timing is confounded by word difficulty, session length, selection, user behavior, and repeated events.

### 3.5 FSRS calibration

For due sentence-reading outcomes from 2026-03-27 through 2026-07-25:

| Pre-review stability | Success |
|---|---:|
| <7 days | 77.9% of 5,253 |
| 7–30 days | 78.7% of 2,292 |
| ≥30 days | 76.9% of 805 |

For July alone:

| Pre-review stability | Success |
|---|---:|
| <7 days | 73.7% of 758 |
| 7–30 days | 70.4% of 378 |
| ≥30 days | 71.6% of 341 |

Therefore:

- stability ≥30 days must not be labeled harmless, solid, or intrinsically low urgency;
- overdue age and recent failure history matter;
- current predicted retrievability is materially more optimistic than observed due recall;
- changing desired retention or FSRS weights without a clean calibration study is unsafe.

A follow-up matched check computed current-library retrievability from every stored
`pre_card` at the **actual review timestamp**, so observed lateness was already included.
Its original “observed recall” column used Alif's strict learning-success
definition (`rating >= 3`):

| Pre-review stability | Mean predicted recall | Observed recall | Reviews | Median lateness |
|---|---:|---:|---:|---:|
| <7 days | 88.9% | 78.0% | 5,224 | 1.4 d |
| 7–30 days | 91.3% | 78.8% | 2,284 | 2.8 d |
| ≥30 days | 92.9% | 77.0% | 809 | 4.3 d |

This rules out lateness as the sole explanation for the gap. It does not remove the
algorithm-version caveat: the current retrievability function was applied to historical
stored states. These clean WP1 counts additionally exclude function/inert review rows,
which explains the small denominator difference from the first matched scratch query.
That is why this finding justifies honest urgency labels and a clean calibration
study—not immediate FSRS retuning.

The segmented WP6 audit subsequently corrected an important target mismatch.
FSRS retrievability is mathematically calibrated to “not Again”
(`rating >= 2`), while Alif strict success deliberately treats confused/Hard as
not learned. Across the same 8,317 matched reviews:

| Outcome definition | Predicted | Observed | Gap |
|---|---:|---:|---:|
| strict Alif success, rating ≥3 | 89.9% | 78.1% | -11.8 pp |
| FSRS recall, rating ≥2 | 89.9% | 82.9% | **-7.0 pp** |

The scheduler remains overconfident, but the earlier strict-success comparison
overstated mathematical FSRS miscalibration. The remaining FSRS gap is 4.1
points on post-acquisition cards versus 22.5 points on legacy/untracked-origin
cards, and 14.5 points in Relearning state. Even reviews within one day of due
miss FSRS recall by 5.6 points, so lateness is important but not sufficient.
These splits strengthen the case for population-specific replay rather than one
global retune.

### 3.6 Session behavior and wrap-up capacity

Across interaction logs from 2026-07-05 through 2026-07-25:

- 87 sessions had enough card data for approximate completion analysis;
- 27 reached and answered the final planned sentence;
- approximate completion rate: 31%;
- completed sessions had a median 17 distinct rating-1 words;
- abandoned fragments had a median 3 answered sentences and 2 distinct failed words.

Approximate completion means the final planned card was shown and a sentence review
was recorded at or after that display. Distinct rating-1 words are the union of
`sentence_review.word_ratings == 1` by `session_id`; therefore one `no_idea` answer
correctly contributes every eligible word in that sentence. A later independent rerun
found 28/90 completed and median 16 after additional sessions arrived. Production also
showed 10-card and 8-card automatic wrap-ups; the one-card events initially cited in a
counter-review were checkpoint fetches.

Retrying every failed word immediately at completion could approximately double card
workload in some completed sessions before endpoint exclusions. Treat this as a
workload upper bound to monitor, not evidence for a cap. The user-authorized
“retry every rating-1 failure” policy remains in force unless completion or retention
guardrails demonstrate harm.

The correct product principle is:

> Every rating-1 failure receives an eventual retry.

It does not follow that every retry must happen at the same session wrap-up.

## 4. System diagnosis

### 4.1 Measurement fragmentation

Current views mix:

- raw FSRS due;
- actionable main-lane FSRS due;
- acquisition due;
- total session demand;
- sampled slow-lane demand;
- daily maximum backlog snapshots;
- distinct due words served;
- words labeled “cleared” even after a failure;
- target-only accuracy and all-word scheduling.

This makes it possible for productive learning to look stagnant and for a misleading metric to drive algorithm changes.

### 4.2 Selector allocation

The documented “oldest due” opening block is implemented with frequency rank first and overdue age as a tie-break. It inspects only a small prefix and can underfill when those candidates are unavailable.

Consequences:

- fragile or long-neglected serviceable words are not guaranteed representation;
- the first protected slots do not reliably shrink the most learning-critical debt;
- unavailable candidates can waste reserved capacity;
- current scoring mixes several objectives without an empirically calibrated risk layer.

### 4.3 Acquisition delivery

Current Box-1 words target four within-session appearances, and acquisition repetition can add up to 15 cards beyond the normal session limit.

This is deliberate prior design: the four appearances are the current encoding
payload, and auto-skip was designed not to erase them. A0 therefore remains the
load-bearing baseline. The following are hypotheses to calculate and replay, not
presumed defects:

- massed repetition consumes breadth while many due Box-1 words wait;
- long sessions can convert repeated within-session successes into accuracy-tier graduation;
- cumulative `times_seen` and `times_correct` mix working-memory and spacing-eligible evidence;
- a large intake cohort can stay old and due even while a small subset receives repeated exposure.

### 4.4 FSRS and urgency

Current behavior uses library-default FSRS weights with desired retention 0.95.
The dependency previously allowed any `fsrs>=6.0.0`; historical rows did not
stamp library or parameter identity. The workspace now pins the currently
production-matched 6.3.1 and adds prospective config telemetry, without changing
scheduling intent. Existing optimizer inputs still include non-acquisition quiz
reviews as ordinary FSRS evidence and do not adequately model modality,
algorithm-version changes, or lateness.

Observed calibration is poor enough that:

- nominal stability cannot define urgency alone;
- new parameters should not be deployed from the current optimizer output;
- review timing behavior must not be mistaken for memory behavior;
- walk-forward validation is mandatory.

### 4.5 Intake

The 2026-07-15 and 2026-07-21 book-related imports were deliberate and valuable, but they entered an already constrained pipeline.

There is no pre-import forecast showing:

- added Box-1 stock;
- expected session demand;
- projected cohort completion date;
- displaced maintenance or active-goal work;
- expected acquisition-to-FSRS arrival flow.

### 4.6 Rapid retry

Rapid retry is useful but supporting, not central.

Current implementation risks include:

- automatic wrap-up includes some rating-2/confused words;
- sub-10-minute FSRS successes can receive full scheduling credit;
- acquisition guard successes still enter cumulative graduation counters;
- checkpoint entries can be lost after failed card fetch;
- automatic wrap-up is not truly persistent across abandoned fragments;
- treatment origin and version are not persisted;
- independent rollback switches are absent;
- the same-day amendment changed the causal contrast.

## 5. Proposed before/after system

| Area | Current | Proposed |
|---|---|---|
| Objective | Reduce or explain due count | Maximize durable sentence recognition per learner effort |
| Evidence | Scheduling uses all words; several analytics privilege primary | All sentence words are first-class evidence everywhere; primary is a selector/effort slice |
| Debt | Several incompatible totals | One reconciled stock-and-flow ledger with clearly named lane views |
| Selector | Frequency-first opening block plus mixed scoring | Calibrated risk layer, guaranteed filled quotas, then sentence coverage optimization |
| Acquisition | Four Box-1 repetitions; cumulative counters drive graduation | Broader, better-spaced delivery; separate encounter and graduation-eligible evidence |
| Graduation | Transition timestamp emphasized | Time to retained FSRS knowledge emphasized |
| FSRS | Default weights, 0.95 target, contaminated optimizer inputs | Walk-forward calibrated, modality-aware comparison on held-out sentence outcomes |
| Retry | Client-local, 4-minute minimum, unbounded wrap-up | Persistent episode ledger, credited after intended spacing, capped immediate delivery, spillover |
| Intake | Immediate cohort addition, sometimes cap-bypassed | Capacity preview, staged option, explicit immediate override |
| Reporting | Headline totals and target-only slices | Effort, all-word reviews, distinct obligations served, inflow/outflow, cold retention |

## 6. Specific next-step plan

The work is divided into independently reviewable work packages. Each package has an entry condition, concrete outputs, tests, and an exit gate.

Validation is proportional to blast radius:

- **Fast lane:** a narrow conformance fix, read-only preview, or selector-allocation
  correction may proceed after pinned calculations, focused unit/state-machine tests,
  bounded golden replay or historical snapshot replay, a feature switch, and a
  documented rollback.
- **Deep lane:** acquisition/graduation semantics, FSRS parameters, stored-counter
  restructuring, or state repair requires version-segmented word-state replay and
  external adversarial validation.

WP0–WP1 are prerequisites for new experiments in either lane. The complete WP2
word-state replay is a prerequisite for deep-lane changes, not for every safe
behavioral correction.

### Work Package 0 — Freeze definitions and baseline

**Purpose:** Prevent metric and scope drift before writing algorithms.

**Tasks:**

1. Create a versioned metric dictionary covering:
   - sentence cards answered;
   - all valid sentence-word reviews;
   - primary selector-target reviews;
   - quiz and listening reviews;
   - distinct due obligations served;
   - net FSRS stock change;
   - acquisition stock and box transitions;
   - scheduled arrivals;
   - graduations entering FSRS;
   - first cold success ≥24 h, ≥3 d, and ≥7 d;
   - retained graduation;
   - session completion and abandonment.
2. Define canonical inclusion/exclusion rules:
   - function words;
   - proper names;
   - canonical variants;
   - suspended words;
   - slow-lane words;
   - quiz modality;
   - undone and duplicate reviews.
3. Record the exact baseline:
   - Git commit SHA;
   - database snapshot filename, size, and SHA-256;
   - interaction-log range and SHA-256 per file;
   - FSRS library version and scheduler configuration;
   - relevant selector/acquisition constants;
   - timezone and audit cutoff.
4. Preserve the definitions and baseline manifest in `research/`.

**Proposed outputs:**

- `research/learning-metrics-spec.md`
- `research/baselines/learning-system-2026-07-25.json`
- `research/baselines/learning-system-2026-07-25.sha256`

**Tests/checks:**

- every metric has one unambiguous unit;
- primary and collateral with the same word/rating follow identical scheduling definitions;
- raw and main-lane debt are explicitly distinct;
- opening stock + arrivals + graduations − resolutions = closing stock, or every residual is explained.

**Exit gate:** Another agent can reproduce and explain every reported number without reading this conversation.

### Work Package 1 — Build one reproducible read-only analysis

**Purpose:** Replace scratchpad queries with an auditable command.

**Tasks:**

1. Add a read-only script with explicit inputs:

   ```text
   backend/scripts/analyze_learning_system.py
     --db PATH
     --interaction-log-dir PATH
     --cutoff ISO_DATETIME
     --output-dir PATH
     --strict-read-only
   ```

2. Open SQLite using read-only URI mode and fail if a journal, WAL, or database write is attempted.
3. Produce:
   - machine-readable JSON;
   - CSV tables;
   - a generated Markdown report;
   - warnings for incomplete logs, missing metadata, or definition mismatches.
4. Recompute:
   - current stocks;
   - daily flows;
   - all-word retention;
   - stability/overdue calibration;
   - acquisition duration and post-graduation outcomes;
   - failure/re-exposure cohorts;
   - session fragmentation;
   - selector serviceability.
5. Keep primary/collateral only as diagnostic slices.

**Proposed outputs:**

- `backend/scripts/analyze_learning_system.py`
- `backend/tests/test_analyze_learning_system.py`
- `research/learning-system-baseline-2026-07-25/summary.json`
- `research/learning-system-baseline-2026-07-25/report.md`

**Tests/checks:**

- run twice against the same inputs and compare byte-identical JSON;
- verify exact headline values against independent SQL;
- inject a fake primary/collateral pair and prove equal treatment;
- inject a quiz review and prove it does not silently become a sentence outcome;
- verify undone and duplicate events are excluded correctly;
- confirm the script never modifies timestamps or databases.

**Exit gate:** Independent SQL and the script agree on stocks, flows, and outcome denominators.

### Work Package 2 — Build deterministic replay before candidate algorithms

**Purpose:** Establish that counterfactual claims are produced by a trustworthy harness.

#### 2A. Word-state replay

Replay actual per-word review events through:

- current acquisition logic;
- current FSRS logic;
- candidate logic selected by configuration.

Each replay step must preserve:

- event ID;
- review timestamp;
- modality;
- rating;
- sentence/quiz context;
- acquisition versus FSRS state;
- algorithm version;
- pre-state and post-state;
- intended next due;
- graduation eligibility.

**Required baseline check:** On a bounded, version-consistent window, current-policy
replay must reconstruct stored states and due dates within documented tolerances.
Synthetic/golden histories must match exactly. Production mismatches must be
classified, not ignored; reconstructing every historical state across all scheduler
versions and manual repairs is explicitly out of scope.

#### 2B. Session-state replay

Replay interaction events through a pure state machine covering:

- session start;
- card display;
- answered card;
- long pause;
- session refresh/swap;
- abandoned fragment;
- retry enqueue;
- maturity;
- expiry;
- fetch failure;
- checkpoint delivery;
- wrap-up delivery;
- spillover to the next session.

**Required invariant:** Every eligible failed-word episode reaches exactly one documented terminal state:

- delivered and answered;
- pending for future delivery;
- deliberately expired into normal scheduling;
- excluded for a recorded reason.

It must never disappear silently.

#### 2C. Selector replay

Given one immutable database snapshot and session request:

- run the current selector;
- run a candidate selector;
- log every considered candidate and exclusion;
- compare serviceability, coverage, risk, quality, diversity, and lane allocation;
- do not submit reviews.

**Proposed outputs:**

- `backend/app/replay/word_state.py`
- `backend/app/replay/session_state.py`
- `backend/app/replay/selector.py`
- `backend/scripts/replay_learning_system.py`
- focused unit and golden-fixture tests.

**Exit gate:** Baseline reconstruction passes, every mismatch is explained, and replays are deterministic across repeated runs.

### Work Package 3 — Measurement/UI semantics, still without learning-policy change

**Purpose:** Make the current system legible before optimizing it.

**Tasks:**

1. Replace ambiguous review-debt presentation with:
   - raw FSRS stock;
   - actionable main-lane FSRS stock;
   - acquisition due stock;
   - empirically calibrated risk bands;
   - scheduled arrivals;
   - graduation arrivals;
   - distinct obligations served;
   - net closing change.
2. Replace “solid/harmless” stability labels.
3. Split “touched” into:
   - last sentence review;
   - last retrieval of any modality.
4. Keep sentence effort and all-word learning evidence separate.
5. Correct “cleared” semantics:
   - “served” means reviewed while due;
   - “resolved” means the post-review state is no longer immediately due under the chosen definition;
   - “net change” is a stock calculation.

**Validation:**

- API and UI totals reconcile to the baseline report;
- labels specify units and lane;
- no primary-only metric is presented as global retention;
- screenshots and snapshot tests cover all combinations.

**Rollback:** UI/API fields are additive first; old fields remain until consumers migrate. Removal is a later isolated commit.

### Work Package 4 — First behavioral candidate: selector allocation

**Purpose:** Improve learning coverage without changing ratings, FSRS, acquisition, or retry credit.

This is the recommended first behavioral experiment because it changes presentation priority only and preserves review validity.

#### Candidate selector structure

1. Build the serviceable due set.
2. Assign words to protected needs:
   - fragile/relearning;
   - long-overdue empirically high-risk;
   - oldest due Box 1;
   - due Box 2/3;
   - explicit active-book goal;
   - ordinary maintenance.
3. Fill small quotas for each protected need.
4. If a candidate is unavailable, continue scanning until the quota is filled or the set is proven unserviceable.
5. Deduplicate selected sentences and recompute actual covered obligations.
6. Allocate remaining slots through current quality/diversity/set-cover logic.
7. Record the selection reason for every chosen sentence and every unfilled quota.

#### Candidate configurations to replay

Do not choose weights before calculation. At minimum compare:

- **S0:** current selector;
- **S1:** corrected filled opening block, current frequency ordering;
- **S2:** empirical-risk opening block;
- **S3:** empirical-risk quotas plus explicit active-book reservation;
- **S4:** S3 plus acquisition breadth reservation.

#### Offline comparison

For every historical session-start snapshot:

- number of sentence cards;
- distinct due FSRS words covered;
- distinct acquisition words covered;
- risk-weighted obligations covered;
- oldest unserved age;
- active-book targets covered;
- sentence quality;
- unknown scaffold count;
- duplicate similarity;
- estimated duration.

#### Shadow mode

If an offline candidate passes, compute candidate sessions in production without returning them to the client. Log candidate versus actual selection.

#### Live experiment

Only after shadow validation:

- randomize eligible session builds between current and one candidate;
- preserve all review and scheduling behavior;
- use all sentence-word outcomes;
- cluster analysis by session and lemma;
- primary outcome: risk-adjusted first cold recall and distinct risk-weighted obligations served per sentence;
- guardrails: session completion, response time, sentence quality flags, active-book displacement.

**Rollback:** One server-side selector-policy flag returns session assembly to S0. No review history needs deletion because all presented reviews remain valid.

### Work Package 5 — Acquisition delivery and graduation evidence

**Purpose:** Reduce old Box-1 stock while improving—not laundering—retained graduation.

#### Separate evidence classes

Define conceptually:

- `total_encounters`;
- `total_reviews`;
- `spaced_eligible_reviews`;
- `spaced_eligible_successes`;
- `same_session_reencoding_successes`;
- `cold_successes`.

Graduation logic must not derive solely from aggregate counters that mix these categories.

Do not begin by replacing `times_seen`/`times_correct` or adding six mutable columns.
First derive these classes from `ReviewLog` and explicit review metadata. Preserve the
legacy counters for compatibility, audit every leech/recovery/graduation consumer, and
consider physical schema changes only if derived evidence proves insufficient.

#### Candidate delivery policies

Compare:

- **A0:** current four Box-1 appearances;
- **A1:** two appearances separated by at least N sentence cards;
- **A2:** two appearances plus a persistent delayed retry after failure;
- **A3:** broader protected Box-1 coverage with no forced fourth repetition;
- **A4:** hybrid based on zero-correct versus previously-correct Box 1.

The value of `N` must be selected by replaying actual card timing. Candidate timing should include 10-, 15-, and 20-minute equivalents rather than assuming card count is an adequate proxy.

#### Graduation policy replay

Compare current rules with variants that:

- keep Tier-E long-interval graduation;
- require at least one spacing-eligible success for perfect/high-accuracy graduation;
- prevent a rapid quiz success from entering later graduation accuracy;
- preserve full failure evidence.

#### Outcomes

Primary:

- retained graduation: first sentence success ≥24 h and ≥3 d after graduation.

Secondary:

- 7-day sentence recall;
- time in Box 1;
- due Box-1 age distribution;
- cards per retained graduation;
- total acquiring stock;
- new FSRS arrivals;
- downstream FSRS failures.

**Rollback:** Acquisition-policy version is logged per review. A policy flag stops future use. Existing legitimate reviews are preserved; deterministic replay is required before repairing derived state.

### Work Package 6 — Clean FSRS calibration

**Purpose:** Determine whether scheduling parameters or desired retention should change.

**Status 2026-07-26:** deterministic segmented descriptive calibration is
complete, including separate strict-success and FSRS-recall targets. It rejects
immediate retuning and identifies legacy/relearning state as the dominant
mismatch. Exact dependency pinning and per-review config stamps are implemented
in the workspace; rolling-origin optimizer/state replay remains pending.

#### Dataset construction

Build separate datasets for:

- sentence reading;
- listening;
- bare-word quiz/retry;
- acquisition-derived initial FSRS cards;
- pre- and post-algorithm-version windows.

For the primary calibration:

- use all eligible sentence-word outcomes;
- keep primary/collateral as diagnostic slices only;
- include actual review lateness;
- exclude duplicates, undo artifacts, function words, proper names, and invalid historical states;
- tag same-session outcomes.

#### Validation design

Use rolling-origin/walk-forward validation:

1. train on an earlier window;
2. validate on the next untouched window;
3. roll forward;
4. report every fold;
5. retain a final untouched holdout.

Compare:

- current production scheduler;
- current weights with alternative desired retention;
- optimized weights with current desired retention;
- optimized weights and calibrated desired retention;
- optional lateness/risk correction used only by the selector.

Metrics:

- calibration curve;
- Brier score;
- log loss;
- recall by predicted-risk decile;
- recall by overdue-age bucket;
- recall after lapse;
- intended interval distribution;
- simulated due arrivals;
- retained recall under actual session capacity.

#### Deployment rule

Do not deploy merely because optimizer loss improves. A candidate must:

- improve held-out calibration;
- avoid unacceptable 7-/30-day retention loss;
- remain feasible under actual learner capacity;
- avoid a large increase in fragile overdue stock;
- pass deterministic state replay.

**Rollback:** Scheduler configuration is versioned and independently switchable. Pre-deployment state is backed up. All treatment reviews log the scheduler version.

### Work Package 7 — Harden rapid retry as a supporting subsystem

**Purpose:** Guarantee eventual correction without massed-credit inflation or unbounded wrap-up.

**Current status (PR #223, conformance protocol v2):** the three confirmed
registration defects are fixed and deployed: automatic wrap-up is rating-1-only,
checkpoint fetch failures no longer consume the queued retry, and guarded acquisition
successes enter neither graduation counter. Telemetry carries protocol version 2 and
the experiment is frozen for its readout window. The persistence/ledger design below
is deferred until after that readout unless an operational loss bug appears.

#### Required behavior

1. Trigger only on rating 1.
2. Persist:
   - episode ID;
   - triggering review ID;
   - canonical lemma ID;
   - origin session;
   - failure time;
   - state at failure;
   - acquisition box or FSRS pre-card;
   - prior interval/stability;
   - arm/config version.
3. Prioritize old lapses and zero-correct Box-1 words for limited immediate delivery.
4. Cap immediate extras:
   - an absolute maximum;
   - and a maximum share of the sentence workload.
5. Spill remaining episodes to later checkpoints or the next session.
6. Do not discard an episode before card fetch succeeds.
7. Keep rating-2 confusion in the exact-surface path.

#### Credit policy candidates

- Success before the intended minimum interval:
  - log as re-encoding;
  - do not advance acquisition graduation evidence;
  - do not skip an FSRS relearning step.
- Success at or after the intended interval:
  - normal acquisition/FSRS policy, subject to spacing eligibility.
- Failure:
  - counts normally.

#### Experiment

Both arms receive eventual retry. Compare delivery timing:

- end/next-session only;
- delayed checkpoint plus fallback.

Stratify:

- acquisition;
- recent FSRS lapse;
- old FSRS lapse.

Primary outcome:

- first sentence-word success ≥24 h.

Secondary:

- ≥3 d and ≥7 d sentence success;
- repeated failures;
- time to two spaced successes;
- cards per recovery;
- completion and abandonment;
- subjective repetition/frustration.

The analysis must cluster by lemma and session. The original naive power estimate must be recomputed with observed clustering and delivery rates.

### Work Package 8 — Intake-impact preview

**Purpose:** Make cohort costs explicit without forbidding valuable immediate learning.

Before applying an import, compute:

- words already known;
- canonical/homograph conflicts;
- new acquiring count;
- projected Box-1 additions;
- reviewable sentence coverage;
- estimated sessions to first exposure;
- estimated sessions to graduation;
- expected new FSRS arrivals;
- maintenance and active-goal displacement;
- staged versus immediate scenarios.

User options:

- import immediately;
- stage across a chosen number of days;
- import only words above a goal-value threshold;
- save the preview without importing.

**Validation:** Replay the July bookifier cohorts against historical session capacity and compare forecast to observed progress.

**2026-07-25 implementation status:** The read-only classification and
capacity-disclosure layer is implemented in `scripts/preview_intake_impact.py`
and validated in `research/intake-impact-preview-validation-2026-07-25.md`.
The July 15 backtest showed that the median completed episode is useful only as
a success-conditioned reference, not an unconditional workload forecast:
610 acquisition word reviews had already been spent while only 88/202 cohort
lemmas had ever graduated. First-exposure, graduation-time, retention, and
goal-displacement predictions therefore remain explicitly unavailable until
the bounded selector replay exists. A subsequent observed-trajectory audit
measured median admission→first review at 51.67 hours (p90 155.17 hours) and
first-review session ordinal at median 12.5 (p90 27); 36.6% were first-reviewed
by day 1, 76.7% by day 5, and four remained unreviewed at 10.46 days. These
values are now available as an empirical reference, not a staged-intake causal
forecast.

### Work Package 9 — Rollout, documentation, and decision log

Every adopted candidate requires:

- one experiment specification;
- one baseline manifest;
- one generated analysis output directory;
- exact code/config commit;
- separate feature switch;
- operational smoke tests;
- monitoring queries;
- rollback command/procedure;
- scheduled early, coarse, and long-term readouts;
- final keep/revert decision in `research/experiment-log.md`.

Behavioral changes should be isolated by concern. Do not combine selector, acquisition, FSRS, retry, and UI changes in one experiment or deployment.

## 7. Proposed execution order

The recommended order is:

1. Retry conformance v2 and clean protocol boundary — completed in PR #223.
2. **WP0–WP1:** freeze definitions and create one reproducible read-only
   analysis — completed 2026-07-25.
3. **WP8:** add the read-only intake-impact preview — classification/capacity
   layer completed 2026-07-25; predictive fields wait for bounded selector
   replay.
4. **WP4/S1:** correct quota filling while preserving current ordering —
   bounded replay completed 2026-07-25 and **rejected S1 before shadow** because
   it reduced mean due-word coverage by 2.42/request and produced large
   short-session regressions. Coverage-budgeted S1b then gained 7.75 base due
   words/request with no coverage regressions, but was also **rejected before
   shadow** because acquisition coupling added 6.75 returned cards on average
   and increased workload in 11/12 requests. A historical-material replay also
   lost five distinct presented words/request on average. Pause selector
   policy changes until additive selection telemetry can define a genuinely
   workload-neutral S1c; do not manufacture another candidate from the same
   single snapshot.
5. **WP4/S2:** consider empirical-risk ordering only after S1 has a clean result.
6. **WP2 deep lane:** build version-segmented word-state replay before acquisition,
   graduation, counter, or FSRS changes.
7. **WP5:** the derived graduation audit and first event-decision replay are
   complete. A prior-day gate would defer 9/23 perfect-accuracy and 0/17
   high-accuracy decisions, but only four deferred events have delivered
   3-day follow-up and the replay cannot model added queue workload. Keep only
   the rating-2 correctness fix; collect explicit v2 evidence telemetry and
   build full state/selector replay before any threshold change. Physical
   counter restructuring remains last.
8. **WP6:** segmented descriptive calibration and reproducibility fixes are
   complete; build rolling-origin optimization/replay separately for
   post-acquisition Review cards and legacy/Relearning recovery before changing
   desired retention or weights.
9. **WP7:** read protocol-v2 retry results before considering a persistent ledger or
   new timing arms.
10. **WP9:** consolidate winning configurations and retire old metrics only after compatibility review.

This sequence is deliberate:

- measurement and replay make later claims reversible;
- selector changes presentation priority without invalidating existing ratings;
- acquisition changes are evaluated before scheduler tuning;
- FSRS calibration uses cleaner modality/version data;
- retry remains part of the system but does not consume the entire optimization agenda.

## 8. Stop conditions and rollback principles

### 8.1 Immediate operational stop

Disable a candidate immediately if any of these occur:

- wrong population receives treatment;
- a review is duplicated or lost;
- an episode disappears without a terminal reason;
- stored scheduler/acquisition state cannot be replayed;
- workload exceeds the configured hard cap;
- a session becomes impossible to resume;
- offline synchronization changes arm or credit;
- undo leaves state inconsistent.

### 8.2 Learning stop

Pre-register the numerical no-harm margins after baseline variance is measured. Stop or revert if:

- ≥24 h or ≥7 d sentence retention is worse than control beyond the margin;
- retained graduations per learner minute decline;
- fragile overdue stock grows materially;
- leech/suspension rates rise;
- completion or continued study declines;
- active reading-goal coverage is displaced beyond the agreed reservation.

### 8.3 Rollback behavior

Preferred rollback:

1. disable future candidate behavior;
2. preserve legitimate review events;
3. rerun deterministic replay to quantify derived-state effects;
4. repair derived state only when the complete affected chain is known;
5. record the rollback and evidence.

Deleting review history merely because an experiment lost is not acceptable. A shown and answered word remains real learning evidence.

## 9. Open questions for validation by another agent

The validating agent should answer these independently, with evidence and explicit disagreement where appropriate.

### 9.1 Data and metric correctness

1. Can the reported 600 reviews, 428 distinct words, and 26 graduations be reproduced from the stated audit window?
2. Are primary and collateral word outcomes truly routed identically in every relevant scheduling and graduation path?
3. Are canonical variants double-counted anywhere in debt, failure, or distinct-word metrics?
4. Are function words, proper names, suspended words, and slow-lane artifacts excluded consistently?
5. Are undone or offline-resubmitted events contaminating counts?
6. Does the debt stock-flow identity reconcile on individual days?
7. Does “first cold review ≥24 h” accidentally select an early quiz or another modality?
8. Are historical algorithm changes large enough that a 120-day pooled analysis is invalid without version segmentation?

### 9.2 Causal and statistical validity

1. How much does lemma/session clustering inflate the original retry power estimate?
2. Are repeated failures from the same lemma treated as independent episodes?
3. Is first-review-after-threshold subject to informative censoring?
4. Does session abandonment create treatment-dependent missing outcomes?
5. How should late review behavior be separated from memory decay?
6. Is a 24-hour threshold sufficient, or should the primary endpoint require sleep or a fraction of the intended interval?
7. Should analysis use a mixed-effects model, clustered bootstrap, randomization inference, or another method?
8. What is the appropriate no-harm margin for retention, completion, and workload?

### 9.3 Selector proposal

1. Is the claim that the opening block is frequency-first and can underfill correct in every selector path, including speculative builds?
2. Does a quota layer reduce global multi-word set coverage?
3. Can risk be calibrated directly from observed outcomes without leaking future information?
4. What is the correct serviceability definition?
5. Could risk-first selection repeatedly present low-quality rescue sentences?
6. How should active-book value be quantified and reserved?
7. Would a simpler corrected oldest-overdue block capture most of the benefit without a full risk model?
8. Does replay account for the fact that counterfactually selected sentences have unknown outcomes?

### 9.4 Acquisition proposal

1. Is fixed four-repetition Box 1 actually causing breadth loss in real builds, or are extra slots mostly otherwise unused?
2. How many words graduate from same-session perfect/high-accuracy evidence?
3. What is their held-out post-graduation retention after controlling for difficulty and source?
4. Does Tier-E long-gap graduation remain safe across acquisition sources?
5. What definition of spacing-eligible evidence best fits sentence learning?
6. Would reducing repetitions harm encoding for zero-correct words?
7. Should zero-correct, previously-correct, failed, and imported words have different policies?
8. Can counters be changed without corrupting historical graduation logic or making replay impossible?

### 9.5 FSRS proposal

1. Is observed overconfidence caused by weights, desired retention, overdue reviews, rating semantics, modality mixing, or selector bias?
2. Are quiz reviews currently included in optimizer and replay inputs, and how large is their influence?
3. Does excluding quiz data improve held-out calibration or merely reduce sample size?
4. Are reading ratings comparable to standard flashcard ratings assumed by FSRS?
5. Should lateness be addressed by FSRS parameters or only selector prioritization?
6. Is stability ≥30 days ever a useful urgency category after empirical calibration?
7. How should acquisition-created initial cards enter optimizer training?
8. Can candidate weights be replayed faithfully across library versions?

### 9.6 Retry proposal

1. Does automatic wrap-up currently include rating-2 confused words?
2. Exactly how often does a sub-10-minute Good skip a pending FSRS relearning step?
3. Does the acquisition retest guard feed cumulative accuracy used by later graduation?
4. Can checkpoint fetch failure lose fallback delivery?
5. Can treatment origin be misattributed after session swap?
6. What proportion of failures would be delayed under a hard cap of 1, 2, or 3?
7. Is bare-word recall the right retry task for sentence-recognition learning, especially for old lapses?
8. Should an old lapse receive corrective feedback followed by a new sentence rather than a citation-form quiz?

### 9.7 Intake proposal

1. Can import impact be forecast accurately enough to inform a decision?
2. What is the opportunity cost of staged import for active-book comprehension?
3. Should book token frequency, chapter proximity, or learner goal determine import value?
4. Can a high-value immediate override remain safe and fully documented?
5. How did the July forecasts compare with actual cohort progress?

### 9.8 Operational reversibility

1. Are proposed feature switches server-controlled and independently deployable?
2. Can every affected state be reconstructed from logs and stored pre-state?
3. What happens when client and server policy versions differ offline?
4. Does undo reverse candidate-specific metadata and derived counters?
5. Are interaction logs retained long enough for 30-day outcomes?
6. Are database backups and baseline hashes sufficient to reproduce an experiment?
7. Can one behavioral candidate be rolled back without reverting unrelated work?

## 10. Known risks

### Analytical risks

- observational timing comparisons are confounded;
- pooled history crosses algorithm versions;
- repeated lemma/session events reduce effective sample size;
- outcome censoring favors frequently reviewed words;
- current interaction logs may not capture every client/offline transition;
- nominal stability and actual review lateness are entangled;
- counterfactual selector outcomes are unobserved.

### Product risks

- risk-first selection can feel repetitive;
- reducing acquisition repetition can weaken initial encoding;
- excluding rapid-success credit can temporarily increase visible debt;
- cleaner FSRS calibration can materially change workload;
- staged imports can delay active reading readiness;
- persistent retry state adds operational complexity;
- a coherent debt display may still invite debt-minimization behavior.

### Engineering risks

- historical state may not replay exactly across algorithm/library versions;
- client-local behavior may be impossible to reconstruct after offline gaps;
- schema additions can outlive abandoned experiments;
- feature-flag combinations can produce untested states;
- later reviews make destructive rollback of earlier scheduler events unsafe.

## 11. Validation brief for another agent

The validating agent should work read-only unless separately authorized.

### Required inputs

The agent should inspect:

- this document;
- `research/analysis-2026-07-25-rapid-reexposure-proposal.md`;
- the relevant entries in `research/experiment-log.md`;
- `backend/app/services/sentence_selector.py`;
- `backend/app/services/acquisition_service.py`;
- `backend/app/services/fsrs_service.py`;
- `backend/app/services/sentence_review_service.py`;
- `backend/app/simulation/runner.py`;
- `backend/scripts/optimize_fsrs.py`;
- `backend/scripts/replay_fsrs.py`;
- `frontend/app/index.tsx`;
- current tests for selector, acquisition, FSRS, retry credit, idempotency, undo, and debt reporting;
- read-only production snapshots/logs supplied for the audit.

### Required method

1. Reproduce the major numerical claims independently.
2. Trace every proposed invariant through code.
3. Identify where the proposal assumes facts not established by data.
4. Attempt to falsify the ranked diagnosis.
5. Check whether a simpler intervention could achieve most of the benefit.
6. Review the replay plan for data leakage and counterfactual overclaiming.
7. Review rollback feasibility after subsequent reviews.
8. Report findings by severity with exact code/data evidence.

### Required output format

The validating agent should return:

1. **Verdict:** accept, accept with revisions, or reject.
2. **Reproduced claims:** exact values and query/script used.
3. **Disputed claims:** proposal statement, contradictory evidence, and corrected statement.
4. **Missing risks:** severity, likelihood, and affected work package.
5. **Plan changes required before WP0–WP2.**
6. **Plan changes required before any live behavior.**
7. **Open decisions requiring the user rather than an agent.**
8. **Confidence and remaining uncertainty.**

The agent should not merely review prose. It should validate the system model, data definitions, experiment design, and reversibility.

## 12. Decisions that still require the user

These should not be inferred by an implementing agent:

1. How much active-book readiness may be traded for maintenance and retention?
2. Is every rating-1 word guaranteed a retry even if delivery moves to a later session?
3. Is temporary growth in visible due count acceptable when it protects spacing validity?
4. What learner-effort increase is acceptable for a demonstrated retention gain?
5. Should bare-word recall remain part of learning, or should retry be sentence-only where possible?
6. What minimum improvement justifies added state-machine and operational complexity?
7. Should import staging be the default with immediate override, or immediate import with a warning?

## 13. Immediate proposed next action

If this proposal is accepted for further work, authorize only:

> Work Packages 0–2: metric specification, reproducible read-only analysis, and deterministic replay.

That authorization should explicitly exclude:

- selector behavior changes;
- acquisition/graduation changes;
- FSRS parameter changes;
- retry behavior changes;
- import-policy changes;
- database repair;
- deployment.

After WP0–WP2 are complete and independently validated, return with:

- reconciled baseline;
- replay trust report;
- updated ranked opportunities;
- exact selector candidate configurations;
- calculation-before-simulation impact estimates;
- a new request for authorization before any behavior changes.
