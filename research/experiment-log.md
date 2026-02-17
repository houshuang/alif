# Experiment Log

Running lab notebook for Alif's learning algorithm. Each entry documents what changed, why, what we expect, and how to verify.

---

## 2026-02-16: Comprehensibility Gate Tightening + Pipeline Cap Headroom + Warm Cache Multi-Target

### Changes
1. **Comprehensibility gate tightened** (`sentence_selector.py`): Changed from counting all content words (70% threshold) to counting only **scaffold words** (non-due, non-function, 60% threshold). Acquiring words with stability < 0.5 (box 1, freshly introduced) no longer count as "known". This prevents book-imported sentences from appearing when most words were batch-imported simultaneously and haven't been reviewed yet.

2. **Pipeline cap headroom** (`update_material.py`): Added `CAP_HEADROOM = 30`. Step 0 now retires down to 270 (not 300), ensuring Step A always has budget for multi-target backfill generation. Previously, Step 0 retiring to exactly 300 + Step A's `>= 300` check = zero budget = no backfill ever ran.

3. **Warm cache upgraded to multi-target** (`material_generator.py`): `warm_sentence_cache()` now uses `group_words_for_multi_target()` + `generate_validated_sentences_multi_target()` for efficient 2-4 words/sentence generation. Falls back to single-target for ungrouped words. Allows up to `PIPELINE_CAP + 10` (310) before skipping.

### Rationale
- Book import creates many "acquiring" words simultaneously. Old gate treated all acquiring as known → book sentences appeared 100% comprehensible when learner had never reviewed any of the words.
- Pipeline was stuck: cron retired to cap, backfill saw `>= cap`, generated nothing. All generation fell to on-demand single-target during API requests.
- Warm cache was using single-target generation, missing the efficiency of multi-target.

### Expected Effect
- Book sentences gradually appear as learner reviews vocabulary (box 2+ words count as known)
- Pipeline maintains ~270-300 active sentences with regular multi-target backfill every 6h
- Warm cache generates 2-4x more efficiently via multi-target batching
- Fewer on-demand single-target generations needed during API requests

### Verification
- Book sentence sid=3459 (Rosie): 50% known scaffold → correctly filtered (was 100% before)
- Pipeline: Step 0 retired 30 → Step A generated 30 (25 multi-target, 5 single-target)
- All 35 backend tests pass

---

## 2026-02-16: Book Import Word Enrichment

### Changes
- **Fixed conjugated glosses**: LLM prompt in `_import_unknown_words()` now uses CAMeL lex (base form) instead of surface form, removes story context bias, explicitly requests dictionary-form glosses ("to wake up" not "she woke up")
- **Inline transliteration**: New Lemma records get ALA-LC transliteration set immediately during import (deterministic, instant)
- **Background enrichment service**: New `lemma_enrichment.py` with `enrich_lemmas_batch()` that populates forms_json, etymology_json, memory_hooks_json for newly created lemmas
- **Wired into import endpoints**: books.py, stories.py schedule enrichment as background task after import
- **Cron catch-all**: update_material.py Step E enriches up to 20 unenriched lemmas per 6h run
- **Retroactive fix script**: `fix_book_glosses.py` re-generates dictionary glosses and runs full enrichment for existing book-imported words

### Rationale
Book-imported words were created with minimal data (just Arabic form + contextual gloss), missing forms, etymology, memory hooks, and transliteration. The LLM prompt included story context that biased toward conjugated translations ("she woke up") rather than dictionary glosses ("to wake up").

### Expected Effect
- Word cards for book-imported words now show proper forms, etymology, memory aids
- Future imports automatically get full enrichment via background task
- Cron catches any words that slip through

### Verification
- Run `fix_book_glosses.py --dry-run` to preview, then run for real
- Import a new book and verify words get enrichment within minutes
- Check word detail page for forms, etymology, transliteration

---

## 2026-02-16: Sentence Pipeline Cap Enforcement

### Changes
- **Hard 300-sentence cap**: `update_material.py` Step 0 retires excess sentences by priority (never-shown stale → shown stale → oldest), keeping min 1 per word
- **Rotation in cron**: `rotate_stale_sentences.py` runs before `update_material.py` every 6h for diversity-based retirement
- **Warm cache cap check**: `warm_sentence_cache()` skips pre-generation when at/above 300

### Before/After
- Before: 560 active sentences, 229 (41%) never shown. Cap was soft (only prevented cron generation, not warm cache)
- After: 300 active, 90 (30%) never shown, 0 words with zero sentences, 231 words with exactly 1 sentence

### Rationale
- 41% never-shown sentences = wasted pre-generation. Pre-generated sentences use stale vocabulary.
- JIT on-demand generation (during session building) produces better sentences with current vocabulary
- Multi-target sentences cover 2-4 words each, reducing per-word sentence need
- Min 1 per word for cap enforcement (not 2) — JIT handles gaps when the one sentence isn't comprehensible

### Expected Effect
- Fresh, more diverse sentences with current vocabulary
- ~100-150 new sentences generated in first week via JIT + cron backfill (Gemini Flash, negligible cost)
- Steady-state churn: rotation retires stale → Step 0 enforces cap → Step A backfills fresh

### Verification
- Monitor sentence count stays near 300: `SELECT count(*) FROM sentences WHERE is_active=1`
- Check never-shown % drops over time
- Watch for JIT generation spikes in session logs (should taper after first week)

---

## 2026-02-16: Learning Progress Analysis

### Findings
Comprehensive data analysis of Feb 10-16 learning sessions. Key findings:

1. **FSRS stability is working correctly.** Initial concern about 44 words with stability <3d was investigated. Two patterns found: (a) post-lapse recovery — words that failed a review (rating 1) had stability crash from ~4d to ~0.7d and are slowly recovering, which is correct FSRS behavior; (b) over-reviewed scaffold words — stable words (16d) get daily reviews as scaffold in other words' sentences, R≈0.99 so no growth, but harmless. Both patterns will self-correct.

2. **Sentence utilization**: 41% never shown (229/560). Mostly pre-generated for words not yet due in sessions. Expected to improve as more words enter review rotation.

3. **Acquisition pipeline healthy**: 5 box 1, 47 box 2, 32 box 3. Graduation avg 0.6 days. 55 words graduated total.

4. **Accuracy improving**: 63% (Feb 9) → 93% (Feb 16), largely driven by tier system changes prioritizing book/OCR words over random wiktionary.

### Action
No code changes needed. Both concerns deferred for re-evaluation late Feb when system has more history. Noted in IDEAS.md.

---

## 2026-02-16: Fix Wrong Word on Tap + Variant Detection Robustness

### What
Fixed bug where tapping a word during review showed wrong word details (e.g. شرطيّ "policeman" showed as شرط "stipulate"). Root cause: false variant detection + display code using canonical lemma_id instead of original.

### Changes
1. **Display fix**: `sentence_selector.py` — `WordMeta.lemma_id` now uses original `sw.lemma_id` for display/lookup, while `effective_id` (variant→canonical) is only used for scheduling (due checks, stability, knowledge state).
2. **Root validation**: `variant_detection.py` — added `root_id` check in `detect_variants()` that rejects candidate pairs with different roots. Prevents worst class of false variants (كلية→أكل, أميرة→مار, شباك→شب).
3. **Production audit**: Re-evaluated all 191 existing variant mappings via LLM, cleared false variants and stale cache entries.

### Why
CAMeL morphological analysis generates noisy candidates — iterates all analyses for a word and picks any whose stripped `lex` matches a DB lemma. Combined with diacritics/clitic stripping, words from different roots could be matched. The LLM confirmation step catches most false positives but isn't perfect, and some import scripts bypassed it.

### Expected Effect
No more wrong-word-on-tap. Three-layer defense: root check (cheapest), LLM confirmation (existing), display fix (defense-in-depth).

### Verification
- All 690 tests pass
- Tap words in review → correct details shown
- Production audit clears false variants

---

## 2026-02-16: Strict Source-Based Priority Tiers

### What
Replaced the flat scoring system with strict source-based priority tiers. Higher tiers ALWAYS beat lower tiers — frequency/root/grammar only serve as tiebreakers within each tier. Previously, the topic system ("nature") was causing wiktionary words (صيد, صقر) to be introduced over OCR and book words.

### Changes
- `word_selector.py`: New tier system replacing `story_bonus` + `page_bonus`:
  - Tier 1: Active book words (`100.0 - page * 0.5`, deterministic page ordering)
  - Tier 2: Active imported stories (+10.0)
  - Tier 3: OCR textbook_scan (+8.0)
  - Tier 4: Duolingo (+6.0)
  - Tier 5: AVP A1 (+4.0)
  - Tier 6: Wiktionary/other (+0.0, strictly by frequency)
- Removed topic filtering entirely. Topic now only adds +0.3 tiebreaker within OCR/Duolingo tiers.
- Backfill: reset low-priority box-1 acquiring words (wiktionary etc.) to encountered.

### Hypothesis
Word introduction now follows a predictable priority: book page 1 → page 2 → ... → stories → OCR → Duolingo → AVP → wiktionary. No more random wiktionary words appearing in sessions while book/OCR words wait.

### Verification
- `select_next_words(db, count=30)`: top words should be book p1, then p2, then OCR
- Auto-introduction in sessions should produce book/OCR words, never wiktionary
- Topic "nature" should not cause صيد/صقر to be introduced

---

## 2026-02-16: Book Progress Tracking + Page Prioritization

### What
Three improvements to the book import experience:
1. **False positive "new word" detection**: Words like في (reviewed 44 times over weeks) were misclassified as "new from book" because `acquisition_started_at` was reset by the OCR reset script. Changed detection to use `review_log` first-review date — the authoritative record of when a word was first studied. Resilient to maintenance script resets.
2. **Introduced today by source**: Stats dashboard now shows daily word introductions broken down by source (Book, OCR, Auto, Learn, etc.). Fixed `start_acquisition()` to preserve meaningful sources instead of overwriting to "auto_intro".
3. **Page prioritization**: Increased book page bonus spread from page 1→1.0 to page 1→1.5 (page 2→1.0, page 3→0.6, page 4→0.3, page 5+→0.1) so page 1 words are strongly preferred for auto-introduction.

### Changes
- `story_service.py`: `_get_book_stats()` and `get_book_page_detail()` now use `MIN(review_log.reviewed_at)` instead of `acquisition_started_at` to detect pre-existing knowledge
- `word_selector.py`: `_book_page_bonus()` steeper page decay (0.5/page vs 0.2/page), higher ceiling (1.5 vs 1.0)
- `schemas.py`: Added `new_total`/`new_learning` to `StoryOut`, `new_not_started`/`new_learning` to `BookPageDetailOut`, `IntroducedBySource` schema
- `acquisition_service.py`: Preserve meaningful ULK.source in `start_acquisition()`
- `book_import_service.py`: Create "encountered" ULK with source="book" after import
- `stats.py`: `_get_introduced_today()` query + source labels
- Frontend: story list shows `X/Y new words learning`, page pills show `learned/total`, stats shows introduced today by source

### Hypothesis
Users can track book import progress accurately. Page 1 words will be prioritized for introduction, enabling sequential page reading. False "new word" counts eliminated.

### Verification
- Check stats page shows introduced-today pills
- Check story list shows learning progress (not "183 unknown" for book with progress)
- في classified as "known at import" on book page detail, not "learning"
- Page 1 words scored higher than page 2+ words in `select_next_words()`

---

## 2026-02-16: Fix Book Page Detail + Story Readiness Includes Acquiring Words

### What
Two bugs in the book import story feature:
1. **Book page detail 500 error**: `get_book_page_detail()` referenced `lem.arabic_bare` and `lem.transliteration` — neither exist on the Lemma model. Correct attributes: `lemma_ar_bare` and `transliteration_ala_lc`.
2. **Story readiness excluded acquiring words**: `_recalculate_story_counts()` and `get_story_detail()` only counted `learning`/`known` as "known" words. After the algorithm redesign where all words go through acquiring first, this meant the majority of actively-learned words weren't reflected in story readiness.

### Changes
- `story_service.py`: Added `_ACTIVELY_LEARNING_STATES = {"acquiring", "learning", "known", "lapsed"}` — used in `_recalculate_story_counts()`, `get_story_detail()`, and `get_book_page_detail()`. Fixed attribute names for page detail.

### Production Impact
After recalculation, story 6 ("Rosie in the Haunted House") went from 10.1% → 23.1% readiness. Other stories shifted to reflect current vocabulary state (some down due to OCR card resets moving words from known→encountered).

---

