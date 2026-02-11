# Experiment Log

Running lab notebook for Alif's learning algorithm. Each entry documents what changed, why, what we expect, and how to verify.

---

## 2026-02-11: Review Undo / Back Navigation

**Problem**: During sentence review, if the user taps "Got it" by mistake, there's no way to go back and correct the rating. Reviews must be submitted immediately per-card (user often leaves mid-session), so deferring to session end isn't an option.

**Change**: Added undo support — submit immediately as before, but allow going back by undoing the submitted review:
1. `fsrs_service.submit_review()` now snapshots pre-review state (card, times_seen, times_correct, knowledge_state) in `fsrs_log_json`
2. New `undo_sentence_review()` in sentence_review_service.py: finds ReviewLog entries by client_review_id prefix, restores pre-review FSRS state, deletes logs, resets sentence metadata
3. New endpoint `POST /api/review/undo-sentence` with idempotent behavior
4. Frontend: `removeFromQueue()` in sync-queue.ts, `unmarkReviewed()` in offline-store.ts, `undoSentenceReview()` in api.ts

**Tests**: 5 new tests in test_sentence_review.py: undo restores FSRS state, deletes review logs, restores sentence metadata, idempotent when not found, API endpoint integration.

**Expected effect**: Users can correct mistaken ratings without restarting the session or having incorrect FSRS data.

---

## 2026-02-11: Sentence Pipeline Cap + Due-Date Priority

**Problem**: Generating 3 sentences per word for all 592 words is wasteful — user can't review more than ~200 sentences in 6 hours. Many generated sentences sit idle while more urgently needed ones don't exist.

**Change**: Modified `update_material.py`:
- `MIN_SENTENCES` reduced from 3 to 2
- Added `TARGET_PIPELINE_SENTENCES = 200` cap
- New `get_words_by_due_date()` sorts words by FSRS due date (most urgent first)
- Step A generates sentences by due-date priority with budget cap
- Step C also respects pipeline capacity

**Expected effect**: Prioritizes sentence generation for words the user will actually review soon. Reduces wasted API calls and generation time.

---

## 2026-02-11: Word List Smart Filters + Next Up Tab

**Problem**: Word list was a flat searchable list with state filters only. Hard to find specific word categories (leeches, struggling words, recently learned).

**Change**: Added smart filter tabs to word browser:
- **Leeches**: words with 6+ reviews and <50% accuracy (sorted worst first)
- **Struggling**: 2+ failures in last 4 ratings
- **Recent**: learning state with 4 or fewer reviews
- **Solid**: knowledge score ≥ 70% (sorted best first)
- **Next Up**: shows learn algorithm's top 20 candidates with score breakdown (frequency, root familiarity, siblings known)

Also added category tabs (Vocabulary/Function/Names), review sparklines on word cards, two-column grid layout.

**Expected effect**: Better visibility into learning progress and word health. Next Up tab surfaces the algorithm's candidate ranking for transparency.

---

## 2026-02-11: Switch Sentence Generation to GPT-5.2

**Problem**: Gemini Flash produces unnatural Arabic sentences. Example: `جرس الراديو في غرفة الصالون` ("the bell of the radio in the room of the living room") — uses wrong collocation (جرس with radio) and unnatural hybrid phrasing (غرفة الصالون). For a language learning app, incorrect collocations teach wrong usage.

**Change**: Switch all sentence generation from Gemini 3 Flash to GPT-5.2 (model_override="openai"). Gemini stays as primary for non-sentence tasks (variant detection, grammar tagging, etc).

**Files changed**: llm.py (defaults), sentence_generator.py, generate_sentences.py, pregenerate_material.py, update_material.py, analyze_word_distribution.py

**Verification**: Added `verify_sentences.py` — sends all existing LLM sentences to GPT-5.2 in parallel batches of 20 for naturalness evaluation. Retires (soft-deletes) flagged sentences. The existing `update_material.py` cron regenerates replacements with GPT-5.2.

**Expected effect**: Higher quality Arabic sentences, fewer semantic errors and unnatural collocations. Cost increase negligible at current scale.

**Verification plan**: Run verify_sentences.py --dry-run first to assess scope, then live run + regenerate via update_material.py.

---

## 2026-02-11: Proper Noun Handling in Story Import

