# Learning Review — 2026-05-03 (last 21 days)

Reference snapshot: `~/alif-backups/alif_20260503_210136.db`. Last review timestamp in DB: `2026-05-03 09:01:18`. Window: 2026-04-13 → 2026-05-03.

## TL;DR

| Indicator | Value | Verdict |
|---|---|---|
| Reviews per day (mean / median) | 328 / 320 | Strong, very consistent |
| 7d / 14d / 21d retention (FSRS) | 94.2% / 94.3% / 93.2% | **Healthy.** Above 90% target |
| Total known words | 1,697 (76.7% of ULKs) | Mean stability 80d, median 64d |
| Acquiring backlog | 86 (47/30/9 across boxes 1/2/3) | Below the 60 box-1 gate |
| 21d intros vs graduations | 269 → 278 | Net **−9** — consolidating, not drowning |
| 7d intros vs graduations | 50 → 97 | Net **−47** — last week cleanup-heavy |
| Sessions in 21d | 99 | ~5/day, normal |
| Lapses 21d retention | 51.2% | Lapsed words coming back slowly |
| Listening reviews | 0 | Mode unused this window |

Net: the algorithm is in good health — retention well above target, pipeline self-balancing, churn down. There are five honest follow-up problems worth investigating, none of them alarms.

---

## 1. System activity (algorithm/data changes)

From `activity_log` in window:

| Event | Count |
|---|---|
| `sentences_retired` | 253 |
| `material_updated` (3-hour cron) | 162 |
| `leech_reintroduced` | 53 |
| `manual_action` | 22 |
| `flag_resolved` | 10 |
| `leech_suspended` | 1 |

Major code shipments in the window (from git):
- 2026-04-30 — PR #55: intro-card coverage + duplicate veto + Al-Kitaab benchmark matcher (deployed today)
- 2026-04-27 — PR #53: 7-day learner-data audit fixes (Tier-0 time gate, Jaccard veto, intro cap, end-of-session intro exclusion)
- 2026-04-27 — PR #52: lemma-decomposition Phase 2 step 4c+6 (re-gated 161 compounds, requeued 3,056 corpus sentences)
- 2026-04-22 — Bookify Arabic redesign (Kalila dove import added 19 lemmas)

The 162 `material_updated` events ≈ the 3-hour generation cron firing 8x/day for 21 days, which lines up.

## 2. Knowledge-state distribution (current)

```
known           1697   76.7%
encountered      319   14.4%   ← see §10
acquiring         86    3.9%
suspended         44    2.0%
learning          42    1.9%
lapsed            24    1.1%
TOTAL           2212
```

2,861 canonical lemmas exist in DB; 2,212 (77%) have a ULK row. The 649 lemmas without a ULK are typically Hindawi-corpus or Quran lemmas not yet introduced.

## 3. Pipeline backlog over time (Leitner boxes)

```
date         box1  box2  box3  total
2026-04-12     32    40    12     84
2026-04-13    106    41    10    157   ← surge after PR #50
2026-04-18    114    39    22    175   ← textbook_scan import wave (47 intros)
2026-04-22     33    42    15     90   ← cleared
2026-04-26     76    32    13    121   ← 84-word textbook batch
2026-04-27    101    41    13    155
2026-04-30     47    37    11     95
2026-05-02     42    30     9     81   ← stable
```

The two visible spikes (4-13 and 4-26) both came from textbook_scan batch imports; both cleared within 4 days. Box-1 currently sits at **47**, comfortably under the `LOW_TIER_BLOCK_BACKLOG = 60` gate, so passive frequency-list intros are *not* gated.

## 4. Daily review activity