## 2026-02-16: Word Category Classification (proper_name / onomatopoeia)

### What
Added `word_category` field to Lemma model to distinguish proper names (أسامة) and onomatopoeia (ماو) from standard vocabulary. Triggered by children's book import introducing "mau" (meow) as real vocab and "usama" as a lion synonym alongside "asad".

### Changes
- `import_quality.py`: new `classify_lemmas()` that categorizes each word (standard/proper_name/onomatopoeia/junk) instead of just pass/fail
- `word_selector.py`: scoring penalty for names (-0.8) and sounds (-1.0)
- `sentence_selector.py`: auto-introduction never picks proper_name or onomatopoeia
- Import paths (story_service, ocr_service) now set `word_category` on Lemma and prefix name glosses with "(name)"
- Frontend shows category badges on word detail, review WordInfoCard, and Learn mode cards

### Hypothesis
Proper names and sounds won't be auto-introduced, preventing confusion. When encountered via stories/OCR, they're clearly labeled. Users can still manually learn them via Learn mode if desired.

### Verification
- Run `backfill_word_categories.py` on production to classify existing words
- Verify "mau" and "usama" get proper categories
- Confirm auto-introduction skips categorized words
- Check UI badges appear on word detail and review screens

---

## 2026-02-14: Michel Thomas Audio Course Import Pipeline (Blocked)

### What
Built infrastructure to import audio language courses into Alif via speech-to-text transcription. First target: Michel Thomas Egyptian Arabic Foundation (8 CDs, 118 tracks, ~8h audio).

### Pipeline
1. Soniox STT transcription (16.2% WER for Arabic, native EN↔AR code-switching)
2. Extract Arabic segments from token stream (per-token language ID)
3. LLM classification: Egyptian vs MSA, add diacritics, translate
4. Import: words as "learning" (skip Leitner → FSRS with Rating.Good), sentences (3+ Arabic words) as Sentence records

### Files Created
- `backend/app/services/soniox_service.py` — Soniox REST API wrapper
- `backend/scripts/import_michel_thomas.py` — 5-phase pipeline with `--phase` resumability
- `backend/app/config.py` — added `soniox_api_key`

### Status
**Blocked**: Need a valid Soniox API key from console.soniox.com. Cost: ~$0.10/CD, ~$0.80 total.

### When Unblocked
```bash
cd backend
SONIOX_API_KEY=xxx python3 scripts/import_michel_thomas.py \
    --audio-dir /tmp/claude/michel_thomas/audio/Arabic\ Foundation\ CD1/ \
    --soniox-key xxx
```
Audio already extracted to `/tmp/claude/michel_thomas/audio/` (all 8 CDs).

---

## 2026-02-15: Page-Level Tracking + OCR Enhancement for Book Imports

### What
Book import now preserves page boundaries and handles dark/low-quality images. Each page is OCR'd and cleaned individually, both Sentence and StoryWord records are tagged with their source page number, and a per-page detail screen shows word/sentence status.

### Changes
- **Models**: Added `page_number` column to `Sentence` and `StoryWord` (migration `ad1ca8ace671`)
- **book_import_service.py**: Process each page through `cleanup_and_segment()` individually. Tag sentences and story words with page numbers. StoryWord surface→lemma fallback resolves more unmapped tokens. Sentences with remaining unmapped tokens are kept (not skipped). Dark image auto-enhancement via Pillow (brightness/contrast when mean < 120). Empty OCR results retry with `gemini-2.5-flash-preview` thinking model.
- **ocr_service.py**: `_call_gemini_vision()` accepts `model_override` param. Timeout increased 120s → 300s.
- **story_service.py**: New `_get_book_stats()` computes per-page readiness. New `get_book_page_detail()` returns per-page words (new vs existing, with status) and sentences (with seen indicator).
- **word_selector.py**: `_book_page_bonus()` gives earlier pages higher priority (page 1 → +1.0, decaying by 0.2/page)
- **Frontend**: Clickable page readiness pills → new `book-page.tsx` detail screen with word list + sentence list. `GET /api/stories/{id}/pages/{page}` endpoint.
- **Pillow** added as dependency for image preprocessing.

### Results
Reprocessed "Rosie in the Haunted House" (20 pages): 27 sentences across 12 pages (up from 24/11 without enhancement). Pages 2, 3, 5 recovered by brightness enhancement.

### Verification
- API returns `page_readiness` array with per-page word counts
- Story card shows page pills with remaining word counts
- Word selector ranks page-1 words above page-10 words for same story
- Clicking a page pill navigates to detail with word list + sentences
- Dark images auto-enhanced, empty pages retried with thinking model

---

## 2026-02-15: Ensure Every SentenceWord Has a lemma_id

### What
Fixed NULL `lemma_id` values in `sentence_words` table and hardened all 5 sentence storage paths to reject sentences with any unmapped word. Three root causes addressed:

1. **`variant_*` forms_json keys not expanded in lookups**: `build_lemma_lookup()` only iterated specific keys (plural, present, etc.) but `forms_json` had `variant_ذكية`, `variant_طالبة` etc. Fixed by iterating all keys.
2. **Function words without Lemma DB entries**: ~80 words in `FUNCTION_WORD_GLOSSES` (لم, لكن, ما, جدا, بعد, etc.) had glosses but no Lemma row. Created 45 new Lemma entries via `backfill_function_word_lemmas.py`.
3. **No rejection guard**: sentences with unmapped words were stored anyway. Added NULL guard in all 5 storage paths.

Additionally, sentence_word mapping now uses `build_comprehensive_lemma_lookup()` (ALL lemmas) instead of learned-only lookup. Sentence validation still uses restricted lookup.

### Changes
- `sentence_validator.py`: Expanded `build_lemma_lookup()` to include `variant_*` keys; added `build_comprehensive_lemma_lookup()`
- `material_generator.py`: Uses comprehensive lookup for mapping; NULL guard in `generate_material_for_word()` and `store_multi_target_sentence()`
- `sentence_selector.py`: Uses comprehensive lookup for on-demand mapping; NULL guard in `_generate_on_demand()`
- `book_import_service.py`: NULL guard in `create_book_sentences()`
- `generate_sentences_claude.py`: Replaced TSV-based lookup with DB-based comprehensive lookup; NULL guard; fixed query ordering (prioritize `active_count ASC`)
- `claude_code.py`: Same `variant_*` expansion fix in `dump_vocabulary_for_claude()`
- `scripts/backfill_function_word_lemmas.py`: New — creates Lemma rows for function words
- `scripts/fix_null_lemma_ids.py`: New — re-maps existing NULLs, retires unfixable sentences

### Why
Sentence 3364 had تَطِيرُ with `lemma_id=NULL`, causing word tap/lookup to fail in the frontend. Investigation found 49 sentence_words with NULL lemma_id. This is a data integrity issue — every word in a displayed sentence must be tappable.

### Production Results
- 45 function word lemmas created
- 32 NULLs fixed by variant_* expansion (deployed first)
- 11 more fixed by function word lemma creation + comprehensive lookup
- 6 sentences retired (genuinely unknown vocabulary: تطير/fly, يأكل/eat, نزور/visit)
- Final state: 0 NULL lemma_ids in active sentences

### Verification
- All 679 backend tests pass
- Production query: `SELECT COUNT(*) FROM sentence_words sw JOIN sentences s ON s.id=sw.sentence_id WHERE sw.lemma_id IS NULL AND s.is_active=1` → 0

---

## 2026-02-15: Enriched Analytics — Predictions & Progress Insights

### What
Added four new analytics features to make progress more visceral and motivating:
1. **CEFR arrival prediction**: "~X days at this week's pace" on the CEFR card
2. **Book pages equivalent**: total words reviewed as book pages (200 words/page)
3. **Unique words recognized**: distinct lemmas correctly recalled this week, with delta vs. prior week
4. **Story completion prediction**: "~Xd until ready" per active story

### Changes
- `backend/app/schemas.py`: Added optional fields to CEFREstimate, AnalyticsOut, StoryOut
- `backend/app/routers/stats.py`: `_add_cefr_predictions()`, `_get_words_reviewed_count()`, `_get_unique_words_recognized()`
- `backend/app/routers/stories.py`: Pace-based prediction in `list_stories()`
- `frontend/app/stats.tsx`: CEFR prediction lines, pace items (pages, words read, recognized)
- `frontend/app/stories.tsx`: "~Xd until ready" on story cards

### Why
Stats showed "207 words to A1" but no timeline — no way to feel the pace. Stories showed unknown counts but no ETA. These are intrinsic motivation signals, not gamification.

### Expected Effect
- More visceral sense of progress (predictions make abstract counts feel real)
- Story completion predictions help prioritize which stories to focus on
- Weekly recognition count rewards diverse review

### Verification
- All backend tests pass (678/678)
- All frontend tests pass (73/73)
- Deploy and verify on stats + stories screens

---

## 2026-02-15: Claude Code Validator-in-the-Loop — Agentic Sentence Generation

### What
Expanded Claude Code CLI integration from simple API replacement to **tool-enabled agentic sessions**. Instead of the external 7-retry loop (generate → validate externally → feed errors as text → retry), Claude now reads the vocabulary file, generates sentences, runs the validator script itself, and self-corrects — all within one `claude -p` session.

### Changes
- `claude_code.py`: Added `generate_with_tools()` (Read/Bash tools, `--dangerously-skip-permissions`, `--add-dir`, `--max-budget-usd`) and `dump_vocabulary_for_claude()` (full vocab export to TSV)
- `scripts/validate_sentence_cli.py`: Thin CLI wrapper around `validate_sentence()` for Claude to call via Bash tool
- `scripts/generate_sentences_claude.py`: Batch sentence generation with validator-in-the-loop. Batches 5 words per session, generates 2 sentences each.
- `scripts/audit_sentences_claude.py`: Quality audit — Claude reads all active sentences + vocabulary, reviews each with vocabulary context, outputs retire/fix/ok report

### Why
- Full vocabulary in file (not 500-word sample in prompt) = better compliance
- Self-validation loop = fewer external retries, more precise error correction
- Free with Max plan (Opus quality at $0 cost)
- Vocabulary-aware quality audit (Haiku API only sees Arabic+English, no vocab context)

### Verification
- Tested locally: 3 words × 2 sentences with Haiku (57s) and Opus (56s), all sentences validated successfully
- 181/182 tests pass (1 pre-existing failure unrelated)
- Next: install Claude CLI on server, integrate with update_material.py cron

---

## 2026-02-15: Vocabulary Diversity in Sentence Generation + Stale Sentence Rotation

### What
Two improvements to sentence quality:
1. **Doubled batch size** (5→10 words/session) to halve startup overhead (~30s amortized over more work)
2. **Diversity-aware generation**: vocab dump now marks "CURRENTLY LEARNING" words; prompt explicitly instructs Claude to use acquiring words as supporting vocabulary and vary word usage across sentences
3. **Stale sentence rotation**: new `rotate_stale_sentences.py` identifies sentences where all scaffold words are fully known (no cross-training value) and retires them for regeneration with vocabulary-diverse replacements
4. **Fixed store_sentences**: column name bug (`english_text`→`english_translation`) and added SentenceWord record creation

### Changes
- `claude_code.py`: Timeout 180→240s; `dump_vocabulary_for_claude()` now includes `knowledge_state` and writes "CURRENTLY LEARNING" section
- `generate_sentences_claude.py`: `WORDS_PER_SESSION` 5→10; system prompt adds VOCABULARY DIVERSITY section; `store_sentences()` fixed column name + creates SentenceWord records
- `scripts/rotate_stale_sentences.py`: New script — scores sentences by diversity (% scaffold words in acquiring/learning state), retires stale ones while keeping min_active per target

### Why
Production analysis showed:
- 448 active sentences, only 38% meet 70% comprehensibility gate
- في appears in 48% of all sentences; top common words heavily overused
- 138 sentences have zero acquiring words in scaffold (stale)
- ~30s startup overhead per Claude Code session → doubling batch size halves total runtime
- `store_sentences()` had wrong column name and didn't create SentenceWord records (sentences wouldn't appear in session building)

### Expected Effect
- Sentences reinforce multiple words per review (not just the target)
- Vocabulary exposure spread more evenly instead of concentrating on early-learned words
- ~50% faster batch generation (10 words/session vs 5)
- More efficient review sessions overall

### Verification
- All 184 tests pass (1 pre-existing failure)
- Vocab dump confirmed "CURRENTLY LEARNING" section present
- Rotation dry-run on production: 109 retirable sentences with min_active=1, 26 with min_active=2
- Tokenization + SentenceWord mapping tested locally