**Problem**: Story imports create Lemma entries for all unknown words, including proper nouns (personal names like زهير, place names like دراج). These clutter Learn mode with non-vocabulary items.

**Change**: Updated `_import_unknown_words()` to detect proper nouns via LLM (new `name_type` field in prompt response). Proper nouns get `is_function_word=True` and `name_type="personal"/"place"` on StoryWord instead of creating Lemma entries. They remain tappable in the story reader and show "personal name" or "place name" in the lookup panel.

**Data model**: Added `name_type` column (nullable String) to StoryWord model. New alembic migration.

**Backfill**: Converted 6 existing proper nouns (2 personal, 4 place) and 1 misclassified function word (إذا) from the pre-existing story imports. Deleted their orphaned Lemma entries.

**Expected effect**: Learn mode only surfaces real vocabulary words from stories. Story readiness calculation excludes proper nouns (treated like function words). Story reader shows appropriate labels when tapping names.

---

## 2026-02-11: Complete Story Import → Learn → Read Pipeline

**Problem**: The story import pipeline was fundamentally incomplete. Three breaks prevented the intended flow (import story → discover unknown words → learn them → read the story):
1. `complete_story()` skipped words without ULK records — no FSRS credit for newly encountered words
2. Story import silently dropped unknown words (`lemma_id=None`) instead of creating Lemma entries
3. No word provenance tracking — users couldn't see where a word came from

**Changes**:
- **Auto-create ULK on completion**: Removed `if not ulk: continue` guards from `complete_story()`, `skip_story()`, `too_difficult_story()`. `submit_review()` already auto-creates ULK records.
- **Unknown word import pipeline**: New `_import_unknown_words()` function in story_service.py. For words with no lemma match after morphological fallback: CAMeL analysis → LLM batch translation → creates Root + Lemma entries with `source="story_import"` and `source_story_id`. NO ULK created — words become Learn mode candidates via existing `story_bonus` scoring.
- **`source_story_id` on Lemma model**: New FK column links lemmas to their source story. New alembic migration.
- **Word provenance on detail screen**: Backend returns `source_info` (type + story_id + story_title). Frontend shows "From story: [title]" badge with tap-to-navigate.
- **Readiness recalculation**: `_recalculate_story_counts()` runs after unknown word import so readiness_pct is accurate.

**Expected effect**: The full loop now works: import a story → all content words get Lemma entries → unknown words appear in Learn mode (with `story_bonus = 1.0` priority) → user learns them → story readiness increases → user reads and completes the story → all words get FSRS credit.

**Verification**: Import a story with unknown words. Check that: (1) words show proper glosses when tapped, (2) unknown words appear in Learn mode candidates, (3) completing the story gives FSRS credit to all words including previously-unseen ones, (4) word detail shows "From story: [title]" badge.

---

## 2026-02-11: Morphological Fallback for Story Word Lookup + Reader UI Declutter

**Problem 1**: Conjugated forms like قالت (she said) show "not in vocabulary" in story reader. The `lookup_lemma()` function uses clitic stripping + forms_json matching, but verb conjugation suffixes (ت feminine, وا plural, etc.) are neither clitics nor indexed in forms_json.

**Change**: Added CAMeL Tools morphological fallback in `_create_story_words()`. When `lookup_lemma()` fails, `find_best_db_match()` runs all CAMeL analyses and matches against known DB lemma bare forms. Results cached per bare_norm to avoid re-analyzing the same form.

**Expected effect**: Conjugated verbs, broken plurals, and other inflected forms now resolve to their base lemma during story import/generation. Previously unresolved words gain lemma_id, enabling proper lookup with gloss, root, and transliteration.

**Problem 2**: Fixed bottom bar with Complete/Skip/Too Hard buttons consumed ~80px of screen space permanently, making reading feel cramped.

**Change**: Moved action buttons from fixed bottom bar to end of scroll content. Complete is a full-width green button; Skip and Too Hard are text-only secondary links below it. The lookup panel remains as the only fixed element at the bottom.

**Verification**: Import a story containing conjugated verbs (قالت, ذهبوا, يقرأ). Tap each word — should now show proper gloss and root instead of "not in vocabulary". Scroll to bottom to see action buttons.

---

## 2026-02-11 — Design Pass: Stories & Words Screens

