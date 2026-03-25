# Acquisition Rate Analysis — 2026-02-20

> Deep dive combining literature review + production data analysis to determine optimal sustainable introduction rate and identify algorithm tweaks.

---

## 1. Maximum Sustainable Introduction Rate

### What the Literature Says

| Source | Sustainable Rate | Duration | Daily Time |
|--------|-----------------|----------|------------|
| Anki community consensus | 10-20 cards/day | Indefinite | 20-30 min |
| Wozniak (personal data) | ~28 items/day | 1 year | 41 min |
| Antimoon (Szynalski) | ~6 items/day | 2.5 years | Selective |
| Geoff Ruddock (3-yr retro) | ~18 cards/day | 3 years | 17 min |
| FSRS simulations | 10 cards/day | 1 year | 20 min |

**The 10x rule**: At steady state, review load ≈ 10× daily new cards. 10 new/day → ~100 reviews/day → ~20 min.

**Wozniak's ceiling**: "Only a genius may learn 30 new vocabulary items a day." His own record was 28/day at 41 min total study time. Few users sustain >200 repetitions/day.

**FSRS advantage**: FSRS achieves same retention with 20-30% fewer reviews than SM-2, effectively raising the sustainable ceiling.

### What Our Production Data Shows

- **Average**: 28 new words/day (close to Wozniak's ceiling)
- **Peak**: 58 on Feb 16
- **Current cap**: `MAX_AUTO_INTRO_PER_SESSION=10`, accuracy-gated (0 at <70%, 4 at 70-85%, 7 at 85-92%, 10 at ≥92%)
- **Session frequency**: 13 sessions/day median → theoretical max 130 new/day, but accuracy gates and available encountered words limit this

### Why Alif Can Sustain Higher Rates Than Traditional SRS

Traditional SRS (Anki/SuperMemo) reviews are individual flashcards — one word per review. Alif uses **sentence-based review** where each sentence exposes the learner to 5-8 words simultaneously. This means:

- The "11.5 reviews/word/week" figure is inflated by collateral credit
- Actual user effort: ~65 sentences/day, not 446 individual flashcard reviews
- Cognitive load per new word is lower because words appear in natural context
- Known scaffold words get "free" reviews as part of sentences targeting other words

**Verdict**: The current 28 new/day intro rate is at the literature ceiling for traditional SRS, but Alif's sentence-based model distributes the cognitive load more efficiently. The accuracy-based gating (`_intro_slots_for_accuracy`) is already doing the right thing — slowing down when the learner struggles.

---

## 2. The 85% Rule — Correction Needed

### What Wilson et al. (2019) Actually Found

The paper in Nature Communications demonstrated that **stochastic gradient-descent based learning algorithms on binary classification tasks** have an optimal error rate of ~15.87% (85% accuracy). This was tested on:
- One-layer perceptrons with artificial stimuli
- Two-layer neural networks on MNIST digit classification
- Monkey perceptual learning models

### Why It Does NOT Apply to Vocabulary Learning

1. Vocabulary learning is **declarative memory retrieval**, not gradient-based perceptual classification
2. The paper explicitly limits scope to binary classification tasks
3. SRS has its own difficulty framework: **Bjork's "desirable difficulties"** (spacing, interleaving, retrieval practice)
4. The 85% number coincidentally overlaps with SRS retention targets but for entirely different theoretical reasons

### Current Usage in Alif Docs

The 85% rule is cited in:
- `docs/scheduling-system.md` line 73: "Optimal training accuracy ~85% | Wilson et al. 2019"
- `research/deep-research-compilation-2026-02-12.md`: "The 85% accuracy rule maximizes both learning and motivation"
- `research/learning-analysis-2026-02-20.md`: "Wilson et al. (2019, Nature) found 85% as the optimal training accuracy"

**Recommendation**: Replace with Bjork's desirable difficulties framework. The accuracy-based intro ramp (70/85/92 thresholds) is still reasonable, but the theoretical justification should cite Bjork, not Wilson. The key insight: there is no fixed optimal accuracy — it depends on the learner and the specific difficulty manipulation.

---

## 3. Algorithm Tweaks — Specific Recommendations

### 3.1 Leech Recovery: Reset Stats on Reintroduction (HIGH PRIORITY)

**Problem**: 0% leech recovery rate. 36 words with leech_count > 0, none recovered.

**Root cause**: When a leech is reintroduced after cooldown, it retains its cumulative `times_seen` and `times_correct` stats. Since leech detection uses cumulative accuracy (`times_correct / times_seen < 50%`), the word immediately re-triggers leech status after just 1-2 failures, because the historical bad stats dominate.

**Example**: A word seen 10 times, correct 3 times (30% accuracy). After 7-day cooldown, it's reintroduced. It needs to reach 50% accuracy overall — meaning 4 correct out of the next 4 reviews just to break even. One failure and it's re-leeched.

**Fix**: On leech reintroduction, partially reset stats:
```python
# In check_leech_reintroductions(), after setting leech_suspended_at = None:
ulk.times_seen = max(3, ulk.times_seen // 2)  # halve history, min 3
ulk.times_correct = max(2, ulk.times_correct)  # preserve at least 2 correct
```

This gives the word a fighting chance: with halved history and preserved correct count, a few successful reviews can push it above 50%.

**Alternative**: Use a sliding window (last N reviews) instead of cumulative accuracy for leech detection. This is more principled but requires storing per-word review history.

### 3.2 First-Session Warmup (MEDIUM PRIORITY)

**Problem**: First review of the day has 78.1% accuracy vs 94.8% for later reviews. This 16.7pp gap is a significant warmup effect.

**Impact**: The first session likely has more failures, which:
- Inflates leech detection (more misses on words that would be fine after warmup)
- Discourages the learner at the start of a study block
- May incorrectly gate new word introduction (accuracy drops below thresholds)

**Fix options**:
1. **Start with high-stability words**: First session of the day should prioritize words with stability >7 days (easy warm-up). Currently, session building sorts by due date — already-overdue low-stability words come first.
2. **Exclude first-session accuracy from intro gating**: Don't count the first session's accuracy toward `_intro_slots_for_accuracy` calculation.
3. **Gentler first session**: Cap new introductions at 2 for the first session of each day.

**Recommendation**: Option 1 (prioritize high-stability first) is the simplest and most natural. The user warms up on easy words, gets into flow, then tackles harder ones.

### 3.3 Daily Introduction Cap (LOW PRIORITY — MONITOR)

**Current state**: No daily cap, only per-session cap of 10. With 13 sessions/day, actual average is 28/day.

**Assessment**: 28/day is at Wozniak's ceiling but sustainable given sentence-based review. The accuracy gate is self-regulating. However, if sessions per day increases further or accuracy stays very high (>92%), the system could push 40+ new/day.

**Recommendation**: Add a soft daily cap as a safety valve:
```python
MAX_DAILY_INTRODUCTIONS = 25  # soft cap, reduces to 0 once reached
```

This prevents runaway introduction on unusually high-session days while staying close to current observed rate. Not urgent — the accuracy gate handles this well already.

### 3.4 Correct the 85% Citation (LOW PRIORITY — DOCUMENTATION)

Replace Wilson 2019 citations in scheduling docs with:
- **Bjork & Kroll 2015**: Desirable difficulties in vocabulary learning
- **Nakata 2023**: Long initial spacing intervals produce best long-term retention
- **FSRS default**: 90% desired retention (not 85% training accuracy)

The accuracy thresholds in `_intro_slots_for_accuracy` (70/85/92) can stay — they're pragmatically sensible even though the theoretical basis is Bjork, not Wilson.

### 3.5 Things That Are Working Well (NO CHANGE NEEDED)

1. **Acquisition pipeline**: Healthy funnel (12→37→22), no stuck words, 17.4% reset rate is appropriate
2. **Root-aware graduation boost**: Just implemented, should reduce review load for Arabic root families
3. **Accuracy-based introduction ramp**: Self-regulating, prevents overload
4. **Session frequency model**: Short, frequent sessions (median 5 sentences) enable same-day box advancement
5. **Retention trend**: 78.3% → 90.0% → 97.5% weekly improvement — system is stabilizing
6. **FSRS retention at 93.3%**: Above the 90% target, healthy

---

## 4. Summary of Recommended Changes

| Change | Priority | Effort | Expected Impact |
|--------|----------|--------|-----------------|
| Leech stat partial reset on reintro | HIGH | Small (10 lines) | Fix 0% leech recovery |
| Correct Wilson 85% citations | LOW | Docs only | Intellectual honesty |
| First-session warmup priority | MEDIUM | Medium (session builder) | ~5% first-session accuracy boost |
| Daily introduction cap (25) | LOW | Small | Safety valve for edge cases |

---

## 5. References

- Wozniak, P. "Knowledge acquisition rate." SuperMemo.guru.
- Wilson, R.C. et al. (2019). "The eighty five percent rule for optimal learning." Nature Communications 10, 4646.
- Bjork, R.A. & Kroll, J.F. (2015). "Desirable difficulties in vocabulary learning." American Journal of Psychology 128(2), 241-252.
- Nakata, T. (2023). "Costs and benefits of spacing for second language vocabulary learning." Language Learning 73(3), 799-834.
- Uchihara, T. et al. (2019). "To what extent is incidental vocabulary learning possible?" Studies in Second Language Acquisition 41(2), 419-440.
- FSRS FAQ: https://faqs.ankiweb.net/frequently-asked-questions-about-fsrs.html
