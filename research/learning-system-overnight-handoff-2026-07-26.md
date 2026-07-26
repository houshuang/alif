# Learning-system overnight handoff

## Outcome

The broad learning/retention plan advanced across measurement, intake,
selection, acquisition graduation, and FSRS calibration. No production data,
deployment, scheduler weights, desired retention, intake, or live selector
policy changed.

One narrow behavior fix is implemented in the workspace:

- acquisition graduation now requires the current review to be successful
  (`rating >= 3`).

Everything else behavioral was rejected or deferred by replay:

- selector S1: rejected for due-coverage loss;
- selector S1b: rejected for hidden card workload and historical breadth loss;
- perfect-accuracy spacing gate: signal, but deferred because the replay cannot
  model queue workload and the retained-outcome cell is only 4 reviews;
- FSRS retuning: rejected on mixed legacy/relearning state and missing
  historical scheduler identity;
- rapid retry protocol v2: left frozen.

## Foundational invariant

Every word outcome in a shown sentence is equally valid, regardless of whether
the word was the selector's primary target or collateral. All new analyses and
replays count those rows equally. `credit_type` is used only for diagnostics or
the explicitly primary-only intake effort gate.

## Implemented workspace changes

### Runtime behavior

`backend/app/services/acquisition_service.py`

- requires `rating_int >= 3` for every graduation tier;
- stamps acquisition graduation policy v2, due-at-review,
  elapsed-since-last-review, and post-counters.

Historical evidence: 2/2,106 stored graduations occurred on rating 2. The fix
has an exact Box-3 3/4-correct → Hard regression test. No retroactive state
repair is proposed.

### Additive runtime telemetry

`backend/app/services/sentence_selector.py` and
`backend/app/routers/review.py`

- log base/repetition/returned card counts;
- log distinct due coverage, acquisition/maintenance due coverage, all-word
  breadth, and selection-reason counts.

`backend/app/services/fsrs_service.py`

- stamps FSRS library version, scheduler-policy version, desired retention, and
  parameter hash on new FSRS review rows.

### Reproducibility

`backend/pyproject.toml`

- changes `fsrs>=6.0.0` to production's `fsrs==6.3.1`. A live read-only check
  found the analysis venv on 6.3.0 and production on 6.3.1 with the identical
  parameter hash; this prevents future silent default drift without changing
  current production behavior.

`backend/scripts/optimize_fsrs.py`

- reads installed-library defaults instead of a stale hard-coded vector;
- computes post-lapse stability with both FSRS 6.3 forgetting branches;
- opens SQLite immutable/read-only;
- explicitly refuses direct deployment of exploratory output.

`backend/scripts/replay_fsrs.py`

- is labeled as a frozen April reproduction, not current calibration.

### Read-only analysis/replay commands

- `analyze_learning_system.py`
- `preview_intake_impact.py`
- `analyze_intake_cohort.py`
- `replay_selector_s1.py`
- `analyze_graduation_retention.py`
- `replay_acquisition_evidence.py`
- `analyze_fsrs_calibration_segments.py`

Each new decision-grade command verifies pinned database/baseline identities,
uses immutable SQLite, emits deterministic artifacts, and has regression
coverage.

## Main empirical findings

### Current system

- valid reviews: 40,845;
- acquisition stock: Box 1 138 (132 due), Box 2 53 (39 due), Box 3 8 (5 due);
- recovery gates: 127 actionable Box 1, 39 due Box 2, 805 strict main FSRS;
- only 28/87 analyzable sessions approximately completed;
- completed sessions had median 16 distinct rating-1 words.

### July 15 intake cohort

- 202 admitted words;
- admission→first review median 51.67h, p90 155.17h;
- 36.6% first-reviewed by day 1, 76.7% by day 5;
- at 10.46 days: 88 ever graduated, 96 acquiring, 24 suspended;
- 614/780 review rows (78.7%) were collateral and fully valid.

Immediate admission creates a first-teach queue; it is not immediate learning.

### Selector

S1 scanned past unserviceable priorities but lost 2.42 due words/request on
average and regressed 6/12 paired requests.

S1b preserved/gained base due coverage (+7.75/request, 0/12 regressions) but
acquisition repetition added 6.75 returned cards/request and grew 11/12
requests. On older historical material it lost five distinct presented
words/request on average.

Conclusion: pause selector policy changes until the new telemetry can constrain
a genuinely workload-neutral candidate.

### Acquisition graduation

Recent observed 3-day sentence success among delivered follow-ups:

- elapsed interval: 33/42;
- first correct: 13/18;
- high accuracy: 8/10;
- perfect accuracy: 7/12;
- standard: 5/5.

A prior-day-success gate would defer 9/23 perfect-accuracy, 0/17 high-accuracy,
and 9/187 total historical decisions. Deferred observed 3-day recall was 2/4.
This is a signal, not enough evidence to add workload to the acquisition queue.

### FSRS calibration

The first calibration used strict Alif success (`rating >= 3`) against FSRS
retrievability. The corrected audit reports both:

- strict success: predicted 89.9%, observed 78.1%, gap -11.8pp;
- FSRS recall (`rating >= 2`): predicted 89.9%, observed 82.9%, gap -7.0pp.

The mathematical FSRS gap is:

- post-acquisition: -4.1pp over 7,001 rows;
- legacy/untracked origin: -22.5pp over 1,316 rows;
- Relearning state: -14.5pp over 1,616 rows;
- within one day of due: -5.6pp.

Lateness matters but is not the whole problem. A single global retune would mix
very different populations.

## Validation completed

- generated artifacts reproduced byte-for-byte on fresh double runs;
- pinned databases remained byte-identical;
- artifact checksum manifests verify;
- focused combined suite: `230 passed, 1 deselected`;
- Python compilation passed for new/changed analysis commands;
- installed environment dependency check: no broken requirements;
- targeted diff whitespace check passed.

The full repository test suite was not rerun overnight. An earlier attempt in
this work stopped at an existing live-network crawl after 511 passing tests and
no failures. The focused suite covers every runtime area changed here.

## Proposed next steps, in order

1. Review the workspace diff before creating commits. The worktree contains
   unrelated user edits; isolate learning-system files carefully.
2. Split any PRs by concern:
   - analysis/replay foundation;
   - rating-2 graduation correctness + acquisition telemetry;
   - FSRS exact pin + config telemetry/optimizer safety;
   - selector diagnostics and dormant replay-only candidates.
3. Before deploying the FSRS pin, read the server's installed package version
   and parameter hash. The pin is intended to freeze current behavior, not
   upgrade it accidentally.
4. Deploy the rating-2 success gate only with its additive policy telemetry and
   record the first production v2 timestamp.
5. Let protocol-v2 rapid retry run untouched to its planned coarse readout.
6. Collect selector diagnostics long enough to learn actual base/repetition
   workload before designing S1c.
7. Build a full state replay from clean telemetry boundaries:
   - box transitions and due dates;
   - counter-neutral retry semantics;
   - perfect/high graduation spacing candidates;
   - selector delivery and acquisition stock displacement.
8. For FSRS, run rolling-origin comparisons separately for:
   - post-acquisition Review-state cards;
   - legacy/untracked cards;
   - Relearning/old-lapse recovery.
9. Compare higher desired retention, state repair, and recovery-specific
   treatment under an equal learner-minute budget. Do not lower desired
   retention while observed recall trails prediction.
10. Use retained sentence success at ≥1/3/7 days, completion, distinct
    obligations served, and acquiring/FSRS debt as joint outcomes.

## Open questions and risks for another agent

1. Can the server's historical/current FSRS package and parameter identity be
   recovered from deployment logs or an installed environment?
2. Do the 19 pre-window unexplained acquisition-counter discontinuities come
   entirely from known legacy canonical-routing/state-repair events?
3. Can a full selector/state simulation reproduce actual sessions without
   synthetic sentence-quality assumptions dominating the result?
4. What is the causal effect of a prior-day perfect-accuracy gate after
   controlling for word difficulty, source, and delivery probability?
5. Is legacy FSRS overconfidence best handled by state repair, higher retention,
   selector urgency, or a dedicated recovery model?
6. Does rating 2 mean “recalled” consistently enough for FSRS state updates,
   even though it correctly remains a strict-learning failure?
7. Can future intake previews forecast admission→first review and retained
   graduation under current capacity without treating staged admission as
   learning?
8. How should selector workload be budgeted when one chosen sentence generates
   later acquisition repetitions?
9. Are policy/version stamps preserved through undo, offline sync, and every
   non-sentence review path?
10. What minimum sample/readout duration makes protocol-v2 retry decisions
    robust for older lapsed words specifically?

## Rollback posture

No schema migrations or historical data rewrites were made.

- S0 remains the default selector policy.
- The graduation fix is one condition.
- Telemetry keys are additive JSON.
- The FSRS version pin is one dependency line.
- Every generated artifact is removable without runtime effect.
- No deployment occurred.