### Problem
Stories list and words list screens looked dated compared to the recently polished review, learn, and word detail screens. Specific issues:
- **Stories**: Small Arabic titles (20px with 0.85 opacity), cramped card layout, centered modals (not bottom-sheet), bare trash icon for delete, thin progress bars, text-only badges, small action buttons
- **Words**: No search icon, wrapping filter chips (messy on mobile), small Arabic text (20px), single-letter state badges ("K" instead of "Known"), no clear button on search

### Changes
**Stories screen** (`frontend/app/stories.tsx`):
- Arabic titles: 20px → 24px with proper Scheherazade font, removed opacity reduction, added lineHeight 34
- Cards: padding 16→18, borderRadius 12→14, restructured with cardHeader/cardTitleArea/cardFooter layout
- Modals: centered → bottom-sheet style (justifyContent flex-end, top-rounded corners 20px), added close X button in header, tap-outside-to-dismiss via overlay pressable
- Delete button: bare trash icon → circular surfaceLight button with close icon (28x28)
- Badges: added inline icons (sparkles for generated, clipboard for imported), added status badges for non-active stories
- Progress bar: 4px → 5px
- Action buttons: paddingVertical 12→14, borderRadius 10→12, gap 10→12
- Empty state: larger icon (48→56), better text hierarchy
- Generating state: replaced ActivityIndicator with sparkles icon

**Words screen** (`frontend/app/words.tsx`):
- Search: wrapped in container with Ionicons search icon + clear (close-circle) button
- Filters: wrapping View → horizontal ScrollView (no more line-wrapping on mobile)
- Arabic text: 20px → 24px (`arabicMedium`) with lineHeight 36
- English gloss: 14px secondary → 15px with medium weight (more readable)
- State badges: single letter on solid color → full word ("Known", "Learning") on tinted background (color + "20" alpha)
- POS shown in accent color as distinct metadata element
- Word rows: padding 14→16, borderRadius 10→12
- Added proper empty state with icon + contextual hint text
- Added error state with warning icon

### Expected Effect
Visual consistency across all main screens. No behavioral or algorithmic changes.

---

## 2026-02-11 — Story Reader Redesign

### Changes
Full design pass on `frontend/app/story/[id].tsx`:
- **Tab bar → pill toggle**: Replaced full-width underline tabs with compact pill-shaped Arabic/English toggle (surfaceLight bg, accent active state)
- **Word text**: 28px → 30px with Scheherazade font (was missing `fontFamily.arabic`), lineHeight 36→46
- **New word dots**: gray → accent blue, slightly larger (4→5px)
- **Lookup panel**: Redesigned from stacked slots to horizontal layout — Arabic and English side-by-side with vertical divider. Root shown in accent-tinted badge instead of plain text. Compact empty state (60px) expands when word selected (80px)
- **Bottom actions**: Complete button gets checkmark icon + flex 1.2 (most visual weight). Skip flex 0.7 (smallest). "Too Hard" hidden entirely for generated stories (was shown disabled). All buttons borderRadius 12
- **Sentence breaks**: 12px → 16px for clearer paragraph separation
- **Lookup count**: Shows "N looked up" badge in header bar when words have been tapped

### Files
- `frontend/app/story/[id].tsx` — full redesign

---

## 2026-02-11 — Review UI Polish

### Changes
- **Removed redundant missed word summary**: After revealing the answer, the card showed a separate list of missed/confused words below the transliteration. Redundant since the words are already highlighted red/yellow in the sentence text itself.
- **Fixed root meaning text overflow**: Long root meanings (e.g. "related to cities, civilization, urbanization, settling, being refined") overflowed the word info card. Added `flexShrink: 1` to `rootMeaning` style.
- **Fixed self-reference in root family**: The revealed word info card showed the looked-up word as its own root sibling (e.g. أيضا appearing in its own root family list). Added `lemma_id` filter to `sortedFamily` in `RevealedView`.
- **Added Clear Cache button**: More screen now has a "Clear Cache" button that flushes all cached sessions, word lookups, and stats from AsyncStorage. Also added word lookups to `invalidateSessions()`.
- **Pre-deploy checks in deploy.sh**: Layout lint (detects href+tabBarButton conflict) and TypeScript validation before pushing to server. Post-deploy Expo bundle check.

