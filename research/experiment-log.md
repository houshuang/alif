# Experiment Log

Running lab notebook for Alif's learning algorithm. Each entry documents what changed, why, what we expect, and how to verify.

---

## 2026-02-11 — Fix Variant Lemma Imports in OCR Pipeline

### Change
1. **Root cause identified**: The OCR pipeline (`ocr_service.py`) correctly computes `base_lemma` via CAMeL Tools morphology (e.g., "كراج" from "كراجك") but `process_textbook_page()` ignores it, using only the conjugated bare form for DB lookup. 54% of active words (368/676) had frequency rank 5000+ — mostly possessive/conjugated variants from textbook dialogues imported as separate lemmas.
2. **OCR prompt hardened**: Step 1 now explicitly requests dictionary base forms, not conjugated/possessive forms. Includes examples (كتابك → كتاب, يكتبون → كتب).
3. **base_lemma passthrough**: `extract_words_from_image()` now passes `base_lemma` from Step 2 morphology through to `process_textbook_page()`. Dedup uses base_lemma instead of bare form.
4. **Lookup priority**: `process_textbook_page()` tries base_lemma for DB lookup first, falls back to bare. When creating new lemmas, uses base_lemma for `lemma_ar_bare`.
5. **Post-import variant detection**: Added the same `detect_variants()` + `detect_definite_variants()` + `mark_variants()` pattern that all other import scripts (Duolingo, Wiktionary, AVP) already had. OCR was the only path missing this.
6. **Leech identification script**: `scripts/identify_leeches.py` queries for words with high review count but low accuracy. Supports `--suspend`, `--dry-run`, `--source`, `--threshold`.

### Hypothesis
Importing base forms instead of conjugated forms will eliminate the variant proliferation problem. The three-layer defense (improved prompt + base_lemma lookup + post-import variant detection) provides redundancy. Leech detection helps identify words consuming review time without progressing.

### Expected Effect
- Future textbook scans import ~50% fewer new lemmas (variants mapped to existing base forms)
- No more possessive forms (كراجك, جاكيتك) appearing as separate FSRS cards
- Production cleanup (variant merge + leech suspension) will consolidate ~100+ variant lemmas

### Verification
- 500 backend tests pass (6 new OCR tests for base_lemma handling)
- Run `cleanup_lemma_variants.py --merge` on production to consolidate existing variants
- Run `identify_leeches.py` on production to review unproductive words
- Test a real textbook page scan post-deploy to verify base form import

---

## 2026-02-11 — Word Frequency + CEFR Level Integration

(See previous entry — CAMeL MSA frequency backfill + CEFR level display across frontend)

---

## 2026-02-11 — Word Management: Suspend, Flag, Action Menu, Tab Consolidation

### Change
1. **Bug fix**: Suspended words were not filtered from sentence_selector.py or fsrs_service.py — they still appeared in review sessions. Fixed by adding `knowledge_state != "suspended"` filters.
2. **Suspend from anywhere**: New `POST /api/words/{id}/suspend` and `/unsuspend` endpoints (previously suspend only worked in Learn mode). Auto-reactivation when suspended words are re-encountered via OCR, imports, or learn mode.
3. **Content flag system**: New `ContentFlag` model + `POST /api/flags` endpoint. Flagged content gets background LLM evaluation (GPT-5.2) for auto-correction of wrong glosses, unnatural sentences, etc. Uses `ActivityLog` to track all flag resolutions.
4. **ActionMenu component**: Replaced AskAI floating button with generic "⋯" menu across all screens (review, learn, story, word detail). Menu includes: Ask AI, Suspend word, Flag translation, Flag sentence.
5. **Tab consolidation**: Reduced from 8 tabs to 6 — Scanner, Chats, Stats moved into new "More" tab with activity log section. Learn renamed to "New Words".

### Hypothesis
Making word suspension and content flagging accessible from the review flow (instead of only Learn mode) reduces friction when encountering problematic words. LLM-powered flag evaluation auto-corrects quality issues that would otherwise persist. Fewer tabs improves mobile navigation.

### Expected Effect
- Suspended words immediately stop appearing in review — less frustration with too-difficult words
- Flagged wrong translations get corrected within seconds (async background task)
- Tab bar is more navigable on mobile (6 tabs fits comfortably)

### Verification
- 494 backend tests pass (22 new tests for suspend/unsuspend/flags/activity)
- Manual: suspend a word → verify it doesn't appear in next review session
- Manual: flag a translation → verify background evaluation auto-corrects
- Deploy and verify on mobile

---

## 2026-02-11 — Word Frequency + CEFR Level Integration

### Change
Added `cefr_level` column to Lemma model. Created backfill script (`scripts/backfill_frequency.py`) that downloads CAMeL MSA Frequency Lists (11.4M surface forms from 12.6B tokens) and Kelly Project Arabic (9K lemmas with CEFR A1–C2 levels). Matches against existing lemma bare forms. Also computes Root.productivity_score from child lemma frequencies.

### Hypothesis
Showing frequency rank and CEFR level alongside words in Learn mode, word browser, word detail, and review lookup helps the learner gauge word importance. This is purely informational — does not change FSRS scheduling or review priority. The existing frequency_rank field was already wired into Learn mode's word selection algorithm (40% weight) but was always NULL.

