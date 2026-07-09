# Return recovery and next-phase learning analysis — 2026-07-09

## Decision summary

The vacation break did not reveal a motivation problem. It exposed three places where Alif
could turn a normal interruption into avoidable learning cost:

1. the true-new intake gate measured acquisition debt but not a sustained FSRS backlog;
2. eligible leeches could re-enter in a burst, then be judged immediately against the bad
   history that caused their suspension;
3. active imported stories were treated as curriculum even when they had not been selected
   as the learner's target text.

The evidence supports a narrow recovery policy rather than a redesign:

- add **750 strict main-lane FSRS cards due** to the existing earned `0 / 8 / 30` intake
  gate;
- admit at most **8 leech reintroductions per UTC day**, only while actionable Box 1 is
  below 20, due Box 2 is below 30, and strict main-lane FSRS debt is below 750;
- judge a reintroduced leech only on reviews since its current reintroduction began, with at
  least **5 fresh observations** before a new suspension verdict;
- give the strong imported-story priority only to an active story explicitly marked
  `metadata_json.curriculum_role = "primary"`;
- run a reading-only, no-extra-work **exact-surface retrieval pilot** for morphology-related
  yellow marks while leaving the canonical lemma, FSRS rating, and due date unchanged.

No Tier E, Tier 0, mature-collateral, Quran-card, or historical-data policy is changed. There
is not yet an authorized contemporary literary text in the repository, so no synthetic
"contemporary lane" is activated.

## Evidence and reproducibility

The analysis combined:

- the current code, documentation, git history, research corpus, and append-only experiment
  log;
- a consistent online backup of the production SQLite database at
  `2026-07-09T21:17:48Z`;
- recent production interaction/activity logs copied for read-only analysis;
- event-level joins across `review_log`, acquisition state, leech interaction events,
  sentence mappings, stories, and form statistics;
- threshold replays and the existing calibrated multi-day simulator.

Snapshot: `/tmp/alif_next_phase_20260709.db`, SHA-256
`d4fcf670a2d62054de52b3d04f6317412622b10c7e44d4dd79b6764527229f05`.
The production snapshot and logs were not modified.

Definitions used here:

- **strict actionable FSRS debt**: due `known | learning | lapsed` cards, excluding function
  words, inert categories, and a variant whose canonical already has a known/learning row;
- **main lane**: acquisition plus non-artifact/frequent FSRS vocabulary under the shared
  frequency-lane classifier;
- **primary card**: `review_mode='reading' AND credit_type='primary'`;
- **correct recall**: rating at least Good (`rating >= 3`);
- **yellow**: a review with `was_confused=true` (the learner knew it after seeing the
  translation, but did not retrieve it from the Arabic form in context).

## State on return

| Signal | Production value |
|---|---:|
| Strict actionable due | 1,180 |
| FSRS due | 1,006 |
| Acquisition due | 174 |
| Strict main-lane FSRS due | 951 |
| Actionable/protected Box 1 | 147 |
| Due Box 2 | 19 |
| Total acquiring | 177 |
| Acquiring with prior leech history | 121 |
| Primary reading cards on 2026-07-09 | 41 |
| Primary-card accuracy on 2026-07-09 | 78.0% |

The already-corrected recovery gate therefore yields an intake budget of zero on the return
day. This is desirable: the learner is working through retrieval debt before adding more
encoding work.

The backlog is not mainly a sentence-supply failure. **98.8%** of due FSRS words have at
least one reviewable sentence; only 55 of 1,006 are in the slow/artifact lane. From the
checkpoint before the main return, 60 primary cards cleared 170 distinct FSRS obligations,
but arrivals, failures, and relearning reduced the net actionable-debt improvement to 89.

Cold primary recall over the preceding 30 days was:

| Demonstrated gap | Correct / total | Recall |
|---|---:|---:|
| under 1 day | 86 / 92 | 93.5% |
| 1–3 days | 71 / 84 | 84.5% |
| 3–7 days | 73 / 97 | 75.3% |
| 7–14 days | 89 / 99 | 89.9% |
| 14–30 days | 79 / 113 | 69.9% |
| 30+ days | 48 / 61 | 78.7% |

