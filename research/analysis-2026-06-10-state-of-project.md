# State of the Project — Full Code + Learning-Analytics Review (2026-06-10)

> **⚠️ Addendum (same day):** the Part 1 headline ("north-star collapse") was corrected by a
> follow-up event-level analysis — the grad−suspension *flow* metric conflated stock and
> flow; the known-word *stock* actually grew +89 in the last week and the new-word funnel is
> healthy. See `analysis-2026-06-10-two-week-ground-truth.md` for the corrected picture and
> revised priority order. The process findings (unlogged cap bypass) and R1–R5 stand.

**Scope.** Requested as a holistic review after ~120 days of daily use and 100+ days of
iteration: (1) learning analytics from the production DB, (2) code-health audit of backend +
frontend, (3) prioritized recommendations for pedagogy/algorithms and code cleanup —
cross-checked against the experiment log, IDEAS.md, and the 2026-05-22 stale-ideas triage so
nothing already tried/rejected is re-proposed.

**Data sources.** Prod `alif.db` (read-only; 53,136 reviews, 2,837 tracked words, 122-day
streak), `scripts/learning_analysis.py` full run, custom supplementary queries (weekly net
growth, accuracy splits, pool aging, leech outcomes, due coverage), prod LLM call logs,
`/var/log/alif-update-material.log`, three parallel code-audit passes over `backend/` and
`frontend/`.

---

## Part 1 — Headline finding: the north-star metric broke in week 22

**Net known-word growth (graduations − leech suspensions) collapsed from ~90–106/week to
11–41/week:**

| week | graduated | suspended | net | FSRS retention | acquisition accuracy |
|------|-----------|-----------|-----|----------------|----------------------|
| W15 | 91 | 0 | 91 | — | 68.7% |
| W16 | 86 | 0 | 86 | — | 67.0% |
| W17 | 92 | 1 | 91 | 94.0% | 79.8% |
| W18 | 105 | 3 | 102 | 94.1% | 81.0% |
| W19 | 121 | 15 | 106 | 93.3% | 78.8% |
| W20 | 53 | 5 | 48 | 90.8% | 72.8% |
| W21 | 87 | 15 | 72 | 86.5% | 70.7% |
| W22 | 54 | 43 | 11 | 90.4% | 84.9% |
| W23 | 107 | 66 | 41 | 91.6% | 68.7% |
| W24 (partial) | 35 | 23 | 12 | 91.2% | 79.4% |

Suspensions ran 0–5/week for two months, then 43 (W22), 66 (W23), 23 (W24 so far). Words in
`lapsed` state also appeared for the first time (now 41).

### Root cause: the 2026-06-03 "complete the tiers" bulk promotion

At 09:55:03–05 UTC on 2026-06-03, **227 words were promoted into acquiring in a single
2-second batch** — 7.5× the `DAILY_INTRO_CAP=30`. The perpetrator is `/tmp/complete_tiers.py`
on the server (left untracked, never committed): a one-off that promoted *every*
encountered/new frequency-core word at rank ≤ 2000 and force-reintroduced *every* suspended
leech at that rank, calling `start_acquisition(..., enforce_daily_cap=False)`. It was not
logged to `activity_log` and has no experiment-log entry — the same day's logged decision
(throttle simulation) explicitly concluded the *opposite*: "raise the earned full budget
only, keep triggers/accuracy floors unchanged."

Measured aftermath of the 227-word cohort (one week later):

- **39/227 (17%) already suspended as leeches**; 92 still stuck in acquiring; 64 reached
  known; 32 learning.
- The cohort is largely *recycled hard words*: existing ULK rows with prior history (times_seen
  up to 26, accuracy ~30–55% — e.g. مَنَّى seen=8 correct=2, خَفِيَ seen=10 correct=2). These
  words had already failed once under normal scheduling.
- Acquisition accuracy dropped to **68.7% in W23**; the acquiring pool is now **138 words, 127
  of them overdue, 85 sitting in Box 1** — which keeps the recovery throttle pinned and the
  comprehensibility gate strained.