### Expected Effect
- All or most existing lemmas get a frequency rank from CAMeL data
- ~30-50% of lemmas match the 9K-word Kelly list for CEFR levels
- Learner sees colored CEFR badges (A1 green → C2 purple) and frequency ranks in the UI
- Word selection in Learn mode becomes more accurate (currently all words score 0.3 fallback)

### Verification
1. Run `python scripts/backfill_frequency.py --dry-run` to see match rates
2. Run without `--dry-run` to populate data
3. Check word list API: `GET /api/words?limit=5` should show frequency_rank and cefr_level
4. Check UI: Learn mode cards, word browser, word detail page should show CEFR badges

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

---

## 2026-02-11 — Arabic Text Sanitization

### Problem

Running `update_material.py` after the diversity overhaul revealed that almost all recently imported words (from textbook OCR) had dirty `lemma_ar` values that caused 100% sentence generation failure:

- **Trailing punctuation**: `النَّرْوِيج؟`, `سنة.`, `مرحباً!`, `نعم،` — validator can't match tokens with punctuation
- **Slash-separated**: `الصَّفُّ/السَّنَةُ` — not a valid single token
- **Multi-word phrases**: `الْمَدْرَسة الثّانَوِيّة`, `روضة الأطفال` — never matches as single token
- **Full sentences as lemmas**: OCR extracted entire textbook sentences as vocabulary entries (e.g., `هَلْ عِنْدَكَ كَلْب؟`)

Out of ~20 words needing sentences, only 2 sentences were generated (for كثير, the only clean entry).

### Changes Made

1. **`sanitize_arabic_word()` function** (`sentence_validator.py`): Strips leading/trailing punctuation (Arabic + Latin), handles slash-separated (takes first), detects multi-word (takes first word + warns). Does NOT strip diacritics. Also added `compute_bare_form()` helper.

2. **DB cleanup script** (`scripts/cleanup_lemma_text.py`): Scans all lemmas, applies sanitization. Categories: punctuation fix (update in place), slash fix, multi-word delete (with merge into existing if first word matches), dedup merge (after cleanup, two lemmas match same bare form).

3. **Hardened all injection points**:
   - OCR prompt (`ocr_service.py`): Added instructions to not include punctuation, multi-word phrases, or slash-separated alternatives
   - OCR extraction (`_step1_extract_words`): Runs `sanitize_arabic_word()` on each extracted word, rejects multi-word
   - OCR lemma creation (`process_textbook_page`): Sanitizes before `Lemma()` creation
   - `import_duolingo.py`: Replaced `is_multi_word()` with `sanitize_arabic_word()`
   - `import_wiktionary.py`: Replaced manual `" " in word` check
   - `import_avp_a1.py`: Replaced manual asterisk removal + multi-word check

4. **Defensive check in `material_generator.py`**: Sanitizes `lemma.lemma_ar` before computing `target_bare` for sentence generation, skips uncleanable entries.

5. **Tests**: 17 new tests in `test_sentence_validator.py` (TestSanitizeArabicWord: 14 tests, TestComputeBareForm: 3 tests).

### Cleanup Results (production)

- **1983 lemmas scanned**
- Fixed punctuation: 38
- Fixed slash-separated: 2
- Deleted multi-word/empty: 82
- Merged duplicates: 108
- **Total changes: 230** (11.6% of all lemmas were dirty)

LLM verification confirmed all 230 changes were safe to apply.

### Hypotheses

- **H11**: Cleaned lemmas will now generate sentences successfully via `update_material.py` — **confirmed**, 133 sentences generated
- **H12**: Future OCR imports will produce clean single-word entries with sanitization at both prompt and code levels

---

## 2026-02-11 — Abbreviation Filter & Conservative TTS Audio

### Problem: Abbreviations

DB contained 6 single-character lemmas that are abbreviations or clitics, not real vocabulary:
- وَ (and), رَ (see), ج (plural marker), ٥ (digit 5), ـهُ (him/his suffix), ـي (my suffix)
- These entered via Duolingo import, OCR textbook scan, and the cleanup script's first-word extraction
- `ج` was trying (and failing) to generate sentences — can never match as a token

### Problem: TTS Audio Cost

`update_material.py` Step B was finding **1,249 sentences** eligible for audio generation (criterion: `times_correct >= 1` for all words). This is far too aggressive — most of these sentences won't be used in listening mode for weeks. Listening mode requires `times_seen >= 3` and `stability >= 7d`.

### Changes Made

1. **Abbreviation filter in `sanitize_arabic_word()`** (`sentence_validator.py`): After all cleaning, computes bare form. If `len(bare) < 2`, adds `"too_short"` warning. All callers (OCR, Duolingo, Wiktionary, AVP imports, material_generator) now check for this warning.

2. **Conservative audio eligibility** (`update_material.py`): Changed `get_audio_eligible_sentences()` from `times_correct >= 1` to `times_seen >= 3 AND stability >= 3.0 days`. Audio is only generated when words are approaching listening readiness. Reduces eligible from 1,249 → 0 currently (early learning phase), growing naturally as words mature. Also filters inactive sentences.

3. **Deleted 6 abbreviation lemmas** from production DB (all had 0-2 reviews from auto-credit).

4. **3 new tests**: single_char_abbreviation, single_char_with_diacritics, two_char_word_ok.

### Audio Coverage

- 406 active sentences already have audio (solid listening backlog)
- 1,773 without audio will get it as word stability builds up
- No wasted ElevenLabs credits on sentences that can't be used yet
