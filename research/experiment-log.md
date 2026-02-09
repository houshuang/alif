# Experiment Log

Running lab notebook for Alif's learning algorithm. Each entry documents what changed, why, what we expect, and how to verify.

---

## 2026-02-09 — Initial Production Analysis & Baseline

### Findings

First full day of real usage analyzed via SSH to production. See [analysis-2026-02-09.md](./analysis-2026-02-09.md) for detailed queries and data.

**Vocabulary state**:
- 216 tracked words (182 from Duolingo import, 34 introduced via Learn mode)
- 0 words with `source="encountered"` — collateral credit auto-creates ULK but all existing words entered via study/duolingo
- Knowledge states: mostly "learning", few "known"

**FSRS stability distribution**:
- 118 words with stability < 0.5 days (55%)
- 95 words with stability 1-3 days (44%)
- 0 words above 3 days — nothing has solidified after first day of use
- This is expected for day 1; will be the key metric to watch over coming days

**Accuracy & comprehension**:
- 57 words with 0% accuracy (times_seen >= 1, times_correct == 0) — 31% of seen words
- 78% of sentence reviews rated "partial", 22% "understood", 0% "no_idea"
- Most "partial" reviews have only 1 word marked as missed — user reports this feels normal

**Offline sync**:
- All 59 sentence reviews synced cleanly, all with `source="sync"`
- Zero duplicates detected
- JSONL event counts match DB review counts exactly

**Session assembly**:
- 39 session_start events for 59 reviews (likely from frontend code reloads during development)
- Greedy set cover working: sessions cover multiple due words per sentence

**Open questions**:
- Are the 57 always-failing words truly unknown, or just never the focus word in any sentence?
- Is 78% partial comprehension a healthy steady state, or too high?
- Will stability distribution naturally shift as more reviews accumulate?

### Changes Made

1. **Rich statistics endpoint** (`GET /api/stats/deep-analytics`): 7 new query functions — stability distribution (7 buckets from <1h to 30d+), retention rates (7d/30d), state transitions (today/7d/30d by parsing fsrs_log_json), comprehension breakdown, struggling words, root coverage, recent sessions. Frontend: 5 new sections on stats screen (VocabularyHealth, LearningVelocity, Comprehension, StrugglingWords, RootProgress).
2. **Re-introduction cards**: Detection in `build_session()` — words with `times_seen >= 3, times_correct == 0` removed from sentence pool, returned as `reintro_cards` (max 3/session). Rich card data: root, root family, forms, grammar, example. New endpoint `POST /api/review/reintro-result` — "Remember" submits FSRS rating 3 (Good), "Show again" submits rating 1 (Again). Frontend: ReintroCardView phase shown after grammar lessons, before sentence cards.
3. **Context diversity**: Added `diversity = 1.0 / (1.0 + times_shown)` multiplier to sentence candidate scoring in both initial scoring and greedy set cover re-scoring. Effect: never-shown sentences score 1.0x, shown-once 0.5x, shown-twice 0.33x.
4. **Shared card components**: Extracted FormsRow, GrammarRow, PlayButton, posLabel from learn.tsx into `frontend/lib/WordCardComponents.tsx` for reuse across Learn mode and reintro cards.
5. **Experiment tracking**: Added section 6 to CLAUDE.md requiring all agents to document algorithm changes. Created this log and analysis-2026-02-09.md.
6. **Tests**: 16 new tests (test_deep_analytics.py: 7, test_reintro.py: 9). Total: 408.

### Hypotheses

- **H1**: Re-introducing struggling words via focused cards (with root, forms, example) will lead to them passing within 1-2 re-introductions, vs indefinite cycling in sentences
- **H2**: Context diversity (different sentence each review) will improve retention compared to seeing the same sentence repeatedly
- **H3**: Stability distribution will shift rightward (toward higher stability) over the next 3-5 days as FSRS schedules catch up

### How to Verify (next analysis)

- Compare struggling word count: should decrease if H1 is correct
- Check `times_shown` distribution on sentences: should be more uniform if H3 is working
- Compare stability distribution to today's baseline
- Track how many words transition from learning → known over next week
