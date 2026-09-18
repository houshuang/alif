# From learning history to learning experiments

## Decision logging, target-trial analyses, and micro-randomized experiments for Alif

**Date:** 2026-07-30  
**Scope:** one learner, one evolving application, repeated word-level outcomes from displayed Arabic sentences  
**Companion report:** `longitudinal-learning-report-2026-07-30.html`

## 1. Why this design is appropriate

The database is unusually rich for an N-of-1 system: each displayed sentence creates a timestamped judgment for every canonical content word it contains. That supports high-resolution within-person prediction and carefully bounded causal experiments. It does **not** turn thousands of correlated observations into thousands of independent learners. Generalization is to future decisions for this learner under Alif, unless later replicated elsewhere.

The core analytical rule is:

> Once a sentence has been displayed, every canonical content word in it is an equally valid learning outcome. “Primary” records why the scheduler selected the sentence; it is not an inclusion rule or an evidence weight.

The evidence hierarchy for product decisions should be:

1. randomized word- or decision-level comparisons;
2. prospective temporal prediction on future outcomes;
3. pre-specified quasi-experiments and target-trial emulations;
4. adjusted repeated-outcome associations;
5. raw descriptive comparisons;
6. current-state summaries used as historical explanations.

Large row counts narrow sampling noise inside a chosen model, but they do not repair time-varying confounding, outcome dependence, missing presentation metadata, or policy drift.

## 2. Scientific background translated into Alif