- Of 132 words suspended since 2026-05-25: 72 textbook_scan, 16 frequency_core, 16 study;
  64 verbs vs 61 nouns; 88 of 132 at frequency rank 1k+. 20 had previously graduated.

A secondary contributor predates June 3: review volume roughly doubled from May 26
(889/753/622/1001 reviews/day vs ~250 before), so the 30-day sliding leech window
accumulated misses faster, and the W20 intake spike (203 intros) matured into W22 leeches.

**Verdict.** The leech engine is doing its job — suspending words the scheduler can't teach
right now. The metric damage came from feeding it: a cap-bypassing bulk promotion of words
with documented failure history. This is the exact failure mode already recorded in memory
("system-wide caps belong at the chokepoint... one caller that others bypass") — the cap *was*
at the chokepoint, and the bypass flag was used anyway.

### Recommendations (R1–R3, learning engine, highest priority)

- **R1 — Drain the acquiring backlog deliberately.** Run `demote_inert_acquiring.py` (exists)
  to demote the never-reviewed / repeatedly-failing members of the 138-word acquiring pool
  back to `encountered`; let the cap re-promote them at ≤30/day through the normal flow.
  Box-1 count of 85 vs `RECOVERY_BOX1_UNREVIEWED_LIMIT=5` means sessions will otherwise be
  recovery-throttled for weeks.
- **R2 — Make the cap bypass auditable.** `enforce_daily_cap=False` has no in-repo callers; it
  exists only for ad-hoc scripts. Require every bypass to write an `activity_log` row
  (`log_activity` inside `start_acquisition` when the flag is set), so the next bulk action
  is visible in the same dashboards that track its consequences. Add a pytest for the cap
  (acquisition_service currently has **no dedicated test file** — notable given this incident).