---

## 2026-02-15: Background Session Refresh — Seamless Stale Session Swap

### What
Added in-session staleness detection: when the app resumes after a 15+ minute gap since the last review, a fresh session is fetched in the background and seamlessly swapped in on the next card advance. No loading screen — the user reviews their current card and the next card is from the fresh session.

### Why
Production data analysis (3 days, 45 sessions) showed:
- 48% of sessions abandoned mid-review
- 9 stale resumptions (>30 min gap) with average -22% comprehension drop
- Worst case: 5-hour gap, comprehension 75% → 17%
- The existing 30-minute cache staleness TTL only applied when *loading* sessions from cache. Once a session was in React state, it was never re-evaluated — users could resume stale cards indefinitely.

### Changes
1. **`frontend/lib/api.ts`**: Added `fetchFreshSession()` — bypasses cache, fetches directly from API.
2. **`frontend/app/index.tsx`**: Added `lastReviewedAt` ref (updated on every card advance), `pendingRefreshRef` (holds fresh session), `AppState` listener (triggers background fetch on 15+ min gap), `applyFreshSession()` (seamless session swap), and check in `advanceAfterSubmit()` to swap pending refresh.

### Expected Effect
- Stale sessions eliminated: users always get algorithmically optimized cards after any significant gap
- Zero loading latency: background fetch runs while user reviews current card
- Graceful degradation: if fetch fails (offline), existing cards continue normally
- No impact on short pauses (<15 min): normal review flow unaffected

### Verification
- Monitor session patterns in interaction logs for reduced stale resumptions
- Compare comprehension before/after gaps in upcoming data

---

## 2026-02-14: Demand-Driven Auto-Introduction — Remove Acquiring Pipeline Caps

### What
Replaced fixed acquiring-count caps (MAX_ACQUIRING_WORDS=40, MAX_ACQUIRING_CEILING=50, MAX_BOX1_WORDS=12) with demand-driven logic: introduce words when the session is undersized, with no global cap on how many words can be in acquisition.

### Why
With 52 acquiring words (exceeding the 40 cap), 419 encountered words waiting, and 90 known words with high stability (not due), sessions were running dry (1-3 cards). The caps were the bottleneck — most of the 52 acquiring words were in box 2/3 waiting for their interval, not competing for session space. The user was getting repetitive catch-up cards from the "almost-due" fallback instead of new content.

### Changes
1. **`sentence_selector.py`**: `_auto_introduce_words()` now takes `slots_needed` (session limit minus due words) instead of `acquiring_count`. Removed `MAX_ACQUIRING_WORDS`, `MAX_ACQUIRING_CEILING`, `MAX_BOX1_WORDS`, `MAX_BOX1_WORDS_FILL`. Per-call cap (MAX_AUTO_INTRO_PER_SESSION=10) and accuracy throttle still apply.
2. **`cohort_service.py`**: `MAX_COHORT_SIZE` raised from 100 → 200 to accommodate more acquiring words.
3. **`docs/scheduling-system.md`**: Updated session building diagram, constants table, cohort section, divergences section.

### Expected Effect
- Sessions always fill to the requested limit (no more empty/tiny sessions)
- New words introduced whenever there's room, regardless of pipeline size
- Accuracy throttle still prevents overwhelming a struggling learner
- Acquiring pipeline grows organically, words graduate and flow out naturally

### Verification
```sql
-- After a few sessions, check acquiring count is growing
SELECT knowledge_state, COUNT(*) FROM user_lemma_knowledge GROUP BY knowledge_state;
-- Sessions should be full size
SELECT session_id, COUNT(*) FROM sentence_review_log GROUP BY session_id ORDER BY MIN(reviewed_at) DESC LIMIT 5;
```

---

## 2026-02-14: Book Import — OCR Pipeline for Children's Books

### What
Full pipeline to photograph a children's book (cover + content pages), OCR all pages, extract sentences with diacritics, and import as a "reading goal" story. Book sentences are captured and used in review sessions when they become comprehensible.

### Changes
1. **Data model**: Added `story_id` FK to Sentence (links book sentences to source story), `page_count` to Story. New migration.
2. **`book_import_service.py`**: Core pipeline — cover metadata extraction (Gemini Vision), parallel multi-page OCR (ThreadPoolExecutor), LLM cleanup + diacritization + segmentation, separate LLM translation, deterministic transliteration, story creation (reuses story_service helpers), sentence + SentenceWord record creation.
3. **`books.py` router**: `POST /api/books/import` — multipart upload (first image = cover, rest = pages), returns StoryDetailOut.
4. **`sentence_selector.py`**: 1.3x `source_bonus` for book sentences in both initial scoring and set cover re-scoring.
5. **Frontend**: `book-import.tsx` (cover photo + content pages + title input + import button with phase-based loading), stories list updated with "Book" button and book source badges.
6. **Tests**: 11 new tests covering cover extraction, cleanup, translation, sentence creation, full pipeline, title override, source bonus.

### How It Works
- User photographs cover (metadata extraction) + all content pages → parallel OCR → LLM cleanup/diacritize/segment → LLM translate → create Story + StoryWords + Sentences + SentenceWords
- Book sentences start `is_active=True` but the comprehensibility gate (≥70% known) filters them during session building
- Initially most book sentences fail the gate → LLM sentences fill the gap via on-demand generation
- As user learns more book words → more book sentences become comprehensible → gradual transition from LLM to real book sentences
- Words from the book get `story_bonus` in word_selector (existing behavior for story words), so they're prioritized for auto-introduction

### Expected Effect
- User can photograph a library book and have the system train them toward reading it
- Real book sentences appear in review when comprehensible, providing authentic context
- Readiness percentage shows progress toward being able to read the physical book
- Book sentence preference (1.3x) means the system naturally transitions to real book content as vocabulary grows

### Verification
- Unit tests: all 11 pass (mocked OCR + LLM calls)
- Integration: download a children's book from Archive.org (Karim Series), run through pipeline, verify OCR quality + sentence extraction + readiness %
- Session builder: verify book sentences appear when comprehensible and LLM fills gaps when not
- Frontend: verify import flow, story list badges, and reader work for book_ocr stories

---

## 2026-02-14: Story Generation — Opus Integration + Claude Code CLI Wrapper

### What
Implemented benchmark findings: switched story generation from GPT-5.2 to Claude Opus with retry loop, fixed `_get_known_words()` to include acquiring words, created reusable `claude -p` CLI wrapper.

### Changes
1. **`story_service.py`**: `_get_known_words()` now includes `acquiring` state (was only learning+known). Excludes variants. Returns POS info. Generation uses `model_override="opus"` with retry loop (MAX_STORY_RETRIES=3, STORY_COMPLIANCE_THRESHOLD=70%). POS-grouped vocabulary. Acquiring words highlighted as reinforcement targets.
2. **`llm.py`**: Added "opus" to MODELS list (claude-opus-4-6, uses ANTHROPIC_API_KEY).
3. **`claude_code.py`**: New service — reusable `claude -p` wrapper with `--tools ""` (no tool access) + `--json-schema` for structured output. `is_available()` + `generate_structured()`. Designed for reuse in future tasks.
4. **`scripts/generate_story_claude.py`**: Standalone script for local story generation via `claude -p` (free with Max plan). Loads vocabulary from DB, validates compliance, retries with feedback.

### Claude Code CLI Pattern
Key flags for reliable, predictable `claude -p` usage:
- `--tools ""` — disables ALL tools (no file reads, bash, web search)
- `--json-schema {...}` — enforces structured output (clean JSON, no markdown fences)
- `--no-session-persistence` — no saved sessions
- `--system-prompt "..."` — custom system prompt, no project context
- `--model opus` — uses Opus via Max plan subscription

Tested 4/4 story generations successful. Opus hits 78-89% compliance on first attempt (many "unknown" words are actually conjugated forms — real compliance ~87-95%). Sonnet needs retries more often (59-79%). Average generation time ~42s.

### Expected Effect
- Story quality improves dramatically (Opus 3.73 composite vs GPT-5.2 2.63)
- Acquiring words now available in story vocabulary (was excluded, bug)
- Retry loop catches vocabulary violations before storing
- Cost: $0.15-0.45/story via API, free via Max plan CLI

### Verification
- `scripts/generate_story_claude.py --db path --dry-run` to verify prompts
- Generate a story via API and check readiness_pct
- Backend tests pass (172 passed, 2 pre-existing failures unrelated)

---

## 2026-02-14: Story Generation Model Benchmark

### What
Benchmarked 4 LLM models × 4 prompting strategies × 2 stories = 32 stories for Arabic story generation quality. See `research/story-benchmark-2026-02-14/` for full data.

### Models Tested
- Gemini 3 Flash (current sentence gen model)
- GPT-5.2 (current story gen model)
- Claude Opus 4.6 (first test)
- Claude Sonnet 4.5 (first test)

### Key Findings
1. **Opus is the best story model**: composite 3.73, compliance 74%, best single story hit 93% compliance + 4.3 composite
2. **Sonnet has highest narrative quality** (3.90 composite) but worst compliance (57%) — ignores vocabulary constraints
3. **GPT-5.2 produces lowest quality stories** (2.63 composite) despite best compliance (82%) — confirms "word salad" finding from sentence benchmark
4. **Two-pass (generate→rewrite) has worst compliance** (54%) — the vocabulary rewrite pass doesn't work well with same model
5. **Baseline prompting strategy wins** — expanded structures and POS-grouped vocab didn't help for stories
6. **Compliance validator overly strict** — many "unknown" words are conjugated forms of known vocabulary (يوم، رأى، قالت flagged as unknown)

### Recommendations
- Switch story `model_override` from `"openai"` to Opus
- Add retry loop feeding back unknown words
- Fix `_get_known_words()` to include acquiring words
- Expand forms_json coverage for verb conjugations

### Verification
Full benchmark report and all 32 stories in `research/story-benchmark-2026-02-14/`.

---

## 2026-02-14: Acquisition Due-Date Gating + Leech Graduated Cooldown

**REASSESS DATE: 2026-02-17** (3 days after deploy)

### Changes

**1. Due-date gating for box advancement (acquisition_service.py)**
- Box 1→2: always allowed (within-session encoding phase)
- Box 2→3: only when `acquisition_next_due <= now` (enforce 1-day inter-session spacing)
- Box 3→graduation: only when due (enforce 3-day spacing)
- Graduation additionally requires reviews on ≥2 distinct UTC calendar days
- Within-session reviews of not-yet-due words still count for `times_seen`/`times_correct` (exposure credit) but don't advance the box or reset the timer
- New constant: `GRADUATION_MIN_CALENDAR_DAYS = 2`

**2. Increased pipeline capacity (sentence_selector.py)**
- `MAX_ACQUIRING_WORDS`: 30 → 40 (more headroom for slower graduation)
- `MAX_BOX1_WORDS`: 8 → 12 (box 1 clears fast via within-session advancement)

**3. Graduated leech cooldown (leech_service.py)**
- New `leech_count` column on ULK (migration `t0l5m6n7o123`)
- Cooldown: 3d (1st suspension) → 7d (2nd) → 14d (3rd+)
- Stats preserved on reintroduction (not zeroed) — word must genuinely improve
- Fresh sentences generated on reintro
- Memory hooks ensured on reintro (generated if missing)
- Reintro cards include `memory_hooks` and `etymology` fields

### Why

48-hour data analysis (2026-02-14) revealed:
- **Critical bug**: Words graduating in 9 minutes via within-session box jumping. قَرِيب graduated with 5 reviews in 9 min. 10.5% of graduated words immediately lapsed.
- **Binary leech handling**: 14-day flat suspension → cold restart with zeroed stats. حصان graduated in 30 min then leech-suspended 3 hours later. Same treatment regardless of history.
- **Morning review-only sessions**: Box 1 cap (8) filling in first session, blocking all auto-introduction in subsequent sessions.

### Expected effects

| Metric | Before | Expected After |
|--------|--------|---------------|
| Graduation speed | 9 min – 15h | Minimum ~1.5 days (1→2 in-session, 2→3 after 1d, 3→grad after 3d + 2 calendar days) |
| Immediate lapse rate | 10.5% | <3% (genuine consolidation before FSRS) |
| Words in acquisition pipeline | Max 30 | Max 40, flowing ~8-10 graduations/day at steady state |
| Leech reintro | 14d flat, zeroed stats | 3d→7d→14d graduated, stats preserved, memory hooks shown |
| Morning session variety | Only review after first session | 12 box 1 slots instead of 8 |