```
date         reviews sess lemmas  acc%  acq%
2026-05-02       546   11    388 93.4%  8.6%
2026-05-01       333    5    254 92.8% 10.5%
2026-04-30       230    5    177 93.5% 11.7%
2026-04-29       290    5    211 92.1% 18.3%
2026-04-28       549    7    366 90.7% 20.2%
2026-04-27       320    4    230 87.2% 30.9%
2026-04-26        96    3     73 84.4% 34.4%
…
21d totals: 6,893 reviews, mean 328/day, median 320/day, ≥3 sessions every day
```

The **acq%** column (share of reviews that were acquisition-state) drops over the window — 30%+ early to ~10% recently — confirming the pipeline-clearing trend.

## 5. Accuracy by window × state

| State | 7d | 14d | 21d |
|---|---|---|---|
| `known` | 95.2% | 94.6% | 92.6% |
| `learning` | 80.6% | 73.4% | 69.3% |
| `acquiring` | 65.9% | 62.1% | 57.0% |
| `lapsed` | 17.9% | 43.3% | 51.2% |
| `suspended` | 54.2% | 47.9% | 37.6% |
| **OVERALL** | **91.6%** | **91.7%** | **89.6%** |

Rating distribution (7d):
- `again` (1): 6.6%, `hard` (2): 1.8%, `good` (3): 91.6%, `easy` (4): 0%

The lapsed-word accuracy improving over the 7d→21d windows means recently-relapsed words are being reviewed early in the relearning curve (still failing); older lapsed words have stabilized. Suspended-word accuracy *dropping* over 7d→21d is worth a look (§13).

## 6. Retention — FSRS-state words only

```
7d : 1894 FSRS reviews, retention 94.2%
14d: 4001 FSRS reviews, retention 94.3%
21d: 5642 FSRS reviews, retention 93.2%
```

Above the 90% target across all three windows. **Healthy.**

## 7. New introductions

21d source mix:

```
textbook_scan        184   ← 68%
book                  50
collateral            13
leech_reintro          7
duolingo               6
quran                  5
story_import           1
mapping_correction     1
flag_autocreate        1
encountered            1
```

textbook_scan dominates because mid-month bookify Kalila import + an OCR scan batch on 04-26 (84 intros). The mix is consistent with intent: passive-frequency lists (`duolingo`) and `story_import` are tiny, which is correct given the 60-box-1 gate was tripped briefly mid-window.

Weekly rolling:

```
week ending 2026-05-03: intros= 50 grads= 97 net=-47  reviews=2274 acc=91.6% sessions=38
week ending 2026-04-26: intros=143 grads= 92 net=+51  reviews=2511 acc=91.8% sessions=33
week ending 2026-04-19: intros= 76 grads= 89 net=-13  reviews=2108 acc=85.0% sessions=30
```

## 8. Graduations

```
21d total: 278 (mean 13/day)
peaks: 4-13=32, 4-18=28, 4-23=22, 4-27=34, 4-28=29
```

The 4-27/28 spike correlates exactly with PR #53 deployment (Tier-0 time gate landed) — the new graduation tier is firing as designed.

## 9. Overdue distribution

**Acquiring overdue** (current): box-1 = 47, box-2 = 30, box-3 = 3. Note that *all* current acquiring rows are technically overdue (the queue is small enough). The system is keeping pace.

**FSRS overdue** (current snapshot):

```
FSRS words: 1727, overdue: 231 (13.4%)
  <1d         58
  1-3d        56
  3-7d        60
  7-14d       44
  14-30d      10
  30d+         3
mean 5.3d, median 3.1d, max 67.1d
```

The long tail (3 words over 30 days, max 67d) is a real concern — these slip past the `OVERDUE_ESCALATION` 4x boost. They should be re-investigated; see §13.

## 10. Sentence pipeline health

```
Active sentences: 1,140 (1,089 LLM, 45 corpus, 6 book)
New sentences last 10 days: 100–300/day (cron healthy)
Acquiring words WITHOUT any active sentence: 12/86 (14%)
Words in generation backoff: 211
```

12/86 acquiring words have **no sentence available at all**. These are silent failures of the generation pipeline:

| Word | Gloss | Box | Seen | Source | Fails |
|---|---|---|---|---|---|
| دم | to coat, to smear | 1 | 5 | textbook_scan | 7 |
| رد | to return | 1 | 5 | textbook_scan | 4 |
| مخلص | sincere | 1 | 5 | textbook_scan | 4 |
| ارتجى | hoped for | 1 | 6 | textbook_scan | 4 |
| مقسم | switchboard | 1 | 5 | book | 4 |
| رفع | to rise, elevated | 1 | 0 | textbook_scan | 4 |
| غلاف | wrapper, covering | 1 | 0 | textbook_scan | 6 |
| زين | to decorate | 1 | 0 | textbook_scan | 6 |
| قصص, طوق, جحري, سمسم | (book) | 1 | 0 | book | 3–4 |

These are *also* in the §13 struggling list — the user is hitting them in sessions occasionally (presumably via on-demand or pre-existing sentences) but with no current pool, the FSRS system can't space them. **This is the highest-priority follow-up.**

## 11. Diversity & scaffold health (7d)

- Sentence reviews: 524 across 489 distinct sentences → reuse ratio **1.07** (very low — almost no within-window repeats, good for diversity, possibly reduces same-day consolidation)
- Distinct lemmas reviewed: 978
- Distinct roots reviewed: 701
- Mode split: 100.0% reading, 0% listening

`★ Insight ─────────────────────────────────────`
The 1.07 reuse ratio means almost every sentence is fresh. This is *probably* fine for retention (FSRS spaces by stability, not surface) but the system was designed to allow same-day repeats inside acquiring (`MIN_ACQUISITION_EXPOSURES=4` in `sentence_selector.py`). If acquiring intros are not seeing 4× exposure within their box-1 window, that explains the 65.9% 7d acquiring accuracy.
`─────────────────────────────────────────────────`

## 12. Stability distribution (known words only)

```
<7d         133
7-30d       328
30-90d      567   ← largest cohort
90-180d     482
180-365d    155
365d+         0    ← system not yet old enough
mean 80d, median 64d, n=1665
```

The bulk has consolidated into the 30-180d band. No words above 365 days because the app launched ~3 months ago.

## 13. Red flags & follow-up issues

### 13a. Generation backoff is hiding 211 words

Top failures by count:

```
بَرَّاد (refrigerator), سَهَم (to contribute), أَرْمَل (widower),
الْتَمَع (to glisten), تَكَرَّر (to repeat), نَاسَب (to suit),
مُنْدَثِر (extinct), مَغْرِب (Morocco), ضِرْس (molar),
مُرَبَّى (jam), صُوفِيّ (Sufi), خَشُن (to be rough), …
```

All have `failed_count = 8`, all in `known` state. This means the 3-hour cron tried to generate fresh sentences for them (presumably for FSRS retention) and failed 8× consecutively. The backoff is working as designed (avoids burning cron cycles), but the *content* is a mix of perfectly normal nouns (refrigerator, arm, bicycle, jam, Sufi). Worth checking the verifier prompt — this looks like the verifier-too-strict failure mode rather than genuinely impossible words.

### 13b. 12 acquiring words have no active sentence (see §10)

**Action:** for each, run material generation manually with the looser verifier (or one-shot via `claude -p`). The textbook_scan ones (د م، ر د، ر ف ع) are common enough that fresh generation should succeed.

### 13c. 10 acquiring words "starved" (never reviewed, ≥2d overdue)

```
الا (to neglect)        introduced 2026-03-26  4 sentences exist
بنية (brown)             introduced 2026-03-29  2 sentences
اي (any)                 introduced 2026-04-12  5 sentences
زين (to decorate)        introduced 2026-04-18  0 sentences
غلاف (wrapper)           introduced 2026-04-18  0 sentences
امي (my mother)          introduced 2026-04-18  1 sentence
رفع (to rise)            introduced 2026-04-26  0 sentences
جدي (my grandfather)     introduced 2026-04-26  2 sentences
مجان (free)              introduced 2026-04-26  1 sentence
مفحم (silenced)          introduced 2026-04-26  3 sentences
```