### Files
- `frontend/app/index.tsx` — removed missedWordSummary block
- `frontend/lib/review/WordInfoCard.tsx` — flexShrink on rootMeaning, filter self from sortedFamily
- `frontend/app/more.tsx` — Clear Cache button
- `frontend/lib/offline-store.ts` — wordLookups in invalidation
- `scripts/deploy.sh` — pre-deploy checks

---

## 2026-02-11 — Auto-Backfill Root Meanings

### Problem
35 of 937 roots had empty `core_meaning_en` — mostly from OCR imports and the cleanup script creating new Root records without meanings. All import paths (`import_wiktionary.py`, `backfill_roots.py`, `cleanup_bad_roots.py`) created roots with no meaning. `ocr_service.py` used the word's English gloss as the root meaning, which is incorrect (a word gloss like "beautiful" is not the same as a root meaning like "related to beauty, completeness").

### Fix
- Added `backfill_root_meanings(db)` to `morphology.py` — batches empty roots, sends to LLM for semantic field descriptions, fills `core_meaning_en`
- Called automatically from all import paths after new roots are created: OCR pipeline, Wiktionary import, backfill_roots, cleanup_bad_roots
- Backfilled all 35 missing meanings in production

### Verification
- `SELECT count(*) FROM roots WHERE core_meaning_en IS NULL OR core_meaning_en = ''` → 0
- 559 backend tests pass (OCR tests mock `backfill_root_meanings`)

---

## 2026-02-11 — Garbage Root Cleanup + Root Validation Guard

### Problem
OCR textbook imports created 55 garbage roots with invalid formats: `#` placeholders, Latin letters (`O`, `FOREIGN`, `DIGIT`), 2-letter roots, etc. Root "O" (meaning "Norway") had 44 unrelated lemmas including عَلِيّ, أَيْضاً, أَنْتِ. All 133 affected lemmas were mis-tagged as `noun_prop`. Root cause: no validation on root strings before creating Root entries in `ocr_service.py`.

### Fix
1. **Cleanup script** (`scripts/cleanup_bad_roots.py`): LLM-assisted batch classification of 133 affected lemmas — correct root, POS, and base lemma detection. Found 38 variants and linked via `canonical_lemma_id`. Remaining 29 (foreign loanwords, country names, digits, function word fragments) had `root_id` set to NULL.
2. **Root validation guard**: Shared `is_valid_root()` in `morphology.py` — requires 3-4 dot-separated Arabic radicals (Unicode range \u0621-\u064a). Applied to all import paths: `ocr_service.py`, `import_wiktionary.py`, `backfill_roots.py`. `import_duolingo.py` uses hardcoded dict (safe by construction).
3. **POS fixes**: 14 lemmas corrected (country names → `noun_prop`, nationality adjectives → `adj`, function words → `pron`/`prep`, loanwords → `noun`).

### Results
- 55 garbage roots deleted (all of them)
- 104 lemmas reassigned to correct Arabic roots
- 38 variants identified and linked
- 29 rootless lemmas (loanwords/digits) properly nulled
- 14 POS corrections
- Zero bad roots remain in production DB

### Verification
- `is_valid_root()` rejects `O`, `DIGIT`, `#.ل.ه`, `ل.#.#`, `FOREIGN`, `N.T.W.S`
- `is_valid_root()` accepts `ك.ت.ب`, `ع.ل.م`, `ز.ل.ز.ل`
- 559 backend tests pass

---

## 2026-02-11 — LLM-Confirmed Variant Detection (replaces CAMeL-only)

### Change
The CAMeL-only `detect_variants()` had a 34% true positive rate — too many false positives from taa marbuta feminines misidentified as possessives (غرفة→غرف, جامعة→جامع). Replaced with a two-phase approach:

1. **Phase 1 (CAMeL)**: Generate candidate pairs using existing morphological analysis + DB matching
2. **Phase 2 (LLM)**: Gemini Flash confirms or rejects each candidate with semantic understanding

New functions in `variant_detection.py`:
- `evaluate_variants_llm()` — sends candidate pairs to LLM in batches of 15, with DB cache
- `detect_variants_llm()` — full pipeline: CAMeL candidates → LLM confirmation
- `VariantDecision` model + migration for caching LLM decisions

