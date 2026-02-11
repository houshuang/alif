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

---

## 2026-02-10 — Day 3 Analysis & Rich Intro Cards

### Findings

See [analysis-2026-02-10.md](./analysis-2026-02-10.md) for full data.

**Vocabulary**: 282 words (+31%), driven by 85 textbook scanner imports. 97 words (34%) never reviewed.

**Stability progress**: 10 words reached genuine 7d+ stability (from 0 at baseline). Most reviewed words still in <0.5d (40%) and 1-3d (49%) buckets. H3 partially confirmed — progress is real but slow.

**Accuracy**: Bimodal split — 83 words at 80%+ accuracy (45%), 48 at 0% (26%, down from 57). Study-mode words underperform Duolingo (19% vs 70% accuracy).

**Comprehension declining**: "Understood" rate dropped 44% → 24% → 22% over 3 days. Partial dominates at 69%.

**Reintro cards (H1)**: 57% remembered on first attempt. Interesting "fail-then-remember" pattern: 3 words failed first reintro, succeeded 10 min later. Short-term recall improves but FSRS stability remains <0.2d for all reintro words.

**Sentence diversity (H2)**: Confirmed working. 87% of sentences shown only once, max 3 shows. Pool of 1,059 with 426 never shown.

**~~Collateral credit concern~~ (retracted)**: 68% of reviews tagged "collateral" — this was flagged as a concern but is actually working as designed. Reading a sentence reviews ALL words equally. If the user doesn't mark a word, they're confirming they know it. The scheduling reason for showing the sentence is irrelevant to the validity of the comprehension signal. Same principle as story completion or textbook scanning.

### Changes Made

1. **Rich intro cards mid-session**: Intro candidates now shown as full cards at positions 4 and 8 during review (forms, grammar, examples, audio, root family). Learn/Skip actions — user controls what to learn. Previously only shown as tiny pills on session completion.
2. **Removed intro gates**: Dropped 75% accuracy and 4-item minimum requirements for intro candidates. User controls via Learn/Skip buttons. Will monitor rate via interaction logs.
3. **WordInfoCard overflow fix**: Added flexShrink, maxWidth on sibling pills, numberOfLines on gloss text.
4. **WordInfoCard → word detail navigation**: "View details ›" link navigates to /word/[id] full detail page.
5. **Button layout**: Back-phase buttons moved rightward — "Know All" (most common) nearest to right thumb.

### New Concerns

- **Declining "understood" rate**: 44% → 22% over 3 days — monitor if this stabilizes or indicates difficulty creep.

### Bug Fix: Textbook Scanner FSRS Credit

Textbook scanner (`process_textbook_page`) was creating ULK records and incrementing `total_encounters` but NOT submitting FSRS reviews. This meant scanned words had `times_seen=0` and `times_correct=0` — appearing as "never reviewed" despite the user having physically seen them in the textbook. Fixed by calling `submit_review(rating=3, review_mode="textbook_scan")` for every word (new, existing with ULK, existing without ULK). Scanned words now get proper FSRS scheduling (~1 day first interval) and appear as reviewed.

### Hypotheses (new)

- **H4**: Removing intro gates will increase word introduction rate without overwhelming the learner, since Learn/Skip gives user control
- **H5**: The 48 zero-accuracy words are "leeches" that need intervention beyond standard SRS scheduling

### Retracted

- **~~H6~~**: "Collateral credit inflation" — retracted. All words in a sentence are genuinely reviewed by the learner. Unmarked words represent real comprehension, not passive inflation. The credit_type field is metadata only.

### How to Verify (next analysis)

- Track intro card Learn vs Skip rates — are users engaging or always skipping?
- Monitor daily new-word introduction rate — has it increased? Is it sustainable?
- Check if 48 zero-accuracy words decrease with continued reintro card exposure
- Compare comprehension trend — is the decline stabilizing or continuing?

---

## 2026-02-11 — Sentence Diversity Overhaul

### Problem

DB analysis revealed severe diversity issues in the 2,075-sentence corpus, accumulated during early learning when vocabulary was ~10-50 words:

- **هل dominance**: 30% of all sentences start with هل (614 sentences)
- **محمد overuse**: Single proper noun in 16% of sentences (329)
- **Overexposed scaffolds**: Known words like جميلة (204 sentences), جديد (85), مدرسة (83) dominate as scaffold words but are fully learned
- **Tight sentence length**: All 3-7 words (avg 5.0), no variation
- **No retirement**: Once generated, sentences live forever
- **update_material.py gap**: Backfill (Step A) generated sentences without diversity params — no avoid list, no weighted sampling

See [analysis-2026-02-11.md](./analysis-2026-02-11.md) for full baseline metrics.

### Changes Made

1. **Scaffold freshness penalty** (`sentence_selector.py`): New `_scaffold_freshness()` multiplier in scoring. For each scaffold word, penalty = min(1.0, 8 / times_seen). Geometric mean, floored at 0.3. Effect: sentences with over-reviewed known words score lower.

2. **Starter diversity in LLM prompts** (`llm.py`): Added instructions to both system prompts: "Do NOT default to هَلْ questions", "Use different subjects — do NOT always use مُحَمَّد", "Vary starters". Removed "Questions with هَلْ" from difficulty guide.

3. **Stronger avoid list** (`sentence_generator.py`): MAX_AVOID_WORDS 10→20. Added ALWAYS_AVOID_NAMES (محمد, احمد, فاطمة, علي) — always in avoid list.

4. **Post-generation diversity rejection** (`sentence_generator.py`): After validation passes, _check_scaffold_diversity() rejects sentences with 2+ scaffold words appearing in 15+ existing sentences. Rejected words fed back to LLM on retry. MAX_RETRIES 3→5.

5. **update_material.py diversity fix**: Backfill now computes content_word_counts, avoid_words, and uses sample_known_words_weighted() — matching pregenerate_material.py.

6. **Sentence retirement** (`models.py`, `sentence_selector.py`, `retire_sentences.py`): Added is_active column. Selector filters inactive sentences. New script retires overexposed sentences (overexposure index < 0.3) and هل-starters when alternatives exist, keeping ≥3 active per target word. Backfill counts filter by is_active.

### Hypotheses

- **H7**: Scaffold freshness penalty will shift sessions toward sentences with fresher vocabulary contexts, reducing the feeling of repetitiveness
- **H8**: Starter diversity instructions + post-generation rejection will reduce هل dominance from 30% to <10% in newly generated sentences
- **H9**: Retiring overexposed sentences and regenerating will create a more balanced corpus within 1-2 update_material.py cycles
- **H10**: محمد usage will drop significantly with ALWAYS_AVOID_NAMES enforcement

### How to Verify (in 3-5 days)

- Compare sentence starter distribution (% هل, % هذا) — should decrease
- Compare محمد sentence count — should plateau (no new sentences with محمد)
- Check scaffold freshness distribution across sessions — should shift upward
- Monitor comprehension rates — fresher contexts should help or at least not hurt
- Count newly generated sentences and verify they pass the diversity check
