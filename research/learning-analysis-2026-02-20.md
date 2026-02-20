# Learning Analysis — 2026-02-20

> Comprehensive analysis of production data after 13 days of active Arabic study (Feb 8–20, 2026).
> Data extracted via `scripts/learning_analysis.py` run on production DB.
> Visual report: `research/learning-report-2026-02-20.html`

---

## 1. Vocabulary Status

| State | Count | % |
|-------|-------|---|
| Known | 209 | 28.8% |
| Acquiring | 76 | 10.5% |
| Learning (FSRS) | 9 | 1.2% |
| Encountered | 402 | 55.4% |
| Lapsed | 10 | 1.4% |
| Suspended | 19 | 2.6% |
| **Total** | **725** | |

**CEFR estimate**: Early A1 (~209 known words). Children's book milestone (150 words) already achieved.

### By Source (known words)
- auto_intro: 89 (43%)
- book: 53 (25%)
- duolingo: 42 (20%)
- collateral: 12 (6%)
- textbook_scan: 6 (3%)
- study: 5 (2%)

---

## 2. Learning Rate

### Graduation Rate
- **Total graduations**: 143 over 9 active graduation days
- **Weekly**: W07=51, W08=92 (80% week-over-week increase)
- **Daily peak**: 33 on Feb 20
- **Effective rate**: ~11 words/day — well above the 7 words/day research benchmark (Uchihara 2019)

### Time to Acquisition
- **Median**: 98.8 hours (~4.1 days)
- **Mean**: 90.5 hours (~3.8 days)
- **P25/P75**: 55.7h / 103.9h (tight IQR — consistent acquisition pace)
- **Reviews to graduate**: median 7, mean 13.8 (P25=4, P75=11)

Research comparison: Uchihara (2019) meta-analysis found 8-12 meaningful encounters for stable vocabulary. Our median of 7 reviews is on the low end — the acquisition box system compensates by requiring 2+ calendar days and ≥60% accuracy.

---

## 3. Retention

### FSRS Retention
- **Overall**: 93.3% (3,487/3,736 correct)
- **Weekly trend**: W06=78.3% → W07=90.0% → W08=97.5%
- This dramatic improvement reflects: (a) algorithm stabilization, (b) early inflated cards being flushed, (c) genuine learning

### Accuracy by Mode
| Mode | Reviews | Accuracy |
|------|---------|----------|
| Reading | 5,308 | 90.0% |
| Listening | 57 | 71.9% |
| Quiz | 26 | 88.5% |
| Reintro | 50 | 52.0% |
| Textbook scan | 247 | 100% |

### By Phase
- **FSRS**: 93.3% (3,736 reviews)
- **Acquisition**: 83.0% (1,952 reviews)

Research comparison: Wilson et al. (2019, Nature) found 85% as the optimal training accuracy. Our 89.8% overall is slightly above optimal — room to increase introduction rate.

---

## 4. Session Patterns

- **Total sessions**: 184
- **Median size**: 5 sentences, mean 6.8
- **Sessions/day**: mean 13.6 (range 3–27)
- **Comprehension**: 53.6% understood, 46.1% partial, 0.3% no_idea

### Size Distribution
| Bucket | Count |
|--------|-------|
| 1-5 | 101 (55%) |
| 6-10 | 47 (26%) |
| 11-15 | 22 (12%) |
| 16-20 | 6 (3%) |
| 21-30 | 8 (4%) |

Short sessions dominate — the app's design for "micro-completable units" is working well. The high session frequency (14.2/day) enables same-day acquisition box advancement (4h intervals between boxes).

---

## 5. FSRS Stability Distribution

| Bucket | Count | % |
|--------|-------|---|
| <1 day | 18 | 8.5% |
| 1-7 days | 82 | 38.9% |
| 7-30 days | 100 | 47.4% |
| 30-90 days | 11 | 5.2% |
| 90+ days | 0 | 0% |

- **Median stability**: 7.3 days
- **Mean stability**: 9.2 days (known words: 10.3d)
- **Difficulty**: 73% easy (<3), 20% very hard (>7), 5% hard, 2% medium

The 20% "very hard" words (42 words) are potential leeches — worth monitoring.

---

## 6. Frequency Coverage

| Top N Words | In Corpus | Active | Known | Function Words |
|-------------|-----------|--------|-------|----------------|
| 100 | 51 | 26 | 25 | 29 |
| 500 | 175 | 57 | 50 | 44 |
| 1,000 | 271 | 74 | 65 | 46 |
| 2,000 | 403 | 99 | 86 | 49 |

Note: ~29 of the top 100 are function words (prepositions, pronouns, conjunctions) which are excluded from the learning pipeline by design.

### Top Frequency Gaps (excluding function words)
| Word | Gloss | Rank | Status |
|------|-------|------|--------|
| اللّٰهُ | God | 16 | encountered |
| اَلْيَوْمَ | today | 30 | encountered |
| رَئِيسٌ | chief, leader | 45 | not imported |
| أَكْثَر | more | 63 | not imported |
| يَوْم | day | 68 | not imported |
| مِصْرُ | Egypt | 76 | encountered |
| أخْبار | news | 80 | not imported |
| أَفْضَل | best | 86 | encountered |

