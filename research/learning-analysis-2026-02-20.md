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

| Top N Words | In Corpus | Known | Coverage |
|-------------|-----------|-------|----------|
| 100 | 51 | 25 | 49% |
| 500 | 175 | 50 | 29% |
| 1,000 | 271 | 65 | 24% |
| 2,000 | 403 | 86 | 21% |

### Top Frequency Gaps
| Word | Gloss | Rank | Status |
|------|-------|------|--------|
| أَوْ | or | 10 | not imported |
| اللّٰهُ | God | 16 | encountered |
| بَعْد | after | 20 | not imported |
| بَيْن | between | 23 | not imported |
| كانَ | to be | 24 | lapsed |
| قَبْل | before | 37 | not imported |

These high-frequency gaps should be addressed by the topic-based introduction system.

---

## 7. Simulation Projections

### Casual Profile (3.5 study days/week, 1-3 sessions/day)
- **Day 30**: 219 known (+46 from baseline 173)
- **Day 60**: 248 known
- **Day 90**: 256 known
- Growth slows significantly with casual study — only ~1 word/study day

### Calibrated Profile (7 days/week, 8-18 sessions/day — matches production)
- **Day 30**: ~190 known (simulation showed review avalanche — too many due words)
- Warning: review load > 180 words/day is unsustainable

### Intensive Profile (7 days/week, 3-7 sessions/day)
- **Day 30**: similar review avalanche pattern
- Both intensive profiles hit the review ceiling where daily due count exceeds session capacity

### Simulation Insights
1. **Review avalanche** is the dominant constraint — beyond day 15-20, more words = more daily reviews
2. The casual profile avoids avalanche but learns slowly
3. **Optimal pace**: the calibrated profile's 8-18 sessions/day is actually close to optimal for the current algorithm; the bottleneck is review load management, not session count
4. **Key optimization**: the root-aware stability boost should reduce review load for well-connected words

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

At current graduation rate (~11 words/day, 7 study days/week):

| Milestone | Words Needed | Current | Remaining | Est. Days |
|-----------|-------------|---------|-----------|-----------|
| Children's book | 150 | 209 | **ACHIEVED** | — |
| Graded reader (L1-3) | 400 | 209 | 191 | ~17 |
| A1 complete | 500 | 209 | 291 | ~26 |
| A2 | 1,200 | 209 | 991 | ~90 |
| B1 | 2,000 | 209 | 1,791 | ~163 |

Note: These are linear projections. Actual progress depends on study consistency, review load management, and natural deceleration as words become less frequent.

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
