# Two-Week Ground Truth: Event-Level Learning Data (2026-05-27 → 2026-06-10)

**Why this doc.** The same-day state-of-project review
(`analysis-2026-06-10-state-of-project.md`) led with a "north-star collapse" (net
graduations − suspensions fell ~95/wk → ~20/wk). The learner's felt experience disagreed:
*"I don't feel like it's going badly — I'm seeing a lot of new words and gradually acquiring
them."* This analysis drops to the event level — actual sentences, per-word rating
sequences, session composition — to adjudicate. **Verdict: the learner is right; the
aggregate metric was misleading. Two real, fixable issues remain.**

---

## 1. What the last 14 days actually looked like

| day | sessions | sentence cards | understood% | word reviews | distinct words | first-ever words |
|-----|----------|---------------|-------------|--------------|----------------|------------------|
| 05-27 | 5 | 112 | 64% | 546 | 393 | 7 |
| 05-28 | 8 | 122 | 57% | 622 | 457 | 7 |
| 05-29 | 9 | 81 | 58% | 402 | 337 | 4 |
| 05-30 | 7 | 97 | 64% | 476 | 370 | 9 |
| 05-31 | 3 | 21 | 62% | 104 | 98 | 1 |
| 06-01 | 15 | 195 | 54% | 1,001 | 695 | 11 |
| 06-02 | 8 | 96 | 62% | 463 | 360 | 3 |
| 06-03 | 8 | 117 | **44%** | 556 | 408 | **71** |
| 06-04 | 7 | 91 | 41% | 433 | 319 | 62 |
| 06-05 | 3 | 64 | 44% | 322 | 253 | 28 |
| 06-06 | 5 | 103 | 55% | 556 | 372 | 30 |
| 06-07 | 4 | 87 | 46% | 462 | 334 | 14 |
| 06-08 | 6 | 121 | 52% | 578 | 431 | 16 |
| 06-09/10 | 5 | 65 | 57% | 325 | ~150 | 3 |

- **266 first-ever-reviewed words in 14 days** (~19/day) — the "seeing a lot of new words"
  feeling is literally measurable, and most of it arrived with the June 3 tier-completion
  flood (71+62+28 first-evers on June 3–5).
- **1,963 distinct words touched** across 6,846 reviews.
- **Sentence variety is excellent**: 1,238 distinct sentences across 1,372 sentence cards —
  91% shown exactly once in the window, max repeat 6. No grind, no stale deck.

## 2. The new-word funnel is *working*

Of the **302 words that started acquisition in the last 14 days** (mostly the June 3 flood):

| outcome after ≤14 days | n | % |
|---|---|---|
| already graduated (known/learning) | 129 | 43% |
| progressing in boxes (75 b1 / 32 b2 / 13 b3) | 120 | 40% |
| suspended (3–14d cooldown, will retry) | 49 | 16% |
| lapsed | 4 | 1% |

**57% of the 302 have their last two reviews green.** Real sequences look like textbook
acquisition curves: بُلْبُلٌ "nightingale" ✗✓✓✓✓✓ → graduated in 3 days, entirely via
collateral exposure across 5 *different* sentences; سِلَاحٌ "weapon" ✗✗✓~✓✓✓ climbing into
Box 2; الْعُثُور "finding" ✓~✓ in Box 2 after two days. 181 graduations in 14 days.

**Known-word stock check** (the true north-star): 2,170 known on 2026-06-03
(`analysis-2026-06-03-arabic-2week-health.md`) → **2,259 known today: +89 in one week**,
among the fastest weekly gains on record.

## 3. Why the aggregate metric lied

The morning doc's "net = graduations − suspensions" **conflated stock and flow**:

1. **Suspensions don't subtract known words.** Only 20 of the 132 recently-suspended words
   had ever graduated. A suspension mostly means "acquisition attempt timed out — retry in
   3–14 days," not "lost a known word." Netting them 1:1 against graduations was wrong.