The return-only FSRS sample was 32/43 (74.4%); gaps of at least 14 days were 21/31
(67.7%). These samples justify temporary recovery suppression but are too small to retune
FSRS itself.

## Why the FSRS recovery threshold is 750

Observed active-day main-lane FSRS checkpoints were normally 343–439, with an observed high
near 576. Roughly two sparse days reached 672; roughly five sparse days reached 806.

| Candidate threshold | Interpretation |
|---:|---|
| 650 | Only 13% above the observed healthy high; likely to fire on an ordinary heavy week |
| **750** | 30% above the healthy high; catches a sustained break without treating normal maintenance as recovery |
| 850 | Very conservative, but misses some genuine multi-day interruptions |

In the calibrated replay, the corrected acquisition gate already held true-new intake at
zero for seven days while total due moved `1045 → 975 → 872 → 758 → 575 → 522 → 434`;
intake resumed on day 8. Thresholds 650, 750, and 850 therefore behave identically for this
particular return because acquisition debt binds first. The 750 threshold protects a future
break in which the acquisition boxes happen to be healthy but mature FSRS debt is not.

It joins the existing earned budget rather than becoming an unconditional freeze: 40 good
primary cards can still earn 8 introductions, and 100 cards at at least 85% can earn the
full 30 when the learner demonstrably has capacity.

## Leech reintroduction: the largest correctness finding

Since 2026-05-15, logs contain 263 reintroductions. One repair day admitted 64 at once.
Replay of daily admission caps gave:

| Daily cap | Full-history p95 delay | Backlog on 2026-07-09 | Post-burst p95 delay |
|---:|---:|---:|---:|
| 4 | 15 days | 72 | 3 days |
| 6 | 11 days | 24 | 1 day |
| **8** | **7 days** | **0** | **1 day** |

Normal post-burst arrivals averaged 3.65/day and peaked at 10, making 8 a practical cap.
The debt gates matter more than the cap during a vacation: 42 words became eligible during
the break, so an 8/day cap alone would still have fed them all back into an already
overloaded acquisition queue.

More seriously, the current treatment often never received a fair trial:

- 262 reintroduction episodes were identified;
- 161 received a post-reintroduction review;
- 102/161 (63.4%) were suspended on that first review;
- 76/133 (57.1%) whose first review was Good were still immediately suspended;
- median delay from ReviewLog write to suspension was 16.8 milliseconds;
- among 109 paired reintroduction→suspension episodes, 102 had one fresh review, six had
  two, and one had four.

One observed word graduated through elapsed-interval Tier E after a correct long-gap review,
then was suspended 41 ms later because the leech check reused an old 3/8 window. This is
not evidence that Tier E is wrong; it is an episode-boundary bug.

The repair preserves lifetime counts for analytics, but leech detection uses only reviews
at or after the current `acquisition_started_at`. It returns "insufficient evidence" until
five fresh reviews exist, then resumes the existing last-eight, below-50% verdict on every
review. Reintroduction priority is lower `leech_count`, stronger frequency rank, then oldest
eligibility.

## Curriculum findings

All 3,944 production lemmas have `register=NULL` and `dialect=NULL`; current intake is not a
true contemporary/classical lane allocator. Before the guard, the top 100 candidates were:

- 57 legacy `textbook_scan` words;
- 40 words from any active imported story;
- 3 frequency-core words.

The two active imported stories are not suitable contemporary-literature targets, and their
stored mappings/glosses contain shifted associations. Active status is a reader visibility
state, not proof that a story is the chosen curriculum. After requiring the explicit primary
role, the same snapshot yields 57 textbook and 43 frequency-core candidates, with zero
ordinary imported-story words receiving the strong target-text tier.

The strongest measured contemporary candidate remains the opening of Ghassan Kanafani's
*Men in the Sun*: about 84% coverage in the June analysis and approximately 96.4% after 150
targeted gaps. *The Bamboo Stalk* reached only about 91.5% after 500. No authorized copy or
chapter is available locally, so activation requires learner-supplied/authorized Arabic
text. The existing *Collared Dove* material is a plausible secondary classical track
(85.9% known, 89.5% including in-progress, about 95.1% after 200 gaps), but its old token
mapping requires hardened reimport before activation.