### Pipeline throughput model (2 sessions/day)

```
Day 1 AM: Introduce W1-W10, advance to box 2 in-session. Box 1 empty.
Day 1 PM: Introduce W11-W20. W1-W10 in box 2 (not due yet).
Day 2 AM: Introduce W21-W30. W1-W10 due from box 2 → box 3.
Day 2 PM: Introduce W31-W40. W11-W20 → box 3. Cap hit (40 acquiring).
Day 3: W21-W30 → box 3. Waiting for graduations.
Day 5+: W1-W10 graduate (3d in box 3 + 2 calendar days). 10 slots open.
Steady state: ~8-10 graduations/day, ~8-10 introductions/day.
```

### Hypotheses

**H1: No more same-session graduations.** Before this change, words could graduate in 9 minutes via within-session box jumping. With due-date gating, the minimum graduation time is ~1.5 days (box 1→2 in-session, wait 1d for box 2→3, wait 3d for graduation, spanning ≥2 calendar days). **Success metric**: Zero graduations where `graduated_at - first_review_at < 24 hours`.

**H2: Immediate lapse rate drops from 10.5% to <5%.** Words that genuinely consolidate over days should retain better than words that crammed through in one session. **Success metric**: Of words graduated after Feb 14, <5% have their first post-graduation review rated 1 (Again).

**H3: Pipeline stays full — no "ran out of words" sessions.** With MAX_ACQUIRING raised to 40 and MAX_BOX1 raised to 12, the system should keep introducing new words even as graduation slows down. **Success metric**: No sessions with <5 cards when there are encountered words available. Sessions should average ≥8 cards.

**H4: Box distribution shifts toward 2/3.** Previously most words were in box 1 or already graduated. Now we should see a healthy spread across boxes. **Success metric**: At any given time, box 2 + box 3 words > box 1 words (after the first 2 days of the new system).

**H5: Leech reintroductions use graduated timing.** First-time leeches should reintroduce after 3 days (not 14). No leech should reintroduce before its cooldown period. **Success metric**: Any reintroduced leech with leech_count=1 waited ≥3d; leech_count=2 waited ≥7d.

### Verify (2026-02-17)

Run these checks 3 days after deploy:

**1. Graduation timing** — confirm no same-day graduations:
```sql
-- Words graduated after Feb 14 deploy: check time from first review to graduation
SELECT l.lemma_ar, ulk.graduated_at,
       (SELECT MIN(rl.created_at) FROM review_log rl WHERE rl.lemma_id = ulk.lemma_id) as first_review,
       ROUND(julianday(ulk.graduated_at) - julianday(
           (SELECT MIN(rl.created_at) FROM review_log rl WHERE rl.lemma_id = ulk.lemma_id)
       ), 1) as days_to_graduate
FROM user_lemma_knowledge ulk
JOIN lemmas l ON l.lemma_id = ulk.lemma_id
WHERE ulk.graduated_at > '2026-02-14'
ORDER BY days_to_graduate;
-- EXPECT: all days_to_graduate >= 1.5
```

**2. Post-graduation lapse rate**:
```sql
-- First review after graduation for recently graduated words
SELECT l.lemma_ar, ulk.graduated_at, rl.rating, rl.created_at as first_post_grad_review
FROM user_lemma_knowledge ulk
JOIN lemmas l ON l.lemma_id = ulk.lemma_id
JOIN review_log rl ON rl.lemma_id = ulk.lemma_id AND rl.created_at > ulk.graduated_at
WHERE ulk.graduated_at > '2026-02-14'
AND rl.created_at = (SELECT MIN(r2.created_at) FROM review_log r2 WHERE r2.lemma_id = ulk.lemma_id AND r2.created_at > ulk.graduated_at)
ORDER BY rl.rating;
-- EXPECT: <5% with rating=1 (was 10.5% before)
```

**3. Acquisition box distribution**:
```sql
SELECT acquisition_box, COUNT(*) as count
FROM user_lemma_knowledge
WHERE knowledge_state = 'acquiring'
GROUP BY acquisition_box ORDER BY acquisition_box;
-- EXPECT: box 2 + box 3 > box 1 (after 2 days)
```

**4. Session sizes** — confirm sessions stay full:
```bash
python3 scripts/analyze_progress.py --days 3
# Check "Sessions" section: average cards per session should be ≥8
# Check "Acquisition" section: words should be flowing through pipeline
```

**5. Leech reintroductions and cooldowns**:
```sql
SELECT l.lemma_ar, ulk.leech_count, ulk.leech_suspended_at, ulk.knowledge_state, ulk.source
FROM user_lemma_knowledge ulk JOIN lemmas l ON l.lemma_id = ulk.lemma_id
WHERE ulk.leech_count > 0
ORDER BY ulk.leech_count DESC, ulk.knowledge_state;
-- Check: any source='leech_reintro' words respected their cooldown period
-- Check: leech_count increments correctly on re-suspension
```

**6. New word introduction rate**:
```sql
SELECT DATE(created_at) as day, COUNT(*) as introduced
FROM user_lemma_knowledge
WHERE knowledge_state IN ('acquiring', 'learning', 'known')
AND created_at > '2026-02-14'
GROUP BY DATE(created_at);
-- EXPECT: 8-15 new words/day (not 0, not >20)
```

**7. User feel check**: Does the user report sessions feeling stale or overwhelming? Are there too many repeat cards from not-yet-due acquisition words getting exposure credit without advancing?

### Files changed

- `backend/app/models.py` — added `leech_count` column
- `backend/alembic/versions/t0l5m6n7o123_add_leech_count.py` — migration
- `backend/app/services/acquisition_service.py` — due-date gating, calendar day check
- `backend/app/services/leech_service.py` — graduated cooldown, preserved stats, memory hooks
- `backend/app/services/sentence_selector.py` — raised caps, added memory_hooks/etymology to reintro cards
- `backend/tests/test_acquisition.py` — updated for new behavior, added `test_box2_no_advance_when_not_due` and `test_no_graduation_single_calendar_day`
- `backend/tests/test_leech_service.py` — updated for graduated cooldowns, added `test_leech_count_incremented_on_suspension`, `test_reintroduction_second_time_needs_7_days`, `test_reintroduction_third_time_needs_14_days`

---

## 2026-02-13: Sentence Pre-Generation + Session Cache Staleness

**Change**: Added `POST /api/review/warm-sentences` background endpoint and 30-minute session cache staleness TTL.

**What changed**:
1. `material_generator.py` — new `warm_sentence_cache()` background task: identifies focus cohort words + likely auto-introductions with < 2 active sentences, generates for up to 15 words
2. `review.py` — new `POST /api/review/warm-sentences` endpoint (returns 202, runs background)
3. `offline-store.ts` — sessions now stored with `cached_at` timestamp; `getCachedSession()` skips entries older than 30 minutes
4. `index.tsx` — 3-card trigger now calls `warmSentences()` in addition to `prefetchSessions()`
5. `api.ts` — new `warmSentences()` API function

**Why**: Separates expensive sentence generation (persistent, can be done ahead of time) from session assembly (cheap, must be fresh). If user does back-to-back sessions, cached session loads instantly. If user waits >30 min, stale cache is discarded and session rebuilds fresh — but pre-generated sentences are already in DB so build is fast.

**Expected effect**: Faster session transitions. No wasted LLM tokens (sentences persist regardless). Stale sessions no longer served after 30-minute break.

**Verify**: Backend tests pass (662). Frontend tests pass (73). TypeScript clean. Deploy and test: do 2 sessions back-to-back (instant), wait 31 min, pull new session (rebuilds fresh, should be fast).

**Files**: `material_generator.py`, `review.py`, `offline-store.ts`, `index.tsx`, `api.ts`

---

## 2026-02-13: Session Fill Phase — Continuous Learning When Due Words Exhausted

**Change**: Added a fill phase to `build_session()` that introduces more words when the session would otherwise be undersized. Uses relaxed caps (acquiring≤50 vs 30, box1≤15 vs 8) and on-demand sentence generation.

**What changed**:
1. `_auto_introduce_words()` now accepts `has_due_words` and `skip_material_gen` params for fill-phase behavior
2. `_with_fallbacks()` runs a fill phase when `len(items) < limit`: re-calls auto-introduce with relaxed caps, generates sentences on-demand
3. New constants: `MAX_ACQUIRING_CEILING=50`, `MAX_BOX1_WORDS_FILL=15`
4. Removed internal double-cap in `_generate_on_demand()` (callers already pass appropriate limits)
5. Almost-due fallback now takes 3x candidates before cohort filtering

**Why**: After 2-3 sessions, words advance through Leitner boxes (not due for 4h/1d/3d). With the box 1 cap at 8, auto-intro stopped and sessions shrank to ~5 almost-due catch-up cards despite hundreds of encountered words waiting. User expects continuous learning limited only by their performance.

**Expected effect**: Sessions stay full (≈10 cards) even when all previously-reviewed words are ahead of schedule. New words flow in continuously as long as the user performs well. Tomorrow's sessions may be larger (more box 2 words coming due), but the cohort + session limit caps prevent overload.

**Risk**: More acquiring words in flight (up to 50). Mitigated by: cohort still caps review pool at 100, session limit still 10, accuracy check still gates introduction rate. User explicitly wants more aggressive learning.

**Verify**: `python3 -m pytest` passes (662 tests). Do 3-4 sessions consecutively — session should never shrink below limit while encountered words exist.

**Files**: `sentence_selector.py`, `docs/scheduling-system.md`, `CLAUDE.md`

---

## 2026-02-13: All Words Learnable — Function Word Exclusions Removed

**Change**: Emptied FUNCTION_WORDS set so all words (prepositions, pronouns, conjunctions, demonstratives) are now fully learnable with FSRS tracking. Added rich grammar particle info for 12 core particles in frontend.

**What changed**:
1. `FUNCTION_WORDS = set()` — `_is_function_word()` always returns False
2. All words now count for comprehensibility gate, get FSRS review credit, appear in sentence generation vocabulary
3. FUNCTION_WORD_GLOSSES kept as fallback glosses for words without lemma entries
4. FUNCTION_WORD_FORMS kept for clitic analysis prevention (e.g. كانت → كان, not ك+انت)
5. Frontend `grammar-particles.ts`: 12 core particles (في، من، على، إلى، عن، مع، ب، ل، ك، و، ف، ال) show rich grammar info (meaning, category, examples, grammar notes) in WordInfoCard
6. Fixed code inconsistency: all `bare in FUNCTION_WORDS` → `_is_function_word(bare)` across routers and services

**Why**: User couldn't track learning of words like يوجد (there is), كان (was), هو (he). Tapping them showed only a single-word gloss with no detail, no conjugation info, no FSRS scheduling. The distinction between "function word" and "content word" was artificial for a learner — all words need to be tracked.

**Expected effect**: All ~100 formerly-excluded words become eligible for FSRS scheduling, appear in review sessions, and get tracked. Particles show richer grammar info when tapped. No change for words already being tracked.

**Risk**: More words competing for review slots. Mitigated by existing focus cohort (MAX_COHORT_SIZE=100) and adaptive introduction gating.

**Verify**: `python3 -m pytest tests/` passes (656 tests). Frontend type-checks clean. Words like في, هو, كان now appear in word browser and get review credit.

**Files**: `sentence_validator.py`, `sentence_selector.py`, `review.py`, `words.py`, `grammar-particles.ts`, `WordInfoCard.tsx`, plus 4 test files

---

## 2026-02-13: Backend Data Fetching Optimization

**Change**: Four-batch backend performance optimization — no API contract changes.

1. **Review commit storm → single transaction**: Added `commit: bool = True` parameter to `submit_review()`, `submit_acquisition_review()`, and `record_grammar_exposure()`. `submit_sentence_review()` passes `commit=False` to all sub-calls, doing one commit at the end. Reduces 8-9 SQLite fsyncs to 1 per sentence review. Same for `complete_story()`.

2. **Word selector batch queries**: `select_next_words()` pre-fetches root familiarity (total/known counts per root), latest intro dates, and grammar exposure in 4-5 bulk queries. Scoring loop uses dict lookups instead of per-candidate DB calls. Reduces ~1500-2000 queries to <20 for 500 candidates.

3. **Stats SQL rewrites**: `_count_due_cards()` uses `json_extract()` in SQL instead of loading all FSRS JSON into Python. `_get_first_known_dates()` uses `GROUP BY + MIN()` with `json_extract` filter. `_get_root_coverage()` uses single JOIN + GROUP BY instead of N+1 per root. `_get_recent_sessions()` batches comprehension query.