- **R3 — Gate leech *re*-introduction on the word-value judge (Part C, already designed).**
  8+ of the recent suspensions are `leech_reintro` words bouncing back to suspended; the June 3
  script force-reintroduced suspended leeches and many re-suspended within days. The
  IDEAS.md Part C judge (`is_artefact / usefulness / recommended_action`) should run *before*
  any reintro — several of the churning words are frequency-core artefacts (مَنَّى "to arouse
  desire" at rank 329, لَوْح glossed "to wave") where the rank, the gloss, or the lemma itself
  is wrong. Teaching them is negative-value work. This is an endorsement + prioritization of
  an existing design, not a new proposal.

---

## Part 2 — Learning analytics: everything else

### 2.1 What is healthy

- **Consistency is extraordinary**: 122-day streak, 100% of days active, ~7 sessions/day,
  uniform across weekdays.
- **FSRS retention 93.5% lifetime** (target 0.95, weekly 91–94%); median stability 78d;
  46.5% of cards above 90d stability. 2,259 known words; top-1000 content coverage 92.6%.
- **Graduation pipeline**: median 2 reviews / 64h to graduate; Tier-0 instant graduation
  (P25 = 0 reviews) is doing what it was designed to do for familiar words.
- **Collateral credit works**: 42,688 collateral reviews at 92.7% vs 9,944 primary at 79.5% —
  the foundational invariant (every word in every sentence earns credit) is carrying ~80% of
  all review volume.
- **LLM plumbing**: last 3 days — Claude CLI sonnet 3,864 ok / 102 err (2.6%), Codex 2,731 ok /
  417 err (13.2%, failover catching it), API fallback (gpt-5.2) only 152 calls. No silent
  primary-model failure pattern (the Rule-13 check).

### 2.2 Signals worth acting on

**(a) Due-coverage deficit: 51 of 611 due words (8.3%) have zero reviewable sentences.**
The known recurring problem (memory: `feedback_due_coverage_deficit_recurs`). The list is
self-incriminating: نَدْرُسُ "we study", يَكْتُبُونَ "they write", ثَّامِنِ (leading-shadda
artifact), رَّابِعَة — **inflected forms and orthographic artifacts that live as lemmas**.
These can never get good sentences; one has `generation_failed_count=28` (the cron keeps
paying to fail). A `/tmp/refill_deficit.py` recipe exists on the server (2026-05-29) but was
never committed.
→ **R4**: commit the refill script as `backend/scripts/refill_due_coverage.py` and wire it as
an `update_material.py` step (it already runs under the material flock); route the
artifact-shaped gap words through the Part C judge → retire instead of regenerate. This also
covers IDEAS Part D ("stats honesty" — the same artifacts pollute the frequency-gap display).

**(b) The 3-point rating scale is really 2.2-point.** Rating 4 ("easy") has been used **0
times in 53,136 reviews** — the frontend never sends it — and rating 2 is only 1.8%. FSRS-6
therefore learns from {1, 3} almost exclusively. This is consistent with the sentence-first
UX (per-word taps are binary-ish) and is *not* causing visible harm (retention is on target),
but it means `desired_retention=0.95` + no-easy-signal is the only thing standing between
mature cards and over-review. That connects directly to the already-briefed **Lever 1**
(no-credit for R≥0.97 trivial collateral, commit 800b6c43): with no rating-4 signal, FSRS
cannot self-correct mature-card over-exposure — the credit policy has to do it. **R5: proceed
with Lever 1 as specced; it is the single highest-leverage algorithmic change available.**
(Per the 2026-06-06 analysis, 76% of FSRS reviews are trivial recalls.)

**(c) Difficulty distribution is bimodal**: 56.7% of cards at difficulty <3, but **28.5% at
≥7** (675 cards, 610 of them currently "known", 53 with leech history). The hard tail is the
at-risk pool the new scaffold bias (PR #197) feeds back into sentences. No new action needed —
but this is the cohort to watch when judging whether Lever 1 + at-risk bias move retention,
i.e. *segment weekly retention by difficulty band* in the next review rather than reading the
blended number.

**(d) Encountered pool is healthy** (185 words, median 1 encounter — drains correctly), and
the suspended pool is *recent* (median 4 days), so most of the 135 suspended words will hit
their 3–30d reintro timers soon. **Without R3 (judge-gated reintro) the W22–W24 suspension
wave will replay itself in W25–W27.** This is the most time-sensitive recommendation.

### 2.3 Explicitly NOT re-proposed

Checked against the 2026-05-22 triage + experiment log: dynamic session sizing, response-time
fluency signal, familiar-encountered gate, rasm-confusable exclusion, mnemonic quality gates
(mnemonics are off), FSRS optimizer retune (rejected 2026-06-06), comprehensibility-threshold
relaxation (not binding), weakening the `same_lemma` rejection (load-bearing).

---

## Part 3 — Code health

Three audit passes (backend, frontend, prior-work). Full details in the audit transcripts;
condensed here.

### 3.1 Backend

| Finding | Detail | Suggested action |
|---|---|---|
| **File bloat** | `sentence_selector.py` 2,871 LOC, `material_generator.py` 2,683, `sentence_validator.py` 2,346, `story_service.py` 2,179, `routers/stats.py` 1,763. Top 5 = 27% of service code. | Do **not** big-bang refactor (Rule 14 territory; selector has 10+ recent commits). Opportunistic extraction only: scoring functions out of selector, the 100-line `FUNCTION_WORD_GLOSSES` dict + normalization helpers out of validator into a `text_normalization.py` leaf module. |
| **Script sprawl** | 149 scripts; ~62 (42%) are one-off backfills/cleanups, 10+ date-coded (`*_2026_05_15.py` etc.). Only 4 are wired into cron. | `mkdir backend/scripts/archive/` and move completed date-coded one-offs there (keeps history greppable, declutters `ls scripts/` — which Rule 14 makes everyone read). Zero behavior risk. |
| **Dead-code candidates** | `soniox_service.py` (unused TTS alt), `chimera_audit.py`, `bare_shape_check.py`, `pattern_enrichment.py`, `pipeline_tiers.py`, `grammar_tagger.py` (backfill-only). | Verify each has no live import, then delete (git keeps them). `memory_hooks.py` stays — deliberately gated off. |
| **Test gaps at load-bearing spots** | No dedicated tests for `acquisition_service.py` (the intro-cap chokepoint — see Part 1), `sentence_eligibility.py` (the reviewability gate), `canonical_resolution.py` (variant redirect), `mapping_rescue.py`. | Add focused tests for these four first; they encode hard invariants that have each caused production incidents. |
| **Flag drift** | `ALIF_RUN_CRON_LEMMA_ENRICHMENT` defaults to 0 in code but 1 in `deploy/alif-update-material.sh` — intentional (cost control) but only documented in the shell wrapper. | One-line note in CLAUDE.md LLM section is already there; add the same comment at the `_env_bool` site in `update_material.py`. |
| **Server /tmp hygiene** | 60+ ad-hoc `.py` scripts in server `/tmp`, several of which mutated prod state (`complete_tiers.py` being the costly example). | Adopt: any /tmp script that *writes* to the DB must either be committed to `scripts/` or paste its source into an `activity_log` entry. R2's log-on-bypass covers the worst case mechanically. |

### 3.2 Frontend

| Finding | Detail | Suggested action |
|---|---|---|
| **`app/index.tsx` is 5,284 lines** | 4 card components + session state + interleaving + helpers in one file. | Extract `SentenceReadingCard` / `SentenceListeningCard` / actions into `lib/review/` (where `WordInfoCard.tsx` already lives). Mechanical, high readability payoff. |
| **Stats type drift** | `types.ts` declares `known_words/learning_words/new_words/streak_days`; backend sends `known/learning/new` (mapped by hand in `api.ts:335–368`); `streak_days` is never sent. Mock data mirrors the frontend names, so mocks can't catch backend renames. | Rename the TS fields to match the wire format (one mechanical PR), delete the mapping layer. |
| **Duplicated offline queues** | `sync-queue.ts` (317 LOC) and `polyglot-sync-queue.ts` (201 LOC) share lock/retry/flush logic verbatim. | Extract a generic queue when next touching either — not before (working offline code is risky to churn). |
| **stats.tsx 1,697 LOC** | 7 inline card components each with own StyleSheet. | Same treatment as index.tsx, lower priority. |

### 3.3 Suggested cleanup order (code)

1. `scripts/archive/` sweep + dead-service deletion — an hour, zero risk, big `ls` payoff.
2. Tests for `acquisition_service` (cap), `sentence_eligibility`, `canonical_resolution` — directly motivated by Part 1.
3. Stats type-drift PR (frontend rename + drop mapping).
4. `index.tsx` card extraction.
5. Validator normalization-module extraction (only alongside other validator work).

---

## Part 4 — Consolidated priority list

| # | Action | Type | Effort | Expected effect |
|---|--------|------|--------|-----------------|
| R3 | Judge-gate leech reintro (Part C consumer #2) — **before the 135 suspended words' timers fire** | algorithm | medium | stops the W25 replay of the suspension wave |
| R1 | Demote-and-redrip the 138-word acquiring backlog | ops | small | unpins recovery throttle, restores intro flow |
| R5 | Ship Lever 1 (no-credit trivial collateral) per existing brief | algorithm | medium | recovers wasted review time; un-dilutes FSRS signal |
| R4 | Commit + cron the due-coverage refill; retire artifact gap-lemmas via judge | ops/pedagogy | small | kills the recurring 8% due-coverage hole |
| R2 | Log-on-bypass for the intro cap + cap pytest | guardrail | small | makes the next bulk action visible |
| C1–C5 | Code cleanup order above | code | rolling | maintainability |

The deeper pedagogical arc the data supports: the system has fully mastered the *easy half*
of the learner's exposure (82% all-known corpus, 76% trivial recalls, instant graduations) and
is now grinding against the *hard residue* — rank-2k+ vocabulary, verbs (64 of 132 recent
suspensions), and data-quality artefacts masquerading as words. Every recommendation above is
some form of "spend review minutes on real, learnable words and stop spending them on
artefacts and already-known words."