A future 4:1 contemporary/classical-or-Quran split should divide **earned new intake only**;
due reviews must remain obligation-driven. Quran supply also needs curation and material
prewarming, and the learner-requested suspension of Quran verse cards remains in force.

## Yellow marks and the exact-surface pilot

Since 2026-06-03 there were 260 yellow events; the exact surface could be recovered for 251.
Correct boundary-punctuation, tashkeel, Quranic-form, tatweel, and alef normalization yields:

| Category | Events |
|---|---:|
| Citation-form slips | 68 |
| Definite article / pure proclitic only | 84 |
| Non-trivial conjugation or inflection | 99 |
| Historical surface unavailable | 9 |

Of the 99 non-trivial events, 90 were FSRS and 93 were collateral; only six were primary.
This explains why ordinary Hard scheduling does not reliably test the failed form: the
canonical lemma is brought forward, but the sentence selector may present another form and
the original failure was usually collateral.

Only 42/99 historical exact surfaces currently have a different reviewable sentence in
inventory. Natural same-form primary delivery was 15.8% by day 7 and 19.8% by day 14; among
the inventory-supported subset it was 26.5% and 31.2%.

### Pilot contract

- reading-mode, non-acquisition yellow FSRS events only;
- exclude citation forms, article/proclitic-only differences, passages with ambiguous forms,
  the original trigger sentence, explicitly failed LLM material, and forms without a
  different reviewable sentence;
- deterministic 50/50 assignment from review identity + canonical lemma + normalized form;
- persist the episode under reserved
  `UserLemmaKnowledge.variant_stats_json["__exact_surface_v1"]`; no migration;
- treatment reserves at most one **already-due** ordinary session slot and makes that
  canonical lemma primary in a different sentence containing exactly the failed form;
- control receives normal selection;
- never add a card, move a due date, change the learner's Hard rating, or add workload;
- pause treatment while the lemma is in acquisition/reintroduction;
- undo removes a trigger episode or reopens an undone outcome.

Two endpoints are recorded for both arms:

1. **intention-to-treat safety endpoint** — the first later primary reading review in any
   form, including rating, yellow status, form keys, and whether it happened to be exact;
2. **exact-form endpoint** — the first later primary reading review of the same form in a
   different sentence.

The first endpoint avoids conditioning retention comparisons on exact-form delivery, which
the treatment deliberately changes. The second measures whether the intervention actually
delivered and retrieved the target form.

At the observed event rate, four to five active weeks should give roughly 25 episodes per arm
for a delivery check. Retention should not be interpreted before eight to ten active weeks
unless the effect or harm is very large. Report assignment balance, 7/14-day delivery,
first-primary any-form clean recall, exact-form clean recall (`rating >= 3` and not yellow),
repeat yellow/red, and unchanged cards/session.

## What was deliberately not changed

- Tier E has only eight graduations and zero informative follow-ups after a one-day-or-longer
  scheduled interval; it remains observation-only.
- FSRS parameters were not retuned from a small, interruption-biased sample.
- Mature collateral credit was not changed.
- No production history was backfilled or rewritten.
- No existing story was marked primary, suspended, or deleted.
- No contemporary/Quran quota was fabricated without a real target text and safe inventory.
- General `variant_stats_json` display keys retain their historical hamza-sensitive format;
  stronger normalization is scoped to comparison and the reserved pilot data, avoiding
  parallel counters.

## Next decision points

1. Let recovery mode drain the current queue; inspect strict debt, primary accuracy, and
   reintroduction admissions after several active days.
2. After at least 50 resolved post-change leech episodes, compare fresh-episode accuracy,
   graduation, re-suspension, primary-card cost, and cold 7–30-day recall.
3. Review the exact-surface pilot after 4–5 active weeks for delivery and after 8–10 for
   retention/safety.
4. Obtain the learner's selected, authorized contemporary text. Run the existing readiness
   analyzer and hardened import before marking its story metadata as primary curriculum.
5. Only then implement an earned-intake lane allocator, with contemporary literature as the
   larger lane and curated Quran/classical vocabulary as the smaller lane.