4. **Minor N+1 fixes**: Word lookup sibling ULK batch-loaded. Proper names story lookups batch-loaded. Function word filtering over-fetches to ensure correct count. `_build_knowledge_map()` accepts optional `lemma_ids` param.

**Why**: While nothing was broken (single-user SQLite), the commit storms and query fan-outs wasted I/O unnecessarily. Clean internal optimization.

**Expected effect**: Faster review submission, faster word selection, faster stats page. No user-visible behavior change.

**Verify**: `python3 -m pytest` — 662 tests pass. New tests verify commit counts, query counts, and score parity between batch and per-item paths.

**Files**: `fsrs_service.py`, `acquisition_service.py`, `grammar_service.py`, `sentence_review_service.py`, `story_service.py`, `word_selector.py`, `stats.py`, `review.py` (router), `words.py` (router)

---

## 2026-02-13: Progress Visibility & Stats Screen Overhaul

**Change**: Four-part improvement to learning progress visibility.

1. **analyze_progress.py script**: Created comprehensive server-side analysis script replacing inline Python in Claude skill. Covers knowledge states, acquisition pipeline, graduations, session breakdown, comprehension by word count, rating distributions, response times, struggling words, and yesterday vs today comparison. Supports `--days N` flag.

2. **Stats screen: 3 new components**:
   - **TodayHeroCard**: Replaces the old "today" banner. Shows sentence count, comprehension bar (understood/partial/no_idea), calibration signal, graduated word pills, streak.
   - **AcquisitionPipelineCard**: Three-column Leitner box view (Box 1 → Box 2 → Box 3) showing words with accuracy, expandable, recent graduations.
   - **SessionHistoryCard**: Renders existing `recent_sessions` data (was computed but never shown). Last 7 sessions with mini comprehension bars.

3. **Tab bar swap**: Stats promoted to main tab bar (was hidden), New Words moved to More menu.

4. **Data fix**: Reset أساتِذة (teachers) from "known" to "acquiring" — inconsistent state (no FSRS card, 33% accuracy, never graduated).

**Backend extensions**: Added `comprehension_today`, `graduated_today`, `calibration_signal` to `AnalyticsOut`. Added `acquisition_pipeline` to `DeepAnalyticsOut`. New schemas: `GraduatedWord`, `AcquisitionWord`, `RecentGraduation`, `AcquisitionPipeline`.

**Why**: Post-pipeline-overhaul analysis showed healthy data (93% rating-3 on FSRS, zero "no_idea") but user couldn't see daily progression. Acquisition pipeline (words moving through Leitner boxes toward graduation) was invisible.

**Expected effect**: User sees at a glance how today went, which words are progressing, and how recent sessions compared.

**Verify**: Stats screen renders new components. `analyze_progress.py` outputs comprehensive data. Tab bar has Stats instead of New Words.

**Files**: `stats.py`, `schemas.py`, `stats.tsx`, `_layout.tsx`, `more.tsx`, `types.ts`, `analyze_progress.py`, `analyze-learning.md`

---

## 2026-02-13: Memory Hooks — Mnemonics, Cognates, Collocations

**Change**: New `memory_hooks_json` field on Lemma with LLM-generated memory aids: mnemonic (sound-based imagery), cognates (across 11 learner languages), collocations (diacritized Arabic phrases), usage context, and fun facts. JIT generation as a background task when words are introduced via Learn mode. Seed backfill script for currently learning words.

**Why**: The word detail card shows factual etymology but lacks creative memory aids. Research shows bizarre imagery, sound-alikes, emotional connections, and cross-lingual cognates dramatically improve retention. The learner speaks English, Norwegian, Hindi, German, French, Italian, Spanish, Greek, Latin, Indonesian, and some Russian — an unusually rich base for cognate connections (especially Hindi/Indonesian from Islamic influence, Spanish from 800 years of Moorish rule).

**Schema**: `{mnemonic, cognates: [{lang, word, note}], collocations: [{ar, en}], usage_context, fun_fact}` — all fields nullable. Function words get null.

**Files**: `memory_hooks.py` (service), `backfill_memory_hooks.py` (seed script), migration `r8j3k4l5m901`, models.py, learn.py (JIT trigger), words.py/review.py/schemas.py (API responses), word/[id].tsx (full UI section), WordInfoCard.tsx (mnemonic line), learn.tsx/index.tsx (mnemonic on cards).

**Expected effect**: Richer word detail with creative memory aids. Cognate connections leverage polyglot background. JIT generation means no wasted LLM calls on unused words.

**Verify**: Deploy, run `backfill_memory_hooks.py --limit=100` for existing words. Check word detail page shows Memory Hooks section. Introduce a new word via Learn mode → verify hooks generated in background.

---

## 2026-02-13: Cross-Model Quality Review + Sentence Retirement & Regeneration

**Change**: Three-part improvement to sentence quality management.

1. **Sentence retirement (quality audit)**: Ran Gemini Flash quality audit on all 207 active sentences. 99 failed (48%) — gender mismatches, nonsensical clauses, word salad. All GPT-5.2 artifacts. Retired immediately. Further manual review of translations caught 9 more problematic sentences (wrong verbs, bad prepositions, nonsensical meaning). Final active count: 176.

2. **Regeneration with new pipeline**: After deploying the pipeline overhaul (Gemini Flash generation, KNOWN_SAMPLE_SIZE=500, POS-grouped vocab, fail-closed gate), ran `update_material.py`. Generated 77 new sentences in 4.5 minutes. Coverage went from 75 words → 103 words covered.