"Encountered" words should naturally enter acquisition via auto-intro. "Not imported" words need to enter via topic introduction.

---

## 7. Projections (based on actual data)

**Actual study patterns**: 7.0 study days/week (100% consistency over 13 days), 13 sessions/study day median.

### Scenario 1: Current pace (11 grads/study day, linear)
| Milestone | Remaining | Days |
|-----------|-----------|------|
| Graded reader (400) | 182 | ~17 |
| A1 (500) | 282 | ~26 |
| A2 (1,200) | 982 | ~89 |
| B1 (2,000) | 1,782 | ~162 |
| B2 (4,000) | 3,782 | ~344 |

### Scenario 2: Recent 7-day trend (15.6 grads/study day)
| Milestone | Remaining | Days |
|-----------|-----------|------|
| Graded reader (400) | 182 | ~12 |
| A1 (500) | 282 | ~18 |
| A2 (1,200) | 982 | ~63 |
| B1 (2,000) | 1,782 | ~114 |
| B2 (4,000) | 3,782 | ~243 |

### Scenario 3: With deceleration (rate drops ~15% per 500 new words)
| Milestone | Days | Rate by then |
|-----------|------|-------------|
| Graded reader (400) | ~17 | 11.0/day |
| A1 (500) | ~26 | 9.3/day |
| A2 (1,200) | ~117 | 6.8/day |
| B1 (2,000) | ~247 | 4.9/day |
| B2 (4,000) | ~869 | 2.5/day |

The deceleration scenario is likely most realistic — as high-frequency/easy words are exhausted, the graduation rate will naturally slow. The root-aware stability boost should partially offset this for Arabic.

---

## 8. Tashkeel Readiness

- **Eligible for fading** (stability ≥ 30d): 11 words (5.7%)
- **Median stability**: 7.3 days
- **Stability distribution** (review words only):
  - 30-90d: 11
  - 7-30d: 100
  - 1-7d: 81
  - <1d: 1

**Recommendation**: Wait until ~50+ words exceed 30d stability before enabling tashkeel fading (estimated 2-3 weeks at current pace). The feature is ready and opt-in — default mode is "always" (full diacritics).

---

## 9. Scheduling Observations

### What's Working
1. **High retention** (93.3%) with strong weekly improvement trend
2. **Short sessions** (median 5) are effective — users engage frequently rather than in long sessions
3. **Same-day box advancement** enabled by high session frequency
4. **Acquisition pipeline** is healthy: 76 acquiring, none stuck

### Opportunities
1. **Increase introduction rate**: accuracy (89.8%) is above Wilson's 85% optimal → room for 12-15 new words/day
2. **Root-aware boost** should reduce review load for words in established root families
3. **Tashkeel fading** ready to enable when stability matures
4. **Frequency gaps**: auto-introduction should prioritize high-frequency unlearned words (أَوْ, بَعْد, بَيْن)

### Research Comparison

| Metric | Our Value | Benchmark | Source |
|--------|-----------|-----------|--------|
| Learning rate | 11/day | 7/day | Uchihara 2019 |
| Training accuracy | 89.8% | 85% optimal | Wilson et al. 2019 |
| Reviews to graduate | 7 median | 8-12 encounters | Uchihara 2019 |
| FSRS retention | 93.3% | 90% target | FSRS-6 default |
| Words for 95% coverage | 209/9,000 | 9K lemmas | Masrai & Milton 2016 |

---

## 10. Milestone Projections

At current graduation rate (~11 words/day, 7 study days/week, 218 known+learning):

| Milestone | Target | Current | Remaining | Linear | Deceleration |
|-----------|--------|---------|-----------|--------|-------------|
| Children's book | 150 | 218 | **ACHIEVED** | — | — |
| Graded reader (L1-3) | 400 | 218 | 182 | ~17d | ~17d |
| A1 complete | 500 | 218 | 282 | ~26d | ~26d |
| A2 | 1,200 | 218 | 982 | ~89d | ~117d |
| B1 | 2,000 | 218 | 1,782 | ~162d | ~247d |
| B2 | 4,000 | 218 | 3,782 | ~344d | ~869d |

Linear projections assume sustained 11 grads/day. Deceleration model assumes rate drops ~15% per 500 new words as easy/frequent words are exhausted.

---

## Files

| File | Description |
|------|-------------|
| `scripts/learning_analysis.py` | Analysis script (runs inside Docker container) |
| `/tmp/claude/analysis.json` | Raw production data |
| `/tmp/claude/sim_casual.csv` | 90-day casual simulation |
| `/tmp/claude/sim_calibrated.csv` | 30-day calibrated simulation |
| `/tmp/claude/sim_intensive.csv` | 30-day intensive simulation |
| `research/learning-report-2026-02-20.html` | Visual HTML report |