Pavlik and Anderson’s activation-based vocabulary model treats the complete timing of successful practice as relevant to future memory, and their experiment found that the benefit of spacing changes with practice amount and the desired retention interval ([2005 paper](https://onlinelibrary.wiley.com/doi/abs/10.1207/s15516709cog0000_14)). Their later optimization work explicitly balances wider spacing, which can improve long-term retention, against the time cost of failures caused by spacing too aggressively ([2008 paper](https://pubmed.ncbi.nlm.nih.gov/18590367/)). This motivates using the full exposure trajectory rather than only the latest gap.

Settles and Meeder’s half-life regression estimates a concept’s memory half-life and predicts recall as elapsed time relative to that half-life. It is an appropriate transparent benchmark for Alif’s richer history models, not an assumed truth ([ACL 2016](https://aclanthology.org/P16-1174/)).

The “target trial” framework says that a causal analysis of observational history should first specify the randomized experiment it wishes it had—eligibility, time zero, strategies, outcome, follow-up, estimand, and analysis—and only then map stored data to that protocol ([Hernán & Robins 2016](https://pmc.ncbi.nlm.nih.gov/articles/PMC4832051/)). This prevents vague claims such as “spacing caused higher recall” from changing their population or time zero after the data are inspected.

Standard regression is especially fragile when a time-varying predictor is both affected by earlier treatment and influences later treatment. Marginal structural models were developed for this situation using inverse-probability weighting ([Robins, Hernán & Brumback 2000](https://pubmed.ncbi.nlm.nih.gov/10955408/)). In Alif, prior accuracy both responds to earlier spacing and affects later scheduling, so a complete causal spacing analysis ultimately needs sequential propensities, not only a one-row adjustment.

Micro-randomized trials repeatedly randomize an intervention at eligible decision moments, allowing estimation of proximal effects and how those effects vary with time and context ([Klasnja et al. 2015](https://pubmed.ncbi.nlm.nih.gov/26651463/)). This maps naturally to sentence selection: one learner can contribute hundreds of randomized decisions without pretending to be hundreds of people.

Arabic morphology deserves explicit treatment rather than a generic “word difficulty” variable. Prior visual-recognition research has directly tested roots and word patterns, with results that do not justify assuming automatic transfer across all related forms ([Abu-Rabia & Awwad 2004](https://eric.ed.gov/?id=EJ686919)). Alif should therefore measure exact-form and root-family transfer instead of building a policy on a universal morphology claim.

For probabilistic prediction, discrimination and calibration must be reported separately. AUC asks whether risk is ranked well; Brier score and calibration compare probabilities with observed outcomes. A model can rank well and still be too optimistic to schedule safely.

## 3. Canonical analytical dataset

### 3.1 One presentation, several equal outcomes

The presentation table should contain one row per displayed sentence:

- `presentation_id`
- `decision_id`
- `session_id`
- start and reveal timestamps
- sentence ID and immutable sentence revision/hash
- actual displayed Arabic
- actual initial tashkīl state
- response time and interaction events
- policy and model versions

The outcome table should contain one row per canonical content word in that presentation:

- `presentation_id`
- `canonical_lemma_id`
- exact displayed token and normalized surface form
- token position
- clean outcome: rating 3/4 and no explicit confusion
- raw rating, confusion flag, and selected alternative
- primary/collateral and acquisition flags as provenance
- assisted, revealed, prompted, or immediate-retest flags

No word is removed merely because it was collateral.

### 3.2 History clock

Before each word outcome, reconstruct history from **earlier displayed reading sentences only**:

- total sentence exposures;
- clean and failed exposures;
- distinct calendar days;
- distinct sessions;
- distinct sentence IDs;
- exact surface forms;
- time since any exposure, clean exposure, and failure;
- recency-weighted success and failure activation;
- other observed words from the same root;
- prior assisted versus unassisted evidence.

Quiz, listening, intro-card acknowledgement, and reintroduction metadata may be separate predictors, but must not silently advance a variable described as “prior displayed-sentence exposure.”

### 3.3 Outcome hierarchy

Use several outcomes rather than forcing one number to serve every purpose:

1. **Immediate clean recognition:** current word judgment.
2. **Cold recognition:** first clean/non-clean outcome after at least 3 days.
3. **Delayed confirmation:** first outcome in a pre-specified future window.
4. **Response cost:** response time, reveal use, and help interaction.
5. **Acquisition safety:** time in Box 1, unresolved failures, and workload.
6. **Transfer:** clean recognition on a previously unseen exact form.

An immediate assisted clean response must not be interpreted as a delayed unassisted retrieval.

## 4. Retrospective analyses implemented in phase two

### 4.1 Historical randomized intro-card experiment

The randomized code interval is defined by Git, not by current labels:

- start: commit `c20e932d`, 2026-03-03 22:17:14 UTC;
- end: commit `a2009959`, 2026-03-21 19:19:19 UTC;
- cohort entry: `acquisition_started_at` inside that interval;
- assignment: persisted `intro_ab_card` versus `intro_ab_sentence`;
- primary outcome: first post-entry acquisition judgment in a displayed reading sentence;
- estimand: intention-to-treat risk difference;
- uncertainty: Newcombe 95% interval and permutation test;
- multiplicity: Holm correction across the eight reported horizons.

Do not condition the primary analysis on an acknowledgement timestamp: receipt is post-randomization and protocol adherence was imperfect. A sensitivity analysis removes words with displayed-sentence history before cohort entry to match the cold-start hypothesis.

### 4.2 Forward-chained predictive bakeoff

For each April–July fold:

1. train only on outcomes before the first day of the fold;
2. predict every eligible outcome inside the fold;
3. concatenate untouched fold predictions;
4. report Brier, log loss, AUC, calibration error, and predicted versus observed prevalence;
5. repeat for cold tests with a gap of at least 3 days.

Benchmarks:

- recency-only logistic model;
- half-life regression;
- activation/history model;
- full history plus context, form, lexical, and delivery variables;
- stored pre-event FSRS retrievability on the common recoverable subset.

The comparison answers “what predicts the next word judgment?” It does not make the coefficients causal.

### 4.3 Observational spacing sensitivity

The main adjusted panel estimates a standardized contrast between upper and lower quartiles of across-day exposure density. Sensitivities include:

- complete-word bootstrap;
- complete-session bootstrap;
- regularized word intercepts;
- a coarse doubly robust comparison of high versus low density.

The doubly robust analysis is intentionally labeled a sensitivity. Without sequential decision probabilities it is not a marginal structural model of the full treatment history.

### 4.4 Failure/recovery episodes

A failure episode starts at the first failure in a consecutive run and ends at the next clean sentence-word outcome. Record:

- actual time to the next attempt;
- attempts and time to the first clean outcome;
- first later outcome after a gap of at least 3 days.

This distinguishes immediate repair from durable confirmation. Delay is observational and therefore a target for randomization, not a settled policy optimum.

### 4.5 Form, root, and sentence-load analyses

Exact-form transfer is reported by POS and in the adjusted panel. Root-family and weak-root comparisons remain exploratory because exposure to related words was selected. Confusion-network analysis is deferred until many more resolved directed confusions exist.

Sentence length, co-occurring fragile words, co-occurring novel forms, and session position are tested as delivery risks. A weak or null adjusted result is useful: it argues against a hard cap, but not against targeted prospective testing.

### 4.6 Regime detection

Daily delivery variables are standardized and segmented into contiguous regimes by BIC. Accuracy is excluded from selecting breakpoints and summarized only after segmentation. The purpose is to detect periods that require separate interpretation, not to attribute a breakpoint to a particular code commit.

## 5. Required decision log

Historical off-policy evaluation is currently not identifiable because the logs omit complete candidate sets and action probabilities. Every future selection decision should write one immutable record before presentation.

### 5.1 Decision envelope

```json
{
  "decision_id": "uuid",
  "decided_at": "UTC timestamp",
  "session_id": "uuid",
  "slot_index": 7,
  "policy_name": "sentence_selector",
  "policy_version": "git-sha-or-semver",
  "model_versions": {
    "memory": "id",
    "quality": "id",
    "tashkil": "id"
  },
  "experiment_assignments": [
    {
      "experiment": "form_transfer_mrt_v1",
      "arm": "novel_form",
      "probability": 0.5,
      "randomization_unit": "decision_id",
      "rng_key": "stored-audit-key"
    }
  ],
  "learner_state_hash": "reproducible hash",
  "candidate_set_hash": "hash",
  "candidate_count": 42,
  "chosen_sentence_id": 123,
  "chosen_probability": 0.03125,
  "fallback_reason": null
}
```

### 5.2 Candidate snapshot

For every eligible candidate—or an immutable reproducible snapshot referenced by hash—store:

- sentence ID and revision/hash;
- eligible/ineligible;
- every exclusion reason;
- all canonical content lemmas;
- target/due overlap;
- exact current surface forms;
- predicted word-level recall;
- prior counts, gaps, accuracy, day density, form novelty, and acquisition state as seen by the policy;
- sentence quality, source, length, grammar, and novelty features;
- each score component before combination;
- final score;
- probability of selection, including exploration;
- rank;
- computational fallback or missing-feature markers.

If the full candidate set is too large, store a versioned query/input snapshot sufficient to reproduce it exactly, plus the considered top set and all stochastic probabilities. A hash without reproducible inputs is not enough.

### 5.3 Actual presentation state

Selection intent is not exposure. When the card renders, persist:

- `decision_id` and `presentation_id`;
- exact displayed sentence and tokenization;
- actual font, tashkīl state, fading mask, and reveal state;
- intro/reintro/passage/wrap-up parent;
- whether the card was skipped, interrupted, or reconstructed offline;
- client and server version;
- local and server timestamps;
- interaction events and response latency.

### 5.4 Outcome linkage

Every word outcome must point back to `presentation_id` and therefore `decision_id`. Derived delayed outcomes should be materialized only by versioned analysis, not written as mutable truth.

## 6. Target trial for historical spacing

This specification prevents the estimand from drifting during analysis.

| Component | Target trial |
|---|---|
| Eligibility | Canonical word has ≥3 prior displayed-sentence exposures, ≥2 distinct days, a clean current outcome, no assisted prompt, and is not in acquisition danger |
| Time zero | Timestamp of that clean eligible sentence-word outcome |
| Strategies | Next unassisted displayed-sentence exposure targeted for a “short” versus “distributed” opportunity window |
| Assignment | Ideal 1:1 random assignment at time zero |
| Follow-up | From time zero through the first unassisted outcome in a pre-specified 7–21-day assessment window |
| Primary outcome | Clean recognition at assessment |
| Secondary outcomes | Failures before assessment, workload, response time, and unresolved/censored words |
| Estimand | Intention-to-treat difference in assessment clean probability among eligible decisions |
| Analysis | Randomization inference in the prospective trial; sequential weighting only if emulated observationally |

For a retrospective emulation:

- align eligibility, assignment proxy, and time zero;
- model the probability of the observed spacing strategy using only pre-time-zero history;
- model censoring caused by no assessment, suspension, or end of data;
- use stabilized weights with declared truncation;
- cluster uncertainty by word and session;
- report positivity diagnostics and effective sample size;
- conduct negative-control and unmeasured-confounding sensitivities.

The existing data cannot fully implement this because policy probabilities and candidate availability were not stored. The current +6.1-point panel estimate and doubly robust sensitivity motivate the trial; they do not replace it.

## 7. Micro-randomized experiment portfolio

Run one major experiment at a time unless factorial interactions are explicitly intended. Fix code and outcome definitions during each analysis window. Randomization probabilities must be stored for every eligible decision, including decisions where the selected action could not be delivered.

### 7.1 Exact-form transfer MRT

**Question:** When a known canonical word is due, does selecting an unseen useful surface form improve later transfer without increasing immediate failure too much?

- Eligibility: ≥3 prior sentence exposures; predicted clean probability 0.65–0.90; at least one quality-matched candidate with a seen form and one with a novel useful form; no recent failure requiring rescue.
- Arms: select seen-form candidate versus novel-form candidate.
- Randomization: 1:1 within eligibility strata for POS, predicted recall band, and gap band.
- Proximal outcome: current clean recognition and response time.
- Delayed outcome: first unassisted occurrence of the same exact form in days 3–14.
- Guardrails: acquisition backlog, sentence quality, immediate failure rate, and session duration.
- Moderators: verb/noun/adjective, weak versus sound root, prior root-family exposure, and form type.
- Analysis: centered treatment indicator with decision-time covariates; cluster by canonical word and session; report both immediate cost and delayed benefit.

### 7.2 Delayed-confirmation MRT after failure

**Question:** After feedback and immediate repair, when should the diagnostic confirmation occur?

- Eligibility: a failed word receives corrective feedback and becomes clean within the same session; no safety-critical acquisition rule is active.
- Shared treatment: immediate repair remains available to both arms.
- Arms: earliest eligible confirmation at 2–8 hours versus 20–36 hours; a later 2–4-day arm can be added only after safety review.
- Primary outcome: clean confirmation.
- Durable outcome: first unassisted test 3–14 days after confirmation.
- Guardrails: unresolved Box-1 words, cumulative extra cards, and user-visible frustration.
- Analysis: intention-to-treat by assigned opportunity window; record failure to deliver the assigned window rather than silently reassigning.

### 7.3 Tashkīl fading MRT

**Question:** In a safe memory band, what is the immediate cost and delayed benefit of initially hiding vowels?

- Eligibility: predicted clean probability 0.75–0.92; no new exact form; no recent failure; sentence contains no separately protected weak word.
- Unit: whole sentence presentation, to avoid mixed visual conditions and token-level pseudo-replication.
- Arms: initially vocalized versus initially faded.
- Randomization: 1:1, stratified by predicted difficulty and sentence.
- Proximal outcomes: word-level clean recognition, response time, reveal use, and partial/confused judgments.
- Delayed outcome: first unassisted eligible test after 3–14 days.
- Guardrails: automatic reveal, pronunciation access, maximum faded failures per session.
- Analysis: word outcomes remain equal evidence, with presentation- and word-clustered uncertainty.

### 7.4 Spacing/defer MRT

**Question:** When two actions are both safe, does deferring a currently eligible word produce better later retention per unit of practice?

- Eligibility: word is eligible but not overdue beyond a safety boundary; another useful sentence can fill the slot; action probabilities remain nonzero.
- Arms: surface now versus defer until the next declared opportunity window.
- Proximal outcome: workload displaced and any intervening failure.
- Primary delayed outcome: clean recognition in a fixed assessment window common to both arms.
- Guardrails: hard maximum lateness, acquisition exclusion, and daily workload.
- Essential detail: natural collateral exposure after “defer” is a treatment-policy event, not a protocol violation. It must be logged and included in the estimand.

### 7.5 Sentence-load MRT

**Question:** Does packing several novel forms into one otherwise good sentence reduce word-level comprehension?

- Eligibility: two candidates cover the same priority word(s), have matched quality, source, and predicted target difficulty, but differ in other novel-form load.
- Arms: low versus moderate other-novel-form load.
- Outcomes: clean recognition for every word, response time, reveal/tap behavior, and later recall of both target and collateral forms.
- Guardrails: never randomize to a sentence below the ordinary quality threshold.

### 7.6 Intro-card replication, only if the durable question matters

The historical trial already establishes a large immediate benefit. A new trial is justified only to answer a sharper question:

- compare the current full intro card with a minimal gloss/translation anchor, not with avoidable total cold exposure;
- predeclare a delayed assessment before rescue crossover;
- randomize at first-time acquisition only;
- keep sentence opportunities equivalent between arms;
- use first-sentence comprehension as a secondary, not sole, endpoint.

## 8. Analysis of micro-randomized trials

### 8.1 Primary estimand

For each eligible decision time \(t\), estimate the proximal effect of assignment \(A_t\) on outcome \(Y_{t+\Delta}\) among decisions where either arm could safely be delivered.

Use:

- centered treatment \(A_t - p_t\), where \(p_t\) is the stored randomization probability;
- pre-specified decision-time moderators;
- robust or randomization-based uncertainty clustered by word and session;
- calendar/block indicators for concept drift;
- intention-to-treat as primary;
- per-protocol only as a clearly labeled secondary analysis.

### 8.2 Dependence

Sentence-word outcomes share:

- a presentation;
- a session;
- a canonical word history;
- sometimes a root family.

Do not compute naïve binomial intervals over token rows. Use randomization inference where possible, and cluster/bootstrap at the unit that preserves the dependence relevant to the claim. Report both word- and session-clustered sensitivity when neither dominates conceptually.

### 8.3 Multiple outcomes and stopping

For each experiment predeclare:

- one primary outcome and one primary horizon;
- a small ordered set of secondary outcomes;
- correction method for multiple horizons or moderators;
- minimum exposure window;
- safety stopping rules;
- a fixed or alpha-spending analysis schedule.

Do not monitor daily unadjusted p-values and stop when one crosses 0.05. Product safety metrics can be monitored continuously, but efficacy inference must respect the declared stopping rule.

### 8.4 Practical sample planning

Power comes from randomized eligible decisions and variation in treatment, not total historical rows. Before launch:

1. replay current data to estimate the number of eligible decisions per week;
2. simulate outcomes using observed baseline risk, within-word correlation, and plausible effects;
3. apply the exact intended estimator and stopping rule;
4. choose an experiment duration that has useful power for the minimum product-relevant effect;
5. cap the run by calendar time so policy drift cannot continue indefinitely.

For rare decisions such as novel-form matched pairs, prefer longer accumulation or broaden safe eligibility rather than treating correlated word rows from one sentence as independent sample size.

## 9. Model deployment and monitoring

A predictive model may guide randomization strata or safe eligibility only after:

- forward-chained validation;
- calibration by calendar block and outcome definition;
- explicit fallback for missing history;
- versioned feature computation;
- shadow predictions written at decision time;
- comparison with a simple benchmark;
- monitoring of observed versus predicted clean rates;
- a declared recalibration trigger.

The full-history model’s advantage over recency is a reason to preserve trajectories. It is not permission to let the model deterministically choose all future actions; doing so would again remove the variation needed to evaluate alternatives.

## 10. Reporting template for every future policy change

Each experiment or algorithm release should add one compact record:

1. causal or predictive question;
2. eligibility and exclusion rules;
3. exact outcome and horizon;
4. randomization unit and probabilities, or why the change is not randomized;
5. policy/model Git SHA;
6. start and stop timestamps;
7. planned analysis and clustering;
8. guardrails and stopping rules;
9. assignment, delivery, crossover, censoring, and missingness counts;
10. effect, uncertainty, calibration, and absolute event rates;
11. decision taken;
12. what remains unidentified.

This makes later longitudinal analysis a comparison of explicit regimes and experiments, rather than an attempt to reverse-engineer why an evolving selector showed a sentence months earlier.