3. **Cross-model quality reviewer**: Benchmarked 3 models as quality reviewers against all 176 active sentences:
   - Gemini Flash (self-review): 28/176 (16%) — reasonable but misses own generation blind spots
   - Claude Haiku (strict prompt): 71/176 (40%) — catches more but over-flags benign sentences
   - GPT-5.2: 170/176 (97%) — broken, returns malformed JSON with "missing" for all reasons

   Switched quality gate from Gemini Flash self-review to **Claude Haiku with relaxed prompt** (12.5% flag rate). Relaxed prompt focuses on grammar errors, translation accuracy, and coherence — does NOT reject sentences for unusual scenarios or textbook-style simplicity. Cross-model review catches blind spots that self-review misses (e.g., Gemini doesn't catch gender agreement errors in its own output).

**Why**: GPT-5.2 sentences were systematically bad (48% failure rate). Self-review (Gemini reviewing Gemini) has inherent blind spots — the same model makes the same mistakes consistently. Cross-model review (Gemini generates → Haiku reviews) catches different error classes.

**Expected effect**: Higher baseline sentence quality. Cross-model review catches translation mismatches and grammar errors that self-review misses. Relaxed prompt avoids over-rejecting pedagogically valid simple sentences.

**Verify**: Monitor quality gate rejection rate in `update_material.py` logs. Target: 10-15% rejection rate (was 16% Gemini self-review, now 12.5% Haiku cross-review). Run periodic `review_existing_sentences.py` audits.

**Files**: `llm.py` (review_sentences_quality model_override + prompt), `review_existing_sentences.py` (audit tool)

---

## 2026-02-13: Sentence Pipeline Overhaul

**Change**: Seven-part overhaul based on benchmarking 213 sentences across 3 models × 6 strategies. Full investigation reports in `research/sentence-investigation-2026-02-13/`.

1. **KNOWN_SAMPLE_SIZE 50 → 500**: The #1 source of validation failures. GPT-5.2 compliance jumped 57% → 88% with full vocab in testing. 50 was a holdover from early development. At ~500 words, full vocab fits well within any model's context window (~3,500 tokens).

2. **Quality gate fail-closed**: Previously, if Gemini Flash was unavailable for quality review, sentences passed automatically (fail-open). Bad sentences reached users when Gemini was down. Now rejects on LLM failure — better to skip a sentence than show a bad one.

3. **Switch generation model: GPT-5.2 → Gemini Flash**: GPT-5.2 scored 4.63/5 quality and produced all 5 "word salad" sentences in benchmarking. Gemini Flash scored 4.89/5, 84% compliance, cheapest, fastest. Switched all generation defaults (generate_sentence, generate_sentences_batch, generate_sentences_multi_target, update_material.py).

4. **POS-grouped vocabulary in prompts**: Organizing known words by part of speech (NOUNS/VERBS/ADJECTIVES/OTHER) scored 5.0/5 quality and 87% compliance in benchmarking. Helps LLM select appropriate words for syntactic positions. Added `format_known_words_by_pos()` helper, added `pos` field to known_words dicts in all code paths.

5. **Fix validator: inflected forms in known_bare_forms**: `validate_sentence()` was building `known_bare_forms` from base lemma forms only (e.g., `واسع`), but generated sentences use inflected forms (e.g., `واسعة` feminine). Meanwhile `build_lemma_lookup()` already indexes inflected forms from `forms_json`. Fix: use `set(lemma_lookup.keys())` for `known_bare_forms` when available.

6. **Fix validator: function words after clitic stripping**: After stripping `و` from `ولكنه`, the stem `لكن` was checked only against `known_normalized` — but `لكن` is a function word (not in the known words set). Added `_is_function_word()` check after clitic stripping in both `validate_sentence()` and `validate_sentence_multi_target()`.

7. **Documentation updates**: Updated CLAUDE.md, experiment-log.md, IDEAS.md.

**Why**: Investigation showed the sentence pipeline had systematic quality issues traceable to model choice (GPT-5.2 worst quality), vocabulary visibility (KNOWN_SAMPLE_SIZE=50), fail-open quality gate, and two validator false-positive bugs.

**Expected effect**: Higher sentence quality (Gemini Flash > GPT-5.2), fewer validation failures (full vocab + inflected forms + function word fix), no bad sentences slipping through when quality gate LLM is unavailable.

**Verify**: Run `update_material.py` after deploy. Monitor sentence_gen logs for retry rates and quality review rejection rates. Expected: <10% validation failure rate (was ~40%).

**Files**: `llm.py`, `sentence_generator.py`, `sentence_validator.py`, `material_generator.py`, `sentence_selector.py`, `update_material.py`, `story_service.py`

---

## 2026-02-12: Gemini Flash Quality Review Gate + Prompt Overhaul

**Change**: Two-part fix for 57% sentence quality failure rate.

1. **Quality review gate**: Added `review_sentences_quality()` using Gemini Flash 3 as post-generation reviewer. After GPT-5.2 generates a sentence and it passes rule-based validation, Gemini Flash reviews for naturalness and translation accuracy. Rejected sentences feed back into the retry loop with specific feedback. Fails open (if Gemini unavailable, sentence passes). Applied to both single-target and multi-target paths.

2. **Prompt overhaul**: Fixed the root causes of bad generation:
   - Added explicit rules against indefinite noun (nakira) sentence starters — the most common failure
   - Added rules against redundant subject pronouns after verbs (تَسْكُنُ هِيَ)
   - Added semantic coherence requirement for compound sentences (no unrelated clause joining)
   - Added fragment/catalog-style rejection guidance
   - Added beginner-specific archaic/formal word exclusion (no لَعَلَّ، كَأَنَّ، يا سادة at beginner level)
   - Lowered single-target temperature from 0.8 → 0.5 (matches batch)
   - Changed subject preference: pronouns and generic definite nouns over proper names

3. **Bulk cleanup**: Reviewed all 210 active sentences with Gemini Flash, retired ~151 that failed quality review (gender mismatches, nonsensical combinations, archaic words, fragments, unnatural constructions).

**Why**: 57% of existing sentences were flagged as unnatural by Gemini Flash. GPT-5.2 was generating grammatically passable but semantically weird sentences due to missing naturalness rules in the prompt. Temperature 0.8 was too creative for constrained generation.

**Expected effect**: New sentences should be significantly more natural. Quality review gate catches remaining bad ones before they reach the user. Sentence pool temporarily reduced to ~61 active, will be rebuilt by update_material.py cron with the improved pipeline.

**Verify**: Monitor sentence_gen logs for retry rates. Run `review_existing_sentences.py --dry-run` after next material generation batch to check new failure rate (target: <15%).

---

## 2026-02-12: Box 1 Capacity Cap for Auto-Introduction

**Change**: Added `MAX_BOX1_WORDS=8` constraint to `_auto_introduce_words()`. Auto-introduction now checks how many acquiring words are in Leitner box 1 (the most review-intensive stage) and refuses to introduce more if box 1 is at capacity. The final slot count is `min(accuracy_band, MAX_ACQUIRING_WORDS - acquiring_count, MAX_BOX1_WORDS - box1_count)`.

**Problem**: On 2026-02-12, two `build_session()` calls fired within 51 seconds (at 18:23 and 18:24), each introducing 10 words (the per-session max). This dumped 20 new words into box 1 simultaneously. When they all became due 4 hours later, the session ballooned to 25 cards. 5 of the 27 auto-introduced words were never reviewed at all (times_seen=0).

**Hypothesis**: The per-session cap (MAX_AUTO_INTRO_PER_SESSION=10) is insufficient because multiple rapid `build_session()` calls bypass it. A capacity-based constraint on box 1 occupancy is self-regulating: study more → words progress to box 2/3 → box 1 frees up → more introductions allowed. Skip a day → box 1 stays full → no new words until catch-up.

**Verification**: Monitor `auto_introduce` interaction log events. Box 1 count should stay ≤8. Sessions should stay within normal 10-15 card range. No more 25-card avalanche sessions.

**Files**: `app/services/sentence_selector.py`, `docs/scheduling-system.md`

---

## 2026-02-12: Route Collateral & OCR Words Through Acquisition

**Change**: Two paths that bypassed the Leitner acquisition phase now route through it:
1. **Collateral credit**: When a word with no ULK appears in a reviewed sentence, it now starts acquisition (box 1, source="collateral", due_immediately=False) instead of getting a direct FSRS card with knowledge_state="learning".
2. **OCR import toggle**: `POST /api/ocr/scan-pages?start_acquiring=true` starts scanned words in acquisition immediately (box 1, due_immediately=True). Default remains "encountered" for backward compatibility. Frontend scanner has a "Start learning immediately" toggle (default on).

**Hypothesis**: Words need the structured 4h→1d→3d Leitner ramp to build durable memory. Skipping straight to FSRS gives inflated stability that leads to premature long intervals and eventual lapsing. Scanning a textbook page = the user just read those words, so they should be scheduled for follow-up instead of sitting in a queue.

**Verification**: Monitor new "collateral" source ULKs — they should have acquisition_box set and no fsrs_card_json. OCR with toggle on should show words in "acquiring" state. Check that variant detection post-OCR correctly resets variant ULKs.

**Files**: `app/services/sentence_review_service.py`, `app/services/ocr_service.py`, `app/routers/ocr.py`, `frontend/lib/api.ts`, `frontend/app/scanner.tsx`

---

## 2026-02-12: Adaptive Auto-Introduction Rate

**Change**: Replaced binary pause/continue auto-introduction logic with graduated accuracy-based ramp. `_intro_slots_for_accuracy()` maps 2-day accuracy to slot count: <70%→0 (pause), 70-85%→4 (normal), 85-92%→7 (increased), ≥92%→10 (max). Default 4 slots when <10 reviews (was 10).

**Hypothesis**: Strong learners (>92% accuracy) were being throttled at the same rate as struggling learners. Graduated ramp should increase vocabulary growth for proficient learners without overwhelming those who are struggling.

**Verification**: Monitor `auto_introduce` interaction log events which now include `accuracy` and `accuracy_slots` fields. Compare intro rates across accuracy bands over 2 weeks.

**Files**: `app/services/sentence_selector.py`, `tests/test_sentence_selector.py`

---

## 2026-02-12: Multi-Target Sentence Generation

**Change**: Added ability to generate sentences targeting SETS of 2-4 words simultaneously. Words grouped via `group_words_for_multi_target()` (avoids same-root pairs). Each sentence must contain ≥2 target words. Used in both on-demand (session building) and cron (update_material.py) paths. Falls back to single-target on failure.

**Hypothesis**: Multi-target sentences provide natural cross-reinforcement, reduce LLM calls (1 call for 4 words vs 4 calls), and produce more varied sentence structures.

**Verification**: Compare LLM call counts in `update_material.py` logs before/after. Check that multi-target sentences pass validation at a reasonable rate (>50%). Monitor session builder on-demand generation latency.

**Files**: `app/services/llm.py`, `app/services/sentence_validator.py`, `app/services/sentence_generator.py`, `app/services/material_generator.py`, `app/services/sentence_selector.py`, `scripts/update_material.py`

---

## 2026-02-12: Tighter Leech Detection Thresholds

**Change**: `LEECH_MIN_REVIEWS` 8→5, `LEECH_MAX_ACCURACY` 0.40→0.50. Leeches now caught after 5 reviews at <50% accuracy (was 8 at <40%).

**Hypothesis**: Original thresholds were too loose — words had to fail 8+ times at <40% before suspension. Earlier detection (5 reviews, <50%) saves review time and gets struggling words into the 14-day rest + reintroduction cycle sooner.

**Verification**: Monitor `leech_suspended` activity log events. Expect more leeches detected in the first week post-change, then stabilizing. Check that the tighter threshold doesn't over-suspend words that would have recovered.

**Files**: `app/services/leech_service.py`

---

## 2026-02-12: Diacritics + ALA-LC Transliteration Backfill

**Change**: Added deterministic Arabic→ALA-LC transliteration service (`transliteration.py`) and backfilled diacritics + transliterations for all lemmas. 1,022 bare lemmas were diacritized via LLM (Gemini Flash). 97% of ULK words now have diacritics, 90% have transliteration. Transliteration now shows on word info cards during review.

**Motivation**: Word lookup cards during review showed no transliteration because `transliteration_ala_lc` was NULL for all 1,610 words. Root cause: many lemmas (from OCR, Duolingo, frequency lists) were stored without diacritics. The transliterator is deterministic and works perfectly on diacritized input — the real fix was diacritizing the source data.

**Approach**: Rule-based transliterator cross-checked against MTG/ArabicTransliterator and CAMeL-Lab/Arabic_ALA-LC_Romanization. Handles: long vowels, shadda/gemination, hamza carriers (initial=silent, medial/final=ʾ), alif madda (initial=ā, medial=ʾā), alif wasla, sun letter assimilation after al-, tāʾ marbūṭa, alif maqsura, nisba ending (ِيّ→ī), dagger alef. No LLM needed for transliteration — only for diacritization of bare words.

**Files**: `app/services/transliteration.py`, `scripts/backfill_diacritics.py`, `scripts/backfill_transliteration.py`, `frontend/lib/review/WordInfoCard.tsx`

---

## 2026-02-12: SAMER Readability Lexicon Integration

**Change**: Backfilled `cefr_level` on 1,365/1,610 lemmas from SAMER v2 readability lexicon (40K MSA lemmas, L1-L5 human-annotated difficulty, mapped to CEFR A1-C1). Added auto-backfill step (Step D) in `update_material.py` cron so new lemmas get levels automatically.

**Motivation**: `cefr_level` was sparsely populated. SAMER provides human-judged difficulty independent of frequency — e.g., قَد (very frequent but L3/B1 because it's grammatically complex). Enables better sentence difficulty scoring and word introduction ordering.

**Distribution**: L1/A1=678, L2/A2=186, L3/B1=171, L4/B2=163, L5/C1=167. 245 unmatched (mostly plural/inflected forms stored as lemmas).

**Also investigated**: BAREC corpus (69K sentences, 19 readability levels) as a sentence source. Only ~50% diacritized, low levels are junk (headers/fragments), many are context-dependent excerpts. Not practical as drop-in replacement for LLM generation. ~3,700 usable diacritized sentences. Filed findings in IDEAS.md.

**Files**: `scripts/backfill_samer.py`, `scripts/update_material.py` (Step D), `backend/data/samer.tsv` (server only, not in git — license: non-commercial, no redistribution).

---

## 2026-02-12: Multi-Session Simulation Framework

**Change**: Added end-to-end simulation framework (`backend/app/simulation/`) that drives real services (sentence selector, review service, acquisition Leitner, FSRS, auto-introduction, cohort, leech detection) over multiple simulated days against a copy of the production database. Uses `freezegun` for time control.

**Motivation**: Need to observe how the algorithms interact over time — do words graduate from acquisition? Does auto-introduction pace well? Do review loads spike? Do leeches accumulate? The existing `simulate_usage.py` only tests raw FSRS in isolation.

**Profiles**: beginner (55% comprehension), strong (85%), casual (70%), intensive (75%). Each defines session frequency, size, and word-level comprehension probability based on knowledge state.

**Usage**: `python3 scripts/simulate_sessions.py --days 30 --profile beginner`

**Verification**: Run pytest `tests/test_simulation.py` (6 tests, synthetic data). Run CLI against latest backup for real-data validation.

**Files**: `app/simulation/{__init__,db_setup,student,runner,reporter}.py`, `scripts/simulate_sessions.py`, `tests/test_simulation.py`

---

## 2026-02-12: py-fsrs v6 Pin

**Change**: Pinned `fsrs>=6.0.0` (was `>=4.0.0` which already resolved to v6.3.0 in production). Cleaned up dead `scheduled_days` reference in fsrs_service.py review log — v6 cards don't have this field, replaced with `stability`. Verified 0 v4 card dicts remain in DB (all 53 active FSRS cards are v6 format). FSRS-6's w17-w19 parameters provide native same-day review support, which works well with our Leitner acquisition → FSRS graduation pipeline.

---

## 2026-02-12: Story Suspend/Reactivate + ActionMenu Refactor

**Change**: Added story suspend/reactivate toggle. `POST /api/stories/{id}/suspend` toggles between active↔suspended. Suspended stories show dimmed in list with "Suspended" badge, pause/play button on each card. ActionMenu moved from bottom to header bar in story reader, now supports `extraActions` prop for screen-specific actions (suspend story is the first).

**Files**: `story_service.py`, `routers/stories.py`, `models.py`, `stories.tsx`, `story/[id].tsx`, `ActionMenu.tsx`, `api.ts`, `types.ts`

---

## 2026-02-12: Sparkline Inter-Review Gaps

**Problem**: Word list sparklines show last 8 ratings as pass/fail dots but give no information about timing between reviews. Knowing a word after 5 minutes vs. knowing it after 3 days is very different — the current display conflates these.

**Change**: Backend now returns `last_review_gaps` (hours between consecutive reviews) alongside `last_ratings`. Frontend sparkline uses variable gap widths between dots on a log scale: <1h = 1px, same-day = 2px, 1-3d = 4px, 3-7d = 6px, >7d = 9px. Wider visual gaps = longer time between reviews.

**Expected effect**: At a glance, tightly clustered dots indicate cramming/same-session reviews, while spread-out dots indicate spaced practice with real retention. Helps identify words reviewed only in quick succession vs. genuinely spaced.

**Verification**: Check word list in app — words with spaced reviews should show visibly wider gaps than words reviewed multiple times in one session.

---

## 2026-02-12: Phase 5 — Uncap the Learning Pipeline

**Problem**: Algorithm redesign works correctly but is strangled by conservative caps. With 492 encountered words idle, 8 acquiring, and only 3 words introduced per session, sessions are 3-4 cards and the user runs out of material in seconds. Sentences are 3-4 word fragments due to hardcoded "beginner" difficulty. Words like جار (box 3, 14 reviews, 79% accuracy) can't graduate because graduation only fires on rating≥3. SQLite locking from parallel deep prefetch causes 500 errors.

**Changes**:
1. **Blow open caps**: MAX_ACQUIRING_WORDS 8→30, MAX_AUTO_INTRO_PER_SESSION 3→10, MAX_ACQUISITION_EXTRA_SLOTS 8→15, MAX_COHORT_SIZE 25→100
2. **Raise sentence complexity**: Brand new max_words 4→7, same-day 6→9, first week 8→11, established 12→14. Removed "MUST be very short" LLM branch for max_words≤5.
3. **JIT-first sentence strategy**: Keep MIN_SENTENCES=2 as warm cache, raised MAX_ON_DEMAND_PER_SESSION 5→10 for JIT generation with current vocabulary. TARGET_PIPELINE_SENTENCES 200→300. Pre-generated sentences go stale as vocabulary grows; JIT sentences use current known words for better calibration.
4. **Dynamic difficulty**: material_generator.py and update_material.py now call `get_sentence_difficulty_params()` instead of hardcoded "beginner".
5. **Fix graduation bug**: Graduation now fires regardless of current review's rating — checks cumulative stats (box≥3 + times_seen≥5 + accuracy≥60%) after every review.
6. **Fix SQLite locking**: deepPrefetchSessions count 3→2 with 500ms delays, word lookup prefetch sequential after deep prefetch (not parallel).

**Files**: sentence_selector.py, cohort_service.py, word_selector.py, llm.py, material_generator.py, acquisition_service.py, update_material.py, frontend/lib/api.ts

**Expected effect**: Sessions grow from 3-4 cards to 10-15+. Sentences are 5-10 words (not 3-4 fragments). 30+ words can be in acquisition simultaneously. Words with strong cumulative stats graduate faster. No more 500 errors from prefetch.

**Verification**: All backend tests pass (642). All frontend tests pass (74). Deploy + run `update_material.py --max-sentences 300` to backfill sentences. Post-deploy: retired short sentences (≤4 words), regenerated with dynamic difficulty. Final avg 5.1 words/sentence.

---

## 2026-02-12: Topical Learning Cycles

**Problem**: Word introduction via Learn mode pulled from all 20 thematic domains at once, mixing unrelated vocabulary (e.g., food + politics + nature in the same session). Research on semantic clustering (Tinkham 1993/97) shows mixing unrelated domains increases cognitive interference and slows acquisition.

**Changes**:
1. **LearnerSettings model** (`models.py`): Singleton row tracking `active_topic`, `topic_started_at`, `words_introduced_in_topic`, `topic_history_json`. Alembic migration added.
2. **topic_service.py**: Core logic for topical learning cycles. 20 thematic domains, `MAX_TOPIC_BATCH=15` words per topic, `MIN_TOPIC_WORDS=5` minimum available before auto-advancing. `get_or_create_settings()`, `get_current_topic()`, `advance_topic()`, `get_all_topics()`. Auto-selects topic with most available (encountered, non-variant, non-suspended) words.
3. **word_selector.py**: `select_next_words()` now filters candidates by active topic domain. Falls back to unfiltered selection if topic filtering yields too few candidates.
4. **Settings API** (`routers/settings.py`): Three endpoints — `GET /api/settings/topic` (current topic + progress), `PUT /api/settings/topic` (manual override), `GET /api/settings/topics` (all domains with available/learned counts).
5. **Frontend**: Topic display on Learn screen showing current domain + progress. `topic-labels.ts` maps 20 domain keys to human-readable labels + icons.

**Data**: All 1610 lemmas already tagged with `thematic_domain` via `backfill_themes.py`. Auto-selected "nature" (215 available words) as first topic on initial deploy.

**Files**: `models.py`, `topic_service.py`, `word_selector.py`, `routers/settings.py`, `schemas.py`, `frontend/lib/topic-labels.ts`, `frontend/app/learn.tsx`, `frontend/lib/api.ts`

**Expected effect**: Words introduced in thematically coherent batches. Learner builds domain-specific vocabulary clusters before moving on. Auto-advance prevents getting stuck on depleted topics.

**Verification**: Deployed to production. First topic "nature" auto-selected. `GET /api/settings/topics` returns all 20 domains with counts.

---

## 2026-02-12: Learning Phase Redesign — Auto-Intro, Aggressive Repetition, Smaller Cohort

**Problem**: After the initial algorithm redesign (Phases 1-4), the session builder still relied on user-driven word introduction via Learn mode and only repeated acquiring words twice per session. With 40-word cohorts and only 2 exposures, new words weren't getting enough concentrated practice. Additionally, many words had FSRS cards despite never being genuinely learned (times_seen < 5 or accuracy < 60%).

**Changes**:

1. **Data reset** (`reset_to_learning_baseline.py`): New script resets words without genuine learning signal (times_seen >= 5 AND accuracy >= 60% as the keep threshold) back to "encountered" state. Preserves all review history. Production run: 50 words kept as FSRS, 102 reset to encountered.

2. **Auto-introduction in build_session()**: Removed `_get_intro_candidates()` and `MAX_INTRO_PER_SESSION = 2` (the old inline intro card system). Added `_auto_introduce_words()` which auto-introduces 2-3 encountered words per session when acquiring count < MAX_ACQUIRING_WORDS=8 and recent accuracy >= AUTO_INTRO_ACCURACY_FLOOR=0.70. `introduce_word()` now accepts `due_immediately` param, threaded through to `start_acquisition(due_immediately=True)` so auto-introduced words get `acquisition_next_due=now` and appear in the current session.

3. **Aggressive within-session repetition**: Changed from max 2 exposures to MIN_ACQUISITION_EXPOSURES=4 per acquiring word. Multi-pass loop with expanding intervals. Session size expands up to MAX_ACQUISITION_EXTRA_SLOTS=8 extra cards. MAX_ON_DEMAND_PER_SESSION increased from 3 to 5.

4. **Cohort reduction**: MAX_COHORT_SIZE reduced from 40 to 25. Tighter focus = more repetitions per word per session.

5. **Comprehensibility gate fix**: Gate now counts "encountered" words as passive vocabulary (user has seen them even if not formally studying). `all_knowledge` query includes encountered words.

**Files**: `sentence_selector.py`, `word_selector.py`, `acquisition_service.py`, `cohort_service.py`, `reset_to_learning_baseline.py`

**Production verification**: After deploy, session returned 18 items. 11 of 14 due words covered (3 had no comprehensible sentences — on-demand generation filled some). Acquiring words appeared 4x each across the session. Auto-introduction working (encountered words picked up when acquiring count is low).

**Next steps**: Topical learning cycles (Phase 4 from redesign plan), story improvements (Phase 5), themed sentence generation (Phase 6).

---

## 2026-02-12: Legacy Word-Level Review Code Removal

**Problem**: The codebase still contained legacy word-only review infrastructure from before the sentence-first redesign. This included backend endpoints (`/api/review/next`, `/api/review/submit`), the `get_due_cards()` service function, frontend components (`WordOnlySentenceCard`, `LegacyListeningCard`, `LegacySentenceCard`, `LegacyWordOnlyCard`), legacy TypeScript types (`ReviewCard`, `ReviewSession`, `ReviewSubmission`), and a `"legacy"` sync queue type. Despite the design principle "no bare word cards in review," the frontend still had a fallback path that would show word-only cards when sentence sessions failed to load.

**Changes**:
1. **Backend**: Removed `/api/review/next` endpoint, `/api/review/submit` endpoint, legacy sync handler in `/api/review/sync`, `get_due_cards()` from fsrs_service.py, `ReviewCardOut`/`ReviewSubmitIn`/`ReviewSubmitOut` schemas
2. **Frontend**: Removed all legacy card components, `legacySession` state, `handleLegacySubmit()`, `usingSentences` branching variable, legacy types and API functions, `MOCK_REVIEW_CARDS` (~190 lines), `"legacy"` from sync queue types
3. **Tests**: Removed 6 legacy tests across 4 test files, updated idempotency tests to remove legacy sync items

**Scope**: ~1100 lines deleted across 14 files. Sentences are now the only review path — no fallback, no branching, no legacy code.

**Verification**: 623 backend tests pass, 74 frontend tests pass, TypeScript compiles clean. Deployed to production.

---

## 2026-02-12: Variant→Canonical Review Credit + Comprehensive Data Cleanup

**Problem**: After 6+ rounds of data cleanup (garbage roots, text sanitization, abbreviations, LLM variant detection, al-prefix dedup, OCR reset), one structural gap remained: variant detection correctly sets `canonical_lemma_id` on variant lemmas, but the system never acts on it. Each variant still had its own independent ULK/FSRS card, and sentence reviews credited the variant rather than the canonical lemma. Additionally, the LLM-based junk check was incorrectly flagging legitimate variant forms (possessives, conjugated verbs) as junk — it was rediscovering what variant detection had already found.

**Root cause**: Variant detection was a tagging system (sets canonical_lemma_id) but review credit, session scheduling, and cleanup all operated on individual lemma_ids without resolving variants.

**Changes**:
1. **Review credit redirect** (`sentence_review_service.py`): When a sentence contains a variant word, FSRS credit now goes to the canonical lemma. Variant surface forms tracked in `variant_stats_json` on canonical's ULK. Dedup prevents crediting the same canonical twice in one sentence.
2. **Variant resolution in session building** (`sentence_selector.py`): Sentences containing variant forms now correctly cover canonical due words in the greedy set cover algorithm.
3. **Deterministic variant ULK cleanup** (`cleanup_review_pool.py`): Replaced LLM junk check (step 3b) with: (a) suspend all variant ULKs, merging stats into canonical; (b) hardcode-suspend 4 true junk words (سي, واي, رود, توب). Also added step 3e: run variant detection on textbook_scan/story_import words that missed it.
4. **Fixed story_service variant detection**: Was calling `detect_variants_llm()` and `detect_definite_variants()` without `mark_variants()` — variants detected but never marked.
5. **Quality gate on all import paths**: Added to story import (in `_import_unknown_words()`) and Duolingo import (post-import suspension). OCR already had it.

**Design principle**: The canonical lemma is the unit of FSRS scheduling. Variant forms are tracked for diagnostics only, never get independent FSRS cards.

**Files**: `sentence_review_service.py`, `sentence_selector.py`, `cleanup_review_pool.py`, `story_service.py`, `import_duolingo.py`, `test_sentence_review.py`

**Expected effect**: After running cleanup: variant ULKs suspended with stats merged to canonical, review sessions correctly schedule canonical lemmas covering variant sentence forms, no more independent review of possessive/conjugated forms.

**Verification**: 629 tests pass (4 new variant redirect tests). Deployed and ran cleanup on production: 92 words reset to acquiring, 39 variant ULKs suspended (stats merged to canonical, 2 canonical ULKs created), 4 junk words suspended, 106 incomprehensible sentences retired, 126 words need sentence regeneration. ActivityLog entry written.

---

## 2026-02-12: Fix Broken Review Pipeline — Comprehensibility Gate + Data Cleanup

**Problem**: After deploying the algorithm redesign, review sessions were broken:
- 54% of words per sentence were unknown — sentences unreadable
- Only 17% of active sentences >75% comprehensible
- 34+ words in review pool had NEVER been rated ≥ 3 (never truly learned)
- Junk words (سي "c", واي "wi") from OCR imports
- Timezone bug crashing API when acquiring words exist
- No comprehensibility gate in sentence selector
- Word-only fallback cards shown instead of sentences

**Root cause**: OCR reset only handled `textbook_scan` words. Duolingo words given FSRS cards on import without learning were left in pool. Sentences generated with inflated known-word pool.

**Changes**:
1. **Timezone fix**: `sentence_selector.py:240` — naive→aware datetime conversion for acquiring word due dates
2. **Comprehensibility gate**: Skip sentences where <70% of content words are known/learning/acquiring. Added 25+ missing function words (إذا, لأن, مثل, غير, عندما, etc.)
3. **Removed word-only fallback cards**: Due words without comprehensible sentences get skipped (or on-demand generated), never shown as bare word cards
4. **On-demand sentence generation**: When a due word has no sentence, generates 1-2 synchronously using current vocabulary (max 3/session)
5. **Within-session repetition fix**: `break` → `continue` bug fix — acquisition words now properly get 2+ sentences per session
6. **Data cleanup script**: `scripts/cleanup_review_pool.py` — resets under-learned words (times_correct < 3) to acquiring, suspends junk via LLM, retires incomprehensible sentences
7. **Import quality gate**: `services/import_quality.py` — LLM batch filter for all import paths, integrated into OCR

**Files**: `sentence_selector.py`, `sentence_validator.py`, `ocr_service.py`, `import_quality.py`, `cleanup_review_pool.py`, `test_sentence_selector.py`

**Expected effect**: Review sessions should now contain only readable sentences with ≥70% known words. Under-learned words go through proper acquisition. Junk words suspended. On-demand generation fills gaps.

**Verification**: 625 tests pass. Deploy + run cleanup script on production. Verify via `curl /api/review/next-sentences`.

---

## 2026-02-12: Wrap-up Quiz Fix + Story Context on Learn Cards

**Problem**: Three issues found during first real testing of the redesigned algorithm:
1. Wrap-up quiz only showed "acquiring" words — when user had no acquiring words, wrapping up a session showed no quiz at all, even though they missed several words
2. Learn mode was recommending obscure classical Arabic vocabulary (دِمْنَة "ruin remains", نواشر "veins of forearm") from imported stories (Kalila wa Dimna, classical poetry), drowning out high-frequency common words
3. Learn cards and review intro cards showed no context about WHY a word was recommended

**Change**:
1. Wrap-up endpoint now accepts `missed_lemma_ids` in addition to `seen_lemma_ids` — returns cards for both acquiring AND missed words. Frontend sends failed word IDs from `wordOutcomes` tracking. Cards marked `is_acquiring` to distinguish types.
2. Marked stories 3 and 4 (classical Arabic) as `too_difficult` to stop them from polluting word selection
3. Word selector now returns `story_title` for words from active stories. Both Learn mode and review intro cards show "From: Story Title" badge when applicable.

**Files**: `review.py`, `schemas.py`, `sentence_selector.py`, `word_selector.py`, `index.tsx`, `learn.tsx`, `api.ts`, `types.ts`

**Expected effect**: Users will now get a word-level quiz when wrapping up a session with missed words, reinforcing the ones they struggled with. Story words show their source for motivation.

**Verification**: After deploy, wrap-up POST with `missed_lemma_ids` returns cards. Learn endpoint shows `story_title` for story words. All 620+74 tests pass.

---

## 2026-02-11: Frontend Test Infrastructure

**Problem**: No frontend tests existed at all — only backend had pytest coverage (564 tests). Frontend logic in sync-queue, offline-store, smart-filters, and the API client layer was untested.

**Change**: Set up Jest + ts-jest for the frontend with mocks for AsyncStorage, expo-constants, and netinfo. Created 4 test suites (73 tests total):
- `sync-queue.test.ts` (7 tests): enqueue/remove/pending count, dedup, offline queueing
- `offline-store.test.ts` (14 tests): mark/unmark reviewed, session cache, invalidation, story lookups, word lookup cache
- `smart-filters.test.ts` (24 tests): isLeech, isStruggling, isRecent, isSolid with boundary cases and combinations
- `api.test.ts` (28 tests): sentence review submit/undo flow, word lookup with caching, story operations (list/detail/complete/skip/import), learn mode (next words/introduce/quiz), content flagging, offline fallback for words/sessions/stats

**Files**: `frontend/jest.config.js`, `frontend/lib/__tests__/__mocks__/` (async-storage, expo-constants, netinfo), `frontend/lib/__tests__/*.test.ts`

**Expected effect**: Catch regressions in frontend logic during refactors. API test suite validates request payloads, response mapping, caching behavior, and offline fallback — the most complex frontend logic paths.

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

---

## 2026-02-12 — Post-OCR Learning Crisis: Data Analysis & Algorithm Redesign

### Findings

Full production database analysis after 100+ textbook pages imported via OCR on Feb 10.

**Vocabulary state**: 592 ULK records. Sources: textbook_scan 411 (69%), duolingo 149 (25%), study 32 (5%). knowledge_state: learning 307, known 261, lapsed 18, suspended 6.

**Critical finding — false FSRS stability**: ALL 586 active words show 30+ day FSRS stability. The textbook scan submitted `rating=3` (Good) for every word, setting initial stability to ~2.3 days. Subsequent sentence reviews where these words appeared unmarked compounded Good ratings, pushing stability to 30+ days. But the user doesn't actually know most of these words.

**Accuracy crash**: Pre-OCR accuracy was 63-78%. Post-OCR: Feb 11 = 45.8%, Feb 12 = 25.0%. The system thinks words are known; the user can't recognize them.

**Weak signal dominance**: 367 of 586 active words (63%) have been seen 0-2 times. 214 words seen exactly once. Research says 8-12 meaningful encounters needed for stable memory; <6 encounters → <30% recall after a week.

**Session patterns**: Highly variable — 3 to 35 cards. Many incomplete sessions (3-4 cards). Inter-review gaps within sessions are 0-6 minutes; between sessions 12-48 hours. Confirms user's description of unpredictable practice time.

**Leeches**: 20 words with 3+ failures. Top: الحائط (5 fails/6 reviews), صِفة (5/6, suspended), جُمَل (5/8).

**OCR import scale**: Single batch on Feb 10: 37 pages, 459 new words, 692 existing word matches.

### Root Cause Analysis

Three compounding problems identified:

1. **False FSRS state from OCR imports**: Textbook scan gave automatic Good (3) rating → FSRS interprets as "known" → schedules review in 2+ days → when word appears in sentence review and isn't individually marked, gets another Good → stability compounds. All 586 words now at 30+ days stability despite user not recognizing ~300 of them.

2. **No acquisition phase**: FSRS handles long-term retention of established memories. No mechanism for initial acquisition (Anki uses "learning steps" for this). Words go directly from first encounter → FSRS scheduling — too aggressive for genuinely new vocabulary.

3. **Pool too wide**: 586 active words competing for ~50-100 reviews/day. Each word reviewed roughly once every 6-12 days. Research: need 8-12 concentrated encounters, not 1 per week.

### Research Summary (web research conducted)

- **Encounters needed**: 8-12 meaningful encounters for stable representation (Uchihara et al. 2019 meta-analysis). <6 → <30% recall. 10+ → 80%+ recall. 20-30 for long-term embedding.
- **FSRS initial stability**: S₀(Again)=0.2d, S₀(Hard)=1.3d, S₀(Good)=2.3d, S₀(Easy)=8.3d. Single Good from textbook scan ≠ genuine recall.
- **Retrieval vs recognition**: Active recall 150% better than passive re-exposure (Conti 2025). Sentence-based recognition requires more encounters than production.
- **Desirable difficulties** (Bjork): Varying contexts, interleaving, within-session spacing all enhance learning.
- **N-of-1 experiments**: ~400 observations per condition needed. At 100-200 reviews/day, 2-4 weeks per experiment. Crossover design with AR(1) covariance.

### Proposed Changes (not yet implemented)

1. **Data fix**: Reset FSRS cards for textbook_scan words with ≤3 real reviews. Set stability to 0.5d, state to "learning".
2. **Acquisition phase**: Pre-FSRS stage for words with <5 reviews or <50% accuracy. Appear 2-3x per session. Graduate after 5+ reviews at 60%+ accuracy.
3. **Focus cohort**: Rolling set of ~30-50 words. New words enter only as existing ones graduate. Prevents spreading too thin.
4. **Session-level repetition**: Select sentences that repeat acquisition words 2-3x within a 10-card session.
5. **OCR import options**: "Track only" (no FSRS card), "studied today" (current), "studied N days ago" (backdated).
6. **Batch sentence generation**: Generate for word sets instead of individual targets.

### Hypotheses

- **H13**: Resetting textbook_scan FSRS cards will immediately improve session accuracy (from 25% → 50%+) by allowing the system to correctly identify which words the user actually knows.
- **H14**: An acquisition phase requiring 5+ reviews before FSRS graduation will produce higher day-7 retention than direct FSRS scheduling for new words.
- **H15**: Focus cohort of 30-50 words will lead to faster individual word consolidation (more words reaching stability >7 days per week) despite reviewing fewer total words.
- **H16**: Within-session repetition (same word in 2-3 sentences per session) will improve next-day recall by >20% compared to single exposure.

### How to Verify

- H13: Compare session accuracy before/after the FSRS reset (immediate)
- H14: A/B test with `experiment_group` on ULK, track day-7 accuracy (3-4 weeks)
- H15: Compare words reaching stability >7d per week before/after cohort system (2 weeks)
- H16: Compare next-day recall for words seen 1x vs 2-3x within session (2 weeks)

### Deep Research (8-agent swarm, same day)

Comprehensive deep research conducted via 8 parallel agents covering: FSRS-6 internals, cognitive science of memory, Arabic-specific learning, session design, sentence-centric SRS, leech management, N-of-1 experimental design, and full codebase analysis.

Key findings that refine the above proposals:
- **py-fsrs 6.x** has native same-day review support (w17-w19) — upgrade from v4.x
- **Leitner-like acquisition phase** (3-box: 4h→1d→3d) is simpler and better-justified than custom logic
- **Semantic clustering impedes learning** (Tinkham 1993/97) — never introduce root siblings simultaneously
- **85% accuracy target** maximizes both learning rate and motivation (Wilson 2019)
- **Failed retrieval enhances learning** (Kornell 2009) — "no_idea" ratings have genuine value
- **Self-assessment unreliable** — word-tapping is critical corrective signal
- **N-of-1 feasible**: 80-100 words/condition, Bayesian Beta-Binomial, 3-4 weeks to detect 10pp differences

Full compilation: `research/deep-research-compilation-2026-02-12.md`
Original plan: `research/learning-algorithm-redesign-2026-02-12.md`

---

## 2026-02-12 — Learning Algorithm Redesign: Implementation (Phases 1-4)

### What Was Implemented

Full backend implementation of the 4-phase learning algorithm redesign.

#### Phase 1: Emergency Data Fix + OCR Reform
- **reset_ocr_cards.py**: Script to reset inflated FSRS cards from textbook_scan imports. 0 real reviews → reset to "encountered"; 1-2 with <50% accuracy → reset; 3+ → replay through FSRS. Supports --dry-run.
- **OCR import reform**: Removed all three `submit_review(rating_int=3)` calls from `ocr_service.py`. Textbook scans now create ULK with `knowledge_state="encountered"` and no FSRS card.
- **Story completion reform**: `complete_story()` creates "encountered" ULK for unknown words. Only submits real FSRS review for words with active cards.
- **Encountered as Learn candidates**: `select_next_words()` includes encountered words with `encountered_bonus=0.5`.

#### Phase 2: Acquisition System
- **Schema migration**: New columns on ULK (acquisition_box, acquisition_next_due, acquisition_started_at, graduated_at, leech_suspended_at), Lemma (thematic_domain, etymology_json), ReviewLog (is_acquisition). Index on (acquisition_box, acquisition_next_due).
- **acquisition_service.py**: Leitner 3-box (4h→1d→3d). `start_acquisition()`, `submit_acquisition_review()` (box advance/reset/graduation), `_graduate()` (creates FSRS card with initial Good review).
- **introduce_word() reform**: Now calls `start_acquisition()` instead of creating FSRS card directly. Handles encountered→acquiring transition.
- **sentence_review_service.py**: Routes acquiring words to `submit_acquisition_review()`. Skips encountered words. Post-review leech check.
- **sentence_selector.py**: Includes acquisition-due words with pseudo-stability (box 1→0.1, box 2→0.5, box 3→2.0). Focus cohort filtering. Within-session repetition for acquisition words.
- **Wrap-up quiz**: `POST /api/review/wrap-up` returns word-level recall cards for acquiring words.
- **Next-session recap**: `POST /api/review/recap` returns sentence-level cards for last session's acquiring words.
- **Thematic domains**: `backfill_themes.py` tags lemmas with 20 thematic categories via LLM.
- **Etymology enrichment**: `backfill_etymology.py` generates structured etymology (root meaning, pattern, derivation, loanwords, cultural notes) via LLM. Tested: output quality is excellent (verified with 8 representative words).

#### Phase 3: Focus Cohort
- **cohort_service.py**: MAX_COHORT_SIZE=40. Acquiring words always included, remaining filled by lowest-stability FSRS due words.
- Integrated into `build_session()` — due_lemma_ids filtered through cohort.

#### Phase 4: Leech Auto-Management
- **leech_service.py**: Detection (times_seen≥8, accuracy<40%), batch suspend, 14-day reintroduction to acquisition box 1, post-review single-word check.
- Root-sibling interference guard in `word_selector.py`.

### Test Coverage
- 620 backend tests (up from 564), all passing
- New test files: test_acquisition.py (24 tests), test_cohort.py, test_leech_service.py (22 tests)
- Updated: test_ocr.py, test_word_selector.py (assertions updated for new behavior)

### Hypotheses

- **H13** (confirmed by design): OCR imports no longer inflate FSRS stability — `reset_ocr_cards.py` ready to fix existing data
- **H14**: Leitner 3-box acquisition (5+ reviews, 60%+ accuracy before FSRS) will produce higher day-7 retention than direct FSRS scheduling for new words
- **H15**: Focus cohort of 40 words will produce ~2.5 reviews/word/day instead of once/6-days
- **H16**: Within-session repetition will improve next-day recall by >20%
- **H17**: Wrap-up quiz (immediate word-level recall) + next-session recap (delayed sentence-level recall) will strengthen acquisition encoding
- **H18**: Etymology display (root meaning + pattern formula + loanwords) provides memory hooks that reduce time-to-graduation

### Not Yet Implemented
- Frontend changes (acquiring state display, wrap-up UI, recap UI, cohort indicator, etymology display)
- Thematic sentence generation (grouping target words by theme for batch generation)
- py-fsrs v6 upgrade (same-day review support)
- A/B testing framework

### How to Verify (after deploy)
1. Run `reset_ocr_cards.py --dry-run` to preview OCR data fix
2. Run `reset_ocr_cards.py` to apply fix, check session accuracy improvement
3. Introduce a word via Learn mode → verify it enters acquisition box 1 (not FSRS)
4. Review acquiring word → verify box advancement (rating≥3) or reset (rating=1)
5. Check focus cohort: `GET /api/review/next-sentences` should only return words in cohort
6. Monitor genuinely known words growing week over week (north star metric)
