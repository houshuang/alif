# Two-Week Health & Learning Analysis (2026-06-03 → 2026-06-16)

**Why this doc.** Routine full-system health + learning review covering the two weeks since
the 06-03 cap-bypass flood and the 06-10 throttle fix (PR #198). Doubles as the scheduled
recheck the 06-10 entries asked for ("~06-17"). Reconciles every open watch-item from the
06-06 / 06-10 analyses against fresh prod data, and surfaces the one metric that is
**getting worse** (due-coverage deficit) plus a dead generation subsystem (material-job
queue). Data pulled live from prod `alif.db` (`/tmp/two_week_analysis.py`), prod logs, and
the LLM call logs. No code changed by this analysis.

**Verdict: system healthy, learner genuinely progressing (~84 known words/week). The 06-10
throttle fix worked and its targets are met early. Three unshipped levers remain; one
concrete problem (due-coverage deficit) is widening and is the priority fix.**

---

## 1. North-star — known stock is climbing, not collapsing ✅

| Date | Known stock |
|------|-------------|
| 06-03 | 2,170 |
| 06-10 | 2,259 |
| **06-16** | **2,326** |

**+156 in 13 days (~84/week)** — near-record pace. This reconfirms the 06-10 ground-truth
correction: the "north-star collapse" headline was a **flow artifact**. The grad−suspension
"net" (current-week +6, W06-08 +19) understates reality because suspensions are 3–14d
cooldowns, not losses. **Read the known-stock curve as the north-star; never the
grad−suspension flow.**

State distribution (06-16): known 2,326 · suspended 169 · encountered 159 · acquiring 105 ·
learning 77 · lapsed 42.

Weekly flow (graduated / leech-suspended / net), for the record:

| week start | graduated | suspended | net |
|------------|-----------|-----------|-----|
| 04-27 | 104 | 3 | +101 |
| 05-04 | 121 | 15 | +106 |
| 05-11 | 53 | 5 | +48 |
| 05-18 | 87 | 15 | +72 |
| 05-25 | 54 | 38 | +16 |
| 06-01 | 107 | 50 | +57 |
| 06-08 | 94 | 75 | +19 |
| 06-15 (partial) | 17 | 11 | +6 |

The rising suspension column (38 → 50 → 75) is the 06-03 flood's recycled hard words
churning through leech triage — **not** known-word loss (see §3).

## 2. Watch-items from the experiment log — met early ✅

The 06-10 throttle fix asked for a recheck "~06-17". Today is 06-16; targets already met:

| Watch item (source) | Target | 06-16 actual | Status |
|---------------------|--------|--------------|--------|
| Intros/day back to earned schedule (06-10 PR #198) | 8–30/day | 5–19/day (one 34 spike = 06-13 headword-fix import) | ✅ |
| `box2_due` genuine drainable debt (06-10) | < 30 | **18** | ✅ |
| box1 unseen (06-10) | was 3 | **11** | ⚠️ crept up |
| understood% post-flood dip (06-10) | recover from 45–55% | ~51–69%, **no_idea = 0 every day** | ✅ |

The 06-03 flood is unmistakable: **218 intros in one day** (the uncommitted
`/tmp/complete_tiers.py` cap-bypass). Post-fix the budget returned to the earned schedule
exactly as predicted. box1-unseen drifting 3→11 deserves a one-line recheck, but the
*actionable* count (excluding proper-name + generation-backoff rows, per the 06-10 fix to
`_recovery_backlog_counts`) is what actually gates the throttle.

## 3. Suspension wave — real churn, minimal real loss ✅

- 132 words suspended in 14 days; 169 currently suspended.
- **Of currently-suspended words, only 21 were ever graduated** (real knowledge losses).
  The rest are flood-cohort hard words cycling through triage — the 06-10 framing holds.
- **R3 (judge-gate leech reintro, IDEAS Part C) is still unshipped.** 169 suspended rows
  carry 3–30d reintro timers; the W25-replay risk the 06-10 entry flagged is still live.

## 4. ⚠️ Due-coverage deficit is WIDENING — and the job queue isn't draining it

Headline actionable finding.

- **57/421 (13.5%)** FSRS-due known/learning/lapsed words have **zero reviewable
  sentence** — up from 51/611 (8.3%) on 06-10. The recurring deficit is growing.
- **Material-job queue is dead-lettered:** 2,296 `sentence_shard` jobs queued, **2,293
  never attempted** (attempts=0), oldest **2026-05-12**. `done last 7d: 3` vs `created last
  7d: 506`. The worker leases ~3/run; most fail deterministic validation and re-queue.
- Real generation runs via the cron's *direct* path (daily `sentence_gen` logs 30–85 KB;
  reviewable pool **1,878 / 1,951** active) — so the queue is a **starved parallel lane**,
  not load-bearing. But it means the deficit refill never happens.
- **R4 from 06-10 (commit the 05-29 `refill_deficit.py` recipe as a cron step) was never
  done.** That is the direct fix for the 57 uncovered due-words.

## 5. FSRS / retention — genuine recall ~80%, consistent with over-review ✅

- **Primary, non-acquisition (real retrieval), 14d: 79.4%**; lifetime 83.8% (n=5,066). The
  composite "93.5%" headline includes collateral re-confirmations.
- **Rating 4 ("Easy") used 0 times in 6,326 reviews** (rating 3 = 87.2%, rating 1 = 9.5%,
  rating 2 = 3.4%). Confirms the 06-06/06-10 finding: FSRS has no "easy" signal, so massed
  collateral re-exposure inflates stability — **Lever 1** (stop FSRS-crediting trivial
  R≥0.97 collateral) still stands, unshipped, and needs user sign-off (touches the
  FOUNDATIONAL collateral-credit invariant).
- Review volume healthy: 140–940/day, ~80% collateral / 20% primary, daily accuracy 82–93%.

## 6. Generation / LLM provider health — one bad day, now settled ✅

- Provider mix as-designed: `claude_cli/sonnet` (sentence gen) + `codex_cli/gpt-5.5`
  (audit/enrichment), roughly even split.
- **06-14 spike**: 122/747 errors (16%); Sonnet 86/331 (**26%**) → 60 `gpt-5.2` API
  fallbacks (the fallback chain firing — the silent-degradation risk of Rule #13). Codex
  32/324 (10%, matches the documented ~13%).
- **Settled by 06-16**: 17/231 errors (7%), only 4 gpt-5.2 fallbacks. Looks transient
  (likely Claude CLI availability), not structural — but the Sonnet error spike is worth a
  note.
- Frequent cron lines `correct lemma not found in DB` / `no candidates passed deterministic
  validation` are **normal rejection behavior** (no-auto-create + same_lemma gates working).

## 7. Infra & curriculum supply — green ✅

- Services active (alif-backend/expo, polyglot-backend) · disk 65% (26 GB free) · DB 104 MB.
- Backups current: 6-hourly, last 12:00 today. Cron intact (material every 3h at :30,
  backup every 6h).
- Curriculum supply: 177 lemmas gated in 14d; **frequency-core gap at rank ≤2000 is now 0**
  (that initiative is essentially complete for the top 2k); 3,113 / 5,000 FCE rows mapped.
- 24 lemmas in generation backoff. 159 encountered awaiting promotion (daily-cap deferral —
  normal). 49 confusion captures total (17 in 14d) — about to cross the ≥50 analysis
  threshold.

---

## Recommended actions (priority order)

1. **R4 — deficit-refill cron step.** The 13.5% due-coverage deficit is growing and the
   material-job queue can't drain it. Mechanical, low-risk, directly serves due known words.
2. **Material-job queue cleanup.** 2,296 stale shards (oldest May 12, never attempted) are
   misleading cruft. Either fix worker throughput or retire the lane.
3. **R3 — judge-gate leech reintro** before the 169 suspended rows' timers fire and replay
   the wave.

**Lever 1** (collateral-credit policy for R≥0.97) remains the deepest unaddressed lever but,
per the 06-06 entry, needs user sign-off — it touches a foundational invariant.

**Watch next (~W26):** box1-unseen (3→11 drift), understood% trend, confusion-capture
analysis once n≥50.

---

## Deep-dive outcome (same day): generation paths + the fix shipped

Investigating R4 + the queue revealed both are the *same* problem and overturned the
surface reading:

- **The cron does not generate.** `update_material.py` runs with `--max-step-a-sentences 0`.
  Since 2026-05-12 the `material_jobs` queue was meant to be the generation engine but never
  took over — **`warm_sentence_cache` (live, post-session) is the real generator** (2,392
  sentences/14d; the queue completed 101 jobs ever, 3 in the last 7d).
- **The queue is structurally broken:** acquiring-rescue jobs (`priority_tier=0`) outrank
  tier-1 due-deficit jobs (priority 10+) and starve them at 3 jobs/run; rescue words bypass
  backoff and re-enqueue every run (`generated 0 for [873,938]` ×53); hour-windowed
  `dedupe_key` piles up duplicates (one lemma in 159 jobs; 2,296 queued, 2,293 never tried).
- **The 57 deficit words are all tier 1 (overdue)**, not tier-4. Only 17/57 were even
  enqueued. Of the 57: 1 inert, ~6 chronically-failing, ~13 lemma artifacts (conjugations
  stored as lemmas — نَدْرُسُ, يَكْتُبُونَ; leading-shadda ثَّامِنِ/رَّابِعَة), **~40 real &
  generatable**.

**Shipped** (branch `sh/retire-jobqueue-deficit-refill`): retired the `material_jobs` cron
phases (scripts/table kept dormant); added `scripts/refill_due_deficit.py` as a cron phase
(classify-then-act: skip inert + backoff, log artifacts for the decomposition audit,
generate the clean remainder via `batch_generate_material`); tests in
`test_refill_due_deficit.py`. Salvage relaxation untouched — already landed 2026-06-03.

## ✅ Check-back (~2026-06-20, ~4 days post-deploy)

Re-run `/tmp/two_week_analysis.py` §8 and confirm:
1. **Due-coverage deficit < ~5%** (was 13.5%, 57/421). The ~40 real words should be covered
   within 1–2 cron passes; residual = artifacts + chronically-failing (expected, backing off).
2. **`material_jobs` queue empty** (truncated at deploy) and **no new rows** (planner/worker
   no longer in cron).
3. **`deficit_refill` ActivityLog entries** present, showing covered/failed/artifact counts.
4. **Artifact candidates** logged → fold into the open lemma-decomposition audit (retire/merge
   نَدْرُسُ/يَكْتُبُونَ-type conjugation lemmas; fix leading-shadda headwords).
5. Sanity: known-stock still climbing; no LLM error/backoff spike from the new step.
