# Learning-system update — 2026-07-28

*Updated 2026-07-29 with PR #232 hardening, immutable-snapshot rehearsal, and
the separately approved exact three-lemma inventory curation.*

## Executive verdict

The vacation created a backlog shock, not a loss of the learner's underlying
ability. Recovery is materially working:

- strict main-lane FSRS debt is down from **912 on July 10 to 707**;
- actionable Box 1 is down from **136 to 85**;
- the latest session-start debt is **844**, versus **1,111** at the start of
  July 10, despite 236 deliberate Bookifier/Kalīla additions in between;
- recent-word recall has recovered, and July 22–28 word accuracy is back to
  roughly **87%**.

Recovery is not finished. Due Box 2 has risen from **17 to 39**, and all 91
Box-1 rows are due. The system is correctly keeping automatic intake closed.
The largest remaining avoidable retention cost is **lateness**, not weak FSRS
weights. Thirty-day primary non-acquisition recall is **80.3%**: the under-7-day
bands remain healthy, while retrieval weakens sharply after seven days.

For this single highly motivated learner, the fastest safe path is therefore:

1. stop deliberate batch intake until the existing acquisition stock clears;
2. distribute practice more evenly across days instead of relying on two very
   large catch-up days;
3. start reading the physical *Momo* now, with support, rather than waiting for
   an unreliable OCR-derived 95% number;
4. with the hardened corpus path deployed and the three reviewed dictionary
   gaps now curated, separately prepare a reviewed subset of the already
   imported authentic *Momo* sentences; activation is a later decision because
   the current pool has no headroom;
5. leave FSRS weights and graduation thresholds alone until the July 27
   policy changes have mature follow-up.

No learning-history or scheduler-state backfill is warranted. The analysis
itself was read-only. A separately approved July 29 dictionary-only operation
is recorded below; it changed no learner or corpus state.

## Scope and provenance

This is a read-only update to the July 9/10 return analysis and the July 20/25
post-injection work.

- Production SQLite online backup captured approximately
  **2026-07-28 12:20 UTC**.
- Snapshot size: **121,786,368 bytes**.
- Snapshot SHA-256:
  `4ed913f4134fbdc06560e4a5acdae38f56200283eb20d4e8e7a3c863fc121345`.
- `PRAGMA integrity_check`: `ok`.
- Window: half-open
  `[2026-07-10T00:00:00Z, 2026-07-28T12:20:00Z)`.
- Production interaction logs for July 10–28 were copied read-only and every
  supplied checksum passed.