All import scripts (Duolingo, Wiktionary, AVP, OCR) and cleanup tools now use `detect_variants_llm()`. Graceful fallback: LLM failure skips confirmation (imports don't break).

### Data Quality Fixes
- عمل: POS noun→verb (was preventing verb conjugation matching)
- خال: gloss "empty, void"→"maternal uncle" (was preventing خالة match)

### Results
- **Spec test**: 21/21 correct (100%) — 10 false positives correctly rejected, 11 true positives confirmed
- **Production run**: 135 CAMeL candidates → 77 LLM-confirmed → all 77 merged with review data migration
- **Cost**: ~$0.001 for full DB scan (9 batches × Gemini Flash)
- Correctly rejected: nisba adjectives (مصري/مصر), different-meaning taa marbuta (جامعة/جامع), unrelated words (سمك/سم, بنك/بن), masdars (كتابة/كتاب)
- Correctly confirmed: possessives (اسمي/اسم), feminines (صديقة/صديق), conjugations (نعمل/عمل), plurals (غرفة/غرف)
- Cache table prevents re-querying known pairs on future runs

### Verification
- 559 backend tests pass (7 new tests for LLM variant detection + cache)
- Production: 77 variant merges applied, 73 forms_json enriched
- `scripts/test_llm_variants.py` — reusable benchmark with ground truth

---

## 2026-02-11 — Harden All Ingestion Paths + Hamza-Aware Variant Detection

### Change
1. **Hamza normalization at lookup time**: `normalize_alef()` now applied consistently in `morphology.py` (`is_variant_form`, `find_matching_analysis`, `find_best_db_match`) and `variant_detection.py` (`detect_variants`, `detect_definite_variants`). Hamza preserved in storage, normalized only at comparison time — standard Arabic NLP practice.
2. **MLE disambiguator integration**: Added CAMeL Tools `MLEDisambiguator` to `morphology.py`. New `get_best_lemma_mle()` function uses corpus-probability-weighted analysis for better base lemma extraction (reduces false positives like سمك→سم). OCR pipeline now uses MLE in `_step2_morphology()`.
3. **Public lookup API**: Renamed `_lookup_lemma` → `lookup_lemma` and `_lookup_lemma_direct` → `lookup_lemma_direct` in `sentence_validator.py`. Added `resolve_existing_lemma()` helper for import scripts.
4. **Clitic-aware import dedup**: All three import scripts (Duolingo, Wiktionary, AVP A1) now use `build_lemma_lookup()` + `resolve_existing_lemma()` instead of flat bare-form set checks. This catches و-prefixed (وكتاب→كتاب), ال-prefixed, and pronoun-suffixed forms (كتابها→كتاب) at import time.
5. **Unified `strip_diacritics`**: Import scripts now delegate to `sentence_validator.strip_diacritics()` instead of maintaining local copies.
6. **Production cleanup script**: New `scripts/normalize_and_dedup.py` with 3 passes: re-run variant detection with hamza-aware code, clitic-aware dedup via `lookup_lemma()`, and `forms_json` enrichment for all known variants.

### Design Principles
- **Clitics** (كتابي، وكتاب، بالكتاب): strip silently, no separate tracking — syntactic, not learning-relevant
- **Morphological variants** (كتاب/كتب, أسود/سوداء): ONE lemma, but track per-form comprehension via existing `variant_stats_json` on ULK
- **Hamza**: Real consonant, preserved in `lemma_ar_bare` storage. Normalized only at lookup/comparison time. Standard Arabic NLP practice confirmed by AraToken paper research.

### Expected Effect
- Future imports catch ~90% of variant forms before creating duplicate lemmas
- MLE disambiguator improves morphological analysis accuracy (fewer false positive merges)
- Production cleanup should consolidate remaining ~300 rare variant lemmas

### Verification
- 552 backend tests pass (15 new tests for lookup_lemma, resolve_existing_lemma)
- Run `normalize_and_dedup.py --dry-run` on production to preview
- Run `normalize_and_dedup.py --merge` to apply
- Re-run rare word analysis — expect significant reduction in active rare words

### Results (production run)
- 12 al-prefix duplicates safely merged (الكتاب→كتاب type)
- 97 forms_json entries enriched on existing variant lemmas
- 146 CAMeL-detected variants reported but NOT auto-applied — 34% true positive rate (see `research/variant-detection-spec.md` for detailed analysis)
- Key insight: CAMeL misinterprets taa marbuta feminines as possessives (غرفة→غرف+ة), producing false merges. An LLM-based approach may be needed for the remaining ~50 genuine variants.

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