2. **The suspension spike is mostly a *speed* artifact.** Leech detection is rate-based
   (accuracy <50% over the last 8 reviews, ≥5 seen) — per-word it's volume-independent, but
   review volume roughly doubled from May 26 (250→550+/day), so struggling words reach
   their 8-review verdict in days instead of weeks. The wave is the system **triaging the
   June 3 flood at 2× velocity**, concentrated into two weeks of bookkeeping.
3. **The churn is cheap.** The 120 words suspended in this window consumed **379 reviews =
   5.5% of all review volume**. This is why it's invisible to the learner: ~95% of session
   time was spent on productive material.
4. The flood itself partially paid off: 96/227 June-3 words are known/learning within a
   week. The morning doc's "17% re-suspended" was true but one-sided — 42% succeeded.

**Correction to the morning doc's framing**: the June 3 cap bypass was still a process
violation (unlogged, contradicted the same-day throttle decision) and the guardrail
recommendations (R2) stand. But its learning outcome is closer to "aggressive bet with a
16% breakage rate, processed loudly by the leech engine" than to "north-star collapse."
The *stock* metric — known words — never stopped growing.

## 4. The two real issues the ground truth exposes

### 4.1 The recovery throttle is pinned by phantom words (actionable, small)

`RECOVERY_BOX1_UNREVIEWED_LIMIT=5` triggers recovery mode when ≥5 Box-1 words have
`times_seen=0`. There are currently **exactly 9 never-seen Box-1 words — and none of them
can ever be reviewed**:

- **2 proper-name artifacts** stuck since **May 4**: نَجَحَت "Najahat", ثَمِينَه "Thameena"
  (the known proper-name-leak cohort; `word_selector` correctly filters them from sessions,
  but nothing demotes their acquiring rows).
- **7 June-3 rare words that generation can't serve**: رَخَّ "to dilute"
  (generation_failed_count=28), زَرَّ "to encroach upon one's enemy", سَنَا "to irrigate",
  ذَكَرِيّ glossed "memory" (gloss is wrong — it's the adjective "male"; artefact), etc.

So the intro budget is permanently in earned-recovery mode (the "intros run 5–11/day not
30" symptom from the 06-03 health check) because of 9 unreviewable rows. **Fix**: exclude
`word_category='proper_name'` and generation-dead words (genfail ≥ N / max backoff) from
`_recovery_backlog_counts`, and demote those rows to `encountered`/retired. This directly
buys back the "more new words" the learner wants — the funnel above shows they convert at
~43% in two weeks.

### 4.2 Comprehension dipped from ~60% to ~45–55% understood (watch, don't panic)

Before June 3: 57–64% of sentence cards rated "understood." After: 41–55%. The flood +
at-risk scaffold bias (06-06) made sentences denser in fragile vocabulary. `no_idea` stays
≈0 (7 of 10,589 cards lifetime), so this is *stretch*, not drowning — and it matches the
learner's positive perception of "lots of new words." But 41–44% days are below the i+1
design intent; worth tracking as the June-3 cohort converts. If understood% hasn't recovered
toward ~60% by ~W26 as the cohort graduates, revisit scaffold-bias multipliers.

### 4.3 (Reconfirmed) artefact lemmas waste the pipeline

The never-seen list doubles as a Part C judge work-list: wrong glosses (ذَكَرِيّ), classical
rarities mis-ranked by the frequency core (زَرَّ at rank ≤2000), and a word the generator
has failed 28 times. Same conclusion as the morning doc (R3/R4), now with sharper examples.

## 5. Revised priority order (supersedes morning doc Part 4 ordering)

1. **Unpin the recovery throttle** (§4.1) — demote/retire the 9 phantom Box-1 rows + fix
   the trigger count. Smallest change, directly increases daily new words.
2. **R3 judge-gated leech reintro** — unchanged; 135 suspended words return on 3–14d (some
   ×4 low-priority) timers and the artefacts among them should be retired, not retried.
3. **R5 Lever 1** and **R4 due-coverage refill** — unchanged.
4. **R2 log-on-bypass + cap test** — unchanged (process guardrail; the June 3 *outcome* was
   mixed, but unlogged bulk mutations must be visible).
5. North-star instrumentation: report the **known-word stock curve** (weekly known count)
   as the headline metric, not grad−suspension flow. This analysis is the cautionary tale.