- Production was independently verified at exact revision
  `0f3749044aa8e8a87c3d3c07421e49d1fb2c2ec8`
  (PR #231), with `alif-backend` active since
  **2026-07-27 10:20:58 UTC**.
- The database was not opened through the application writer. The deterministic
  baseline ran against `mode=ro&immutable=1`.

Methods:

- `analyze_learning_system.py`;
- policy-aware segmented FSRS calibration;
- fixed-horizon Bookifier/Kalīla cohort analysis;
- graduation-retention and failed-word follow-up analysis;
- protocol-v2 retry and token-evidence log reconciliation;
- current service-level `recovery_status()` on the pinned snapshot;
- context-sensitive Rijāl and *Momo* readiness passes;
- code, git-history, experiment-log, research-index, and production-cron audit.

The deterministic baseline reports 6,095 valid word outcomes. A post-PR-#231
measurement drift described below excluded six outcomes that production now
treats as lexical content; this is too small to change any conclusion, but it
must be corrected before the next formal baseline.

## 1. Recovery trajectory

| Checkpoint | Actionable Box 1 | Due Box 2 | Strict main FSRS |
|---|---:|---:|---:|
| Jul 9 return | 147 | 19 | 951 |
| Jul 10, after PR #208 | 136 | 17 | 912 |
| Jul 14 | 113 | 15 | 874 |
| Jul 20, after the 202-word add | 217 | 52 | 869 |
| Jul 25 | 127 | 39 | 805 |
| **Jul 28** | **85** | **39** | **707** |

The July 28 runtime gate state was:

- recovery active;
- intro budget **0**, introductions used **0**;
- 18 primary reading cards so far that day at 94.4% accuracy;
- 48 eligible leeches correctly deferred because acquisition capacity is still
  closed;
- strict main FSRS below its 750 limit for the first time, but Box 1 and Box 2
  still binding.

Current acquisition stock:

| Box | Total | Due | Zero correct | Median age | p90 age |
|---|---:|---:|---:|---:|---:|
| 1 | 91 | 91 | 10 | 13.25d | 41.86d |
| 2 | 49 | 39 | 0 | 13.23d | 44.42d |
| 3 | 9 | 2 | 0 | 36.11d | 36.29d |

This is a real dig-out, not merely rescheduling debt out of sight. The
interaction log's total-due count fell from 1,154 to 903 during the July 25
work and from 959 to 831 during July 27. Current strict actionable debt is
approximately 893 words: 132 acquisition obligations plus about 761
non-shadowed FSRS obligations.

## 2. Learning throughput and present quality

From July 10 through the cutoff:

- 19/19 UTC days had review activity;
- 1,074 reading sentence cards;
- 217 graduations;
- median time to graduation 10.35 days, p90 34.9 days;
- current states: 2,546 known, 82 learning, 149 acquiring, 68 lapsed,
  300 encountered, and 158 suspended;
- 5,108 rating-3 outcomes, 129 yellow/rating-2 outcomes, and 858 rating-1
  outcomes in the frozen baseline.

The post-injection accuracy dip has recovered:

| Period | Reading cards | Approx. word outcomes | Overall | Primary | Collateral |
|---|---:|---:|---:|---:|---:|
| Jul 10–14 | 217 | 1,180 | 84.3% | 77.1% | 85.8% |
| Jul 15–21 | 403 | 2,203 | 79.8% | 69.4% | 82.0% |
| Jul 22–28 | 454 | 2,576 | **86.7%** | **77.9%** | **88.5%** |

Primary targets are selected because they are harder. The primary/collateral
gap is diagnostic and does not invalidate collateral retrieval evidence.

The learner is exceptionally active, but volume is bursty:

- median 35 reading cards/day;
- July 25 and 27 supplied **69.6% of the last week's word-review volume**;
- the five largest days supplied 58.3% of the whole window's volume.

The approximate planned-session completion rate is only 34/86 (39.5%).
That label is not a motivation metric—the learner often starts another
session—but it matters when adding automatic wrap-up and retry work.
Abandoned sessions had a median of six answered sentences; automatic wrap-ups
occurred 14 times and contained a median of eight cards.

### The dominant retention tax is lateness

Policy-aware due-FSRS calibration:

| Lateness | Reviews | Predicted recall | Unaided recall |
|---|---:|---:|---:|
| 0–1d | 418 | 96.2% | **90.4%** |
| 1–3d | 280 | 90.7% | 80.7% |
| 3–7d | 257 | 85.9% | 77.8% |
| 7–14d | 211 | 86.0% | **67.3%** |
| 14+d | 463 | 82.3% | **62.4%** |

Overall unaided due recall was 75.8% against 88.3% predicted. Scheduler-policy
recall, which uses the actual applied FSRS rating, was 79.0%. Recent stamped
rows are better (81.9% unaided, n=249) than inferred pre-v2 history (74.7%,
n=1,380), but the new cell is small and composition-changing.

Across all primary non-acquisition retrievals in the 30-day view, recall is
80.3%. The sub-seven-day bands are compatible with useful retention; the
weakness is concentrated after seven days. That supports a steadier floor of
roughly 40–60 reading cards on most days, not a new vocabulary batch.

This does **not** support a global FSRS retune. Mature cards remain much later
than fragile cards, the old backlog dominates failures, and the scheduler
policy itself changed on July 27.

The first FSRS outcome after graduation is still a watch item: 72.9% unaided
recall versus 89.6% predicted (n=107). PR #229 corrected the root-boosted
graduate scheduler only on July 27, so the relevant prospective outcomes do
not yet exist.

## 3. Bulk intake was the main self-inflicted bottleneck

The automatic recovery gate behaved correctly. Explicit `/add-batch` did not:
it still calls `introduce_word(..., enforce_daily_cap=False)`.

### July 15 Bookifier cohort

This source cohort contains two deliberate projects and must not be labelled
as a pure *Momo* cohort.

- 202 admitted in one day;
- 201/202 reviewed by the cutoff;
- 106 ever graduated;
- current outcome: **100 learned/retaining** (81 known, 18 learning, 1 lapsed),
  69 acquiring, and 33 suspended;
- acquisition accuracy 63.5% over 706 rows;
- median admission-to-first-review **53.0 hours**, p90 **157.4 hours**;
- success-conditioned median graduation 6.47 days;
- collateral supplied 733/952 observed outcomes.

At day 5, only 35 had graduated and 49 were still never reviewed. The cohort
eventually moved, but first-teach latency is the clearest cost of admitting far
more than one learner can promptly retrieve.

### July 21 Kalīla cohort

- 34 admitted while recovery was still active;
- current outcome: **16 learned** (11 known, 5 learning), 9 acquiring, and
  9 suspended;
- acquisition accuracy 54.7% over 139 rows;
- median first review 8.61 hours, p90 26.45 hours;
- one word remains never reviewed.

The 26.5% one-week suspension fraction shows that this is a genuinely hard
classical cohort, not evidence that the learner should receive more input.

### Consequence

The highest-value intake change is not another priority score. It is to make
explicit text imports capacity-aware: preview them, retain the full
token-frequency-ranked queue, and stage admission in small tranches rather
than turning every approved target into due Box-1 debt immediately.

## 4. Reading targets: begin now, but distrust the headline coverage card

### *Momo*

The current stats service prints 91.1% readable and 94.4% including
in-progress vocabulary. That is too optimistic for a decision boundary:
the generic resolver makes 5,404 CAMeL last-resort mappings and 3,555
ambiguous choices on the OCR, including obvious context errors such as
`رأسه→رئاسة`, `التي→آلة`, and `يجب→أجاب`.

Safer sensitivity analyses give:

- whole book: approximately **89–90% readable now**;
- including acquiring/lapsed/encountered: approximately **92–93%**;
- cleaned chapter 1 subset: **88.8% now / 92.8% including progress**.

The OCR source itself is not clean: nine pages contain substantial English or
model commentary, printed page 26 remains degraded, and 18 page IDs are absent.
The physical Arabic copy is the authoritative reading surface.

A text-linked snapshot finds 185 Bookifier lemmas relevant to *Momo*:
71 known, 16 learning, 61 acquiring, 1 lapsed, and 36 suspended. The top
remaining mapped gaps are mostly suspended words already imported
(`اضطر`, `مدرع`, `هب`, `أطلق`, `انتبه`, `بصر`, `انحنى`, `اتضح`, `اتخذ`).
Importing them again or adding another general batch would add no value.

The book is below effortless extensive-reading coverage, but prior familiarity
with the story and a physical copy make it suitable for supported reading now.
The better next measurement is actual lookups/yellows over two or three
chapters, not another OCR-derived global percentage.

### Rijāl fī al-shams — Abū Qays

The v3 bilingual EPUB contains 89 Arabic blocks and 1,807 production tokens.
After context-correcting known resolver errors and treating recurring names as
inert, practical coverage is approximately **85% now / 89% including
in-progress**.

It remains a useful short diagnostic with adjacent English, but not a safe
generic Alif import. Running-text lookup still misresolves, among others,
`الرمل→أرمل`, `رائحة→راح`, `يحلق→حلق` “throat”, `تراه→راوند`, and
`رأسه→رئاسة`. Qays, Basra, the Tigris, Euphrates, and Jaffa also need explicit
proper-name treatment.

Recommended reading order:

1. read the Abū Qays bilingual chapter once as a short diagnostic;
2. begin physical *Momo* chapter 1, attempting a sentence or paragraph before
   looking anything up;
3. use actual repeated lookup/yellow data to choose any future small queue;
4. keep a roughly 4:1 contemporary/classical reading-time split if both goals
   remain equally motivating—without importing a new classical batch now.

## 5. Recent experiments: what is and is not learned yet

### Yellow/rating 2

The learner's account is supported. Since July 10, 129 yellow rows were only
2.2% of reading outcomes, falling from 3.2% early in the window to 1.7% in the
latest week. At least 67/130 correlated token events are explicitly derived,
inflected, or enclitic forms; another 33 have decomposition structure.

PR #227 now correctly treats yellow as failed unaided retrieval: product rating
2 remains stored, but FSRS prospectively applies `Again` at 0.90 desired
retention without relearning steps. This supersedes the July 10 assumption that
yellow should remain ordinary FSRS Hard. It needs no historical backfill.

### Rapid retry and established-lapse recovery

- 141 immediate retry quiz rows since July 25;
- 126/141 immediate successes (89.4%);
- 29 acquisition successes correctly blocked from box/graduation credit;
- zero quiz-driven graduations.

The safety boundary works. Immediate success is working-memory evidence, not
retention.

In the last seven days, failed-word follow-up delivery reached 31.6% by ten
minutes, 37.6% by two hours, and 54.7% by 24 hours. Established lapses reached
37.7%, 45.2%, and 65.9%. Those cells are right-censored and observational; no
seven-day treatment outcome is mature.

### Exact-surface pilot

The July 10 randomized morphology pilot is not merely immature; it is stalled:

- 5 episode assignments (1 treatment, 4 control);
- 42 otherwise eligible triggers rejected because no different reviewable
  sentence contained the exact form;
- zero any-form primary outcomes;
- zero exact-form outcomes.

Later reviews of assigned lemmas were collateral, while the safety endpoint
requires a later primary retrieval. At this delivery rate, waiting will not
reach the planned sample. The pilot needs a delivery-path audit before any
decision to generate more inventory or continue the experiment.

### Token-level form/tashkīl evidence

The new prospective protocol has only 625 token rows across 107 reviews.
There are four selected causes: two “unfamiliar form” and two “mixed up.”
Raw unvocalized/vocalized accuracy is confounded because stronger words receive
faded tashkīl. No cause-specific scheduling or display change is justified.

## 6. Code, data, and documentation findings

### Measurement drift after PR #231

`book_coverage.py` and `analyze_learning_system.py` still use the pre-override
bare-form/category classifier instead of the mapped-lemma
`is_function_word_lemma(..., function_word_override)` policy.

Observed impact at this cutoff is small but real:

- deterministic analysis reports strict main FSRS 707; production runtime
  reports 708;
- six window review rows for lexical homographs/onomatopoeia were excluded by
  the frozen analyzer even though the deployed review path treats them as
  content;
- the docs claim shared classification, so code, metrics, and documentation
  currently disagree.

The *Momo* card's cohort is also source-wide:
`compute_source_cohort(source="bookifier")` reports all 236 Bookifier rows,
including Kalīla/other project imports, rather than the 185 rows actually
linked to the *Momo* token map. The coverage percentage and cohort funnel are
different objects and should not share a source-only identity.

### Authentic *Momo* corpus never activated

On July 15, 243 hand-vetted *Momo* sentences were imported as
`source='corpus'`, `kind='momo_book'`, inactive until enrichment. At the
July 28 cutoff:

- 243/243 inactive;
- 0 translated;
- 0 mapping-verified;
- 0 quality-reviewed;
- all 243 currently touch active vocabulary and are eligible for Step A2.

This is not ordinary pipeline delay. `update_material.py` keeps corpus
enrichment off unless `--run-corpus-enrichment` or
`ALIF_RUN_CRON_CORPUS_ENRICHMENT=1` is supplied. Production's
`/opt/alif-update-material.sh` enables pregeneration and lemma enrichment but
not corpus enrichment. The every-three-hour cron log contains **578 explicit
Step-A2 skips**, including the latest July 28 run. Therefore the experiment
log's “within 1–3 weeks” expectation was impossible.

This is a genuine missed content backfill, but the then-deployed PR #231 Step
A2 must **not** simply be enabled:

- its query is unscoped and unordered; 343 corpus rows are currently eligible
  (100 older generic rows + 243 *Momo*), and the first 50 selected are all old
  rows;
- its `2000-01-01` claim sentinel is not recovered by the NULL-only query;
  production already has 1,707 stranded sentinel corpus rows;
- remapping recreates every `SentenceWord` with `is_target_word=False` and
  does not repair `Sentence.target_lemma_id`; all 6,365 historically enriched
  corpus rows have zero target flags and 202 have a stale target no longer
  present;
- the active pool is already 1,961 against a 1,950 retirement target, so
  activating all 243 would cause near one-for-one churn on later cron passes;
- the 243 rows have no story/page/order metadata and are authentic isolated
  snippets, not a sequential chapter path;
- A2 creates English and verifies mappings against it, but does not run or
  stamp a separate translation/naturalness quality review.

Approximately 104 *Momo* rows touch a currently due word; 34 contain an
acquiring word and are correctly blocked, leaving 70 potential FSRS-demand
rows spanning 68 distinct due lemmas before final verification. Their value is authentic
target-text transfer, not repair of an urgent sentence drought.

### PR #232 hardening and copied-snapshot rehearsal

The hardened implementation uses three explicit non-reviewable dispositions: Jan 1,
2000 is a transient claim; Jan 2 is a durable inventory/mapping blocker; Jan 3
is a durable completed naturalness/translation rejection. A bounded,
scope-specific cursor preflights prospective mapping and inventory risk before
LLM calls. Preparation runs Arabic/translation QA early, requires explicit
verifier verdicts for every row and ambiguity, retries exact rows, and
compare-and-sets the final target/mapping write. Full vocalized/hamza-preserving
Arabic identity is considered before lossy normalization. The corpus path never
creates lemmas.

The only live rehearsal used a disposable working copy derived from the pinned
immutable production snapshot. Three temporary reviewed lemmas were added only
to that copy to model already approved inventory. Exact rows 52182 and 52316
prepared cleanly; row 52352 received a terminal naturalness rejection. No row
activated, all learner-state tables remained unchanged, and production data was
never opened for writing.

A deterministic replay over all 243 *Momo* rows found **235 inventory-complete**.
The remaining eight rows contain nine standalone OCR-spaced `و` tokens. They
need separately confirmed source-text normalization and exact-ID Jan-2 retry,
not vocabulary backfill. Preparation and activation remain mutually exclusive.
With 1,961 active rows against the 1,950 ceiling, activation capacity is zero.

### July 29 operational update — exact inventory curation complete

PR #234 deployed as `e20148b7` and added exactly the three persistent scaffold
lemmas modeled in the copied rehearsal: #4530 `كُلِّيّ`, #4531 `إِلٰه`, and
#4532 `فَعَلَ`. All are gated, canonical, and linked to the independently
reviewed existing roots `ك.ل.ل` (#198), `ء.ل.ه` (#809), and `ف.ع.ل` (#103).
The importer now NFC-normalizes vocalized identity, fails closed under exact
`--only` scope, pins reviewed roots, and resumes compatible ungated rows after
an interrupted gate run.

The immediate production backup is
`/opt/alif-backups/alif_pre_pr234_momo_inventory_20260729.db`
(`sha256=fd1a9eeeb81f36a80242e899c1172d3fbf1b4d325ea4722de45dffa2e61f3183`,
integrity `ok`). Roots, ULK, ReviewLog, frequency-core, and sentence mappings
were unchanged; none of the three new IDs has learner or corpus links.
ActivityLog #3879 records the operation. All 243 *Momo* sentences remain
inactive, untranslated, mapping-unverified, and quality-unreviewed; the corpus
flag remains absent and the active pool is exactly 1,950. This completes only
the three-lemma inventory boundary. It does not authorize preparation,
blocked-row retry, source normalization, or activation.

### Stored target drift is a separate content-repair boundary

The immutable snapshot also contains 22 active sentences whose stored canonical
target is absent from every mapped word; 18 are currently reviewable:
3471, 43996, 45126, 45565, 48093, 48095, 48892, 50975, 50979, 51204,
51529, 51928, 54046, 54119, 54425, 54499, 54513, and 54549.
Six have been shown. Only sentence 50979 has stale-target primary credit:
three historical `ReviewLog` rows name target lemma 561 while the visible
`قدمه` token maps to lemma 3081.

Those observations are valid historical learner evidence and must not be
rewritten. PR #232 prospectively repairs target bookkeeping atomically whenever
mapping correction changes the target. Any exact content repair for these 18
production rows remains a separately reviewed and confirmed data operation.

### Other outstanding records

- PR #232 is deployed as `094ee1c1`. The backend was active and `/api/stats`
  returned HTTP 200 after restart; the corpus-enrichment cron flag remained
  absent. Immediate before/after counts were identical: 4,261 lemmas, 3,304
  ULKs, 65,236 `ReviewLog` rows, 12,857 sentence reviews, 1,974 active
  sentences, and all 243 *Momo* rows still inactive, untranslated, and
  unverified.
- Three reviewable `ستين→سِتّ` mappings and one inactive `تفوق→فَاقَ` residue
  remain from the July 15 collision repair. They are small and not a learning
  bottleneck.

## 7. Approved next phase — current status

### Phase A — restore trustworthy measurement (no learning behavior change)

1. Make the deterministic analyzer and book coverage use the deployed
   lemma-aware function policy; resolve the onomatopoeia definition explicitly.
2. Scope target-book cohort funnels by token-map/goal identity, not the shared
   `bookifier` source.
3. Add regression fixtures for the eight lexical homographs and mixed
   Bookifier sources.
4. Re-run the frozen July 28 baseline and update the research index,
   changelog, and deployment record.

### Phase B — harden, prepare, then activate authentic *Momo* material cautiously

1. **PR #232 code-only release:** exact scope, cursor-progressive
   preflight, early QA, explicit verifier verdicts, exact retry semantics,
   compare-and-set writes, full Arabic identity, and no corpus lemma creation.
2. **Copied immutable-snapshot rehearsal complete:** three reviewed temporary
   lemmas in the copy; 52182/52316 prepared, 52352 terminally rejected, zero
   activation, unchanged learner tables, and no production write.
3. **Full replay complete:** 235/243 inventory-complete. Separately confirm
   source normalization for the eight rows with nine OCR-spaced standalone `و`
   tokens; this is not a vocabulary backfill.
4. **Exact inventory curation complete:** PR #234 added only `كُلِّيّ`,
   `إِلٰه`, and `فَعَلَ` as dictionary-only scaffold rows with reviewed roots.
   No ULK, FCE, review, sentence mapping, preparation, or activation changed.
5. Back up production and separately confirm one small *Momo*-only
   **preparation** tranche with activation zero; code deployment alone does not
   authorize or run it.
   Inspect every Arabic sentence, translation, mapping, target, and disposition.
6. Plan activation separately with preparation set to zero. Production is now
   exactly at the default 1,950 corpus ceiling, so capacity remains **zero**;
   do not activate until an independently reviewed headroom/retirement decision
   creates capacity.
7. Continue only in inspected small tranches. Do not globally enable Step A2,
   and keep the 1,707-row generic sentinel debt as a separate decision.

### Phase C — learner protocol for the next 10–14 days

1. No deliberate vocabulary batch while actionable Box 1 ≥20 or due Box 2
   ≥30; ideally wait until recovery mode fully clears.
2. Aim for a dependable floor of roughly **40–60 reading cards on most days**,
   split into smaller sessions, before optional extra work. The goal is to
   prevent 7+ day lateness, not to cap motivation.
3. Read Abū Qays once, then begin physical *Momo*. Record actual lookup/yellow
   burden by chapter; add no words merely because OCR calls them gaps.
4. At the next 7-day boundary, recheck Box 1/2, the July 15 and Kalīla cohorts,
   retry delivery, session completion, and first post-graduation outcomes.
5. Audit or retire the exact-surface pilot; do not leave it nominally running
   with no attainable endpoint.

## 8. Backfill decision

| Candidate | Decision |
|---|---|
| Historical reviews/ratings | **No backfill** |
| Existing FSRS cards after rating-2 policy change | **No backfill; prospective boundary is correct** |
| Graduation cards after PR #229 | **No backfill; monitor new graduates** |
| Rapid-retry counters | **No backfill** |
| More *Momo* vocabulary | **No batch. Exactly three reviewed dictionary gaps were curated on July 29 without learner intake; add nothing else** |
| Kalīla/classical vocabulary | **No new batch during recovery** |
| 243 authentic *Momo* sentences | **235 are preparation-ready after deployment; eight need separately confirmed source normalization for nine OCR-spaced `و` tokens. Prepare only reviewed small tranches; activation is separate and currently has zero capacity** |
| 18 reviewable stale-target sentences | **No history rewrite. Prospectively fixed in code; any exact production content repair is separately confirmed** |
| Historical learner state for corpus/target fixes | **No backfill; preserve all observations** |
| *Momo* OCR | **Clean nine contaminated pages + printed p26, but physical reading need not wait** |
| Rijāl learner state/import | **No; fix contextual mappings/names before any future rehearsal** |

## Bottom line

The system is doing the important thing correctly: it is converting an
extraordinary backlog while preserving high short-gap recall. The best speed
gain is not a more aggressive scheduler. It is to stop manufacturing lateness,
stage target-text intake to human capacity, and move the learner into the
actual book now. The proposed technical work is primarily measurement repair
plus deployment of the now-rehearsed corpus hardening, followed by a separately
confirmed small preparation tranche of already hand-vetted material. Activation
must wait for a separate pool-headroom decision, and no historical learner-state
rewrite is warranted.