5/10 have sentences yet are not selected. The `NEVER_REVIEWED_BOOST = 5.0` should make these dominate selection. Two hypotheses worth checking: (1) the sentences exist but fail the comprehensibility gate (≥60% known scaffold) because of unknown collateral words; (2) `mappings_verified_at` is NULL on those sentences and the fill phase is skipping them.

### 13d. 305 encountered words with NULL `introduced_at`

Of the 319 `encountered` ULKs, 305 have `introduced_at IS NULL`. These are pre-PR-#53 records (encountered should now always set introduced_at on creation). They're low-priority cleanup — they don't break anything because the pipeline only reads `acquisition_next_due` and `knowledge_state`, but they will produce odd metrics in any report that buckets by introduced_at.

Source breakdown of encountered:

```
textbook_scan       167   oldest 2026-02-21  newest 2026-03-09
book                 69   oldest 2026-02-16  newest 2026-04-22
quran                58   oldest -           newest -
study                23   oldest 2026-03-23  newest 2026-03-23
mapping_correction    2
encountered with active sentence:  23/319
```

Only 23 of 319 have any active sentence — the encountered backlog is *invisible to the engine* until a sentence containing them happens to be reviewed (auto-introduce path). Worth pushing the 167 textbook_scan ones into acquisition explicitly if they're in your study-priority list.

### 13e. 3 FSRS words 30+ days overdue (max 67d)

The OVERDUE_ESCALATION linear ramp tops out at 4× at 17d+; with 67-day overdue items still not selected, either (1) the words have no active sentence, (2) every sentence they're in fails the comprehensibility gate, or (3) they're in a focus-cohort blackout. Run a targeted query to identify them and check.

### 13f. Listening mode unused (0 reviews, 21 days)

100% of sentence reviews were `reading`. If listening is intentionally on hold, fine; if it's a UX dead-end (audio not playing on web, onboarding doesn't surface it, etc.) worth investigating. Audio assets *are* being generated by the cron (`material_updated` summaries say "0 audio" though — either no acquiring words triggered audio gen, or audio gen is failing silently).

### 13g. Suspended-word accuracy degrading

7d/14d/21d → 54%/48%/38%. The trend is downward, suggesting recent reintroductions are not retraining as well as earlier ones. 53 leech reintroductions in window is a lot — worth checking whether the reintro flow is too aggressive (e.g. shipping back a word at box 2 instead of box 1). All current 44 suspended words have leech_count 1–5, mostly clustered around late April, which means they failed within ~10 days of being reintroduced after a previous suspension.

## 14. Invariant check — passed

- ✅ All `acquiring/learning/known` lemmas have `gates_completed_at`
- ✅ All active non-book `sentence_words` have `lemma_id` populated
- ✅ All `acquiring` ULKs have `introduced_at`
- ✅ Box-1 (47) under LOW_TIER_BLOCK_BACKLOG (60)

## 15. Recommended actions (in priority order)

1. **Force-regenerate sentences for the 12 sentence-less acquiring words** (§13b). Use a relaxed verifier or `claude -p` direct.
2. **Audit the 305 encountered-NULL records** — pick which to promote to acquiring vs which to drop (§13d).
3. **Trace the 10 starved acquiring words and 3 long-overdue FSRS words** through `build_session()` with `--debug` to find the gate that's filtering them (§13c, §13e).
4. **Loosen or audit the verifier prompt** for the 211 backoff words — the failure list is suspiciously generic-noun-heavy (§13a).
5. **Surface listening mode** in UI or confirm it's intentionally dormant (§13f).
6. **Investigate the leech-reintro pattern** — current suspension rate may indicate aggressive reintroduction (§13g).
