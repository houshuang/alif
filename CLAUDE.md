# Alif — Arabic Reading & Listening Trainer

## Project Overview
A personal Arabic (MSA/fusha) learning app focused exclusively on reading and listening comprehension. No production/writing exercises. Tracks word knowledge at root, lemma, and conjugation levels using FSRS spaced repetition. Combines LLM sentence generation with deterministic rule-based validation (clitic stripping + known-form matching).

## Quick Start
```bash
# Backend
cd backend
cp .env.example .env  # add API keys
pip install -e ".[dev]"
python scripts/import_duolingo.py  # import 196 words
uvicorn app.main:app --port 8000

# Frontend
cd frontend
npm install
npx expo start --web  # opens on localhost:8081
```

## Architecture
- **Backend**: Python 3.11+ / FastAPI / SQLite (single user, no auth) — `backend/`
- **Frontend**: Expo (React Native) with web + iOS mode — `frontend/`
- **SRS**: py-fsrs (FSRS algorithm) — `backend/app/services/fsrs_service.py`
- **LLM**: LiteLLM for unified multi-model. Sentence generation: GPT-5.2 (best Arabic quality). General tasks: Gemini 3 Flash primary → GPT-5.2 fallback → Claude Haiku tertiary. Keys: GEMINI_KEY, OPENAI_KEY, ANTHROPIC_API_KEY in `.env`
- **TTS**: ElevenLabs REST API (not SDK). Model: `eleven_multilingual_v2`. Voice: Chaouki (MSA male). Learner pauses via Arabic comma insertion every 2 words. Key: ELEVENLABS_API_KEY in `.env`. Frontend uses expo-av for playback. Audio cached by SHA256(text|voice_id) in `backend/data/audio/`.
- **NLP**: Rule-based clitic stripping + known-form matching in sentence_validator.py. CAMeL Tools integrated in morphology.py for lemmatization, root extraction, and variant detection (graceful fallback to stub if not installed).
- **Migrations**: Alembic for SQLite schema migrations. Every schema change must have a migration file. Migrations run automatically on startup.
- **Hosting**: Hetzner (46.225.75.29), direct docker-compose (no Coolify). Backend on port 3000 → container 8000. Frontend as systemd service (`alif-expo`) on port 8081. DuckDNS: `alifstian.duckdns.org`.
- **Transliteration**: ALA-LC standard (kitāb, madrasa) with macrons for long vowels
- **Diacritics**: Always show on all Arabic text
- **CORS**: Wide open (`*`) — single-user app, no auth
- **Offline**: Frontend queues reviews in AsyncStorage sync queue, bulk-syncs via POST /api/review/sync when online. Sessions cached per mode. Story word lookups persisted in AsyncStorage.

## Review Modes

### Sentence-First Review (primary mode)
Reviews are sentence-centric: greedy set cover selects sentences that maximize due-word coverage per session.
1. `GET /api/review/next-sentences` assembles session via 6-stage pipeline (fetch due → candidate sentences → comprehension-aware recency filter → greedy set cover → easy/hard ordering → fallback word-only cards)
2. **Every word in a reviewed sentence gets a full FSRS card** — including previously unseen words. No more encounter-only tracking. Words without existing FSRS cards get auto-created knowledge records.
3. Ternary ratings: understood (rating=3 for all) / partial (tap to cycle: confused=rating 2 Hard, missed=rating 1 Again, rest=3) / no_idea (rating=1 for all)
4. **All words reviewed equally**: every word in the sentence gets an FSRS review based on the user's marking. The scheduling reason for selecting the sentence is irrelevant — unmarked words get rating=3, just like completing a story or scanning a textbook page. The `credit_type` field (primary/collateral) in review_log is purely metadata tracking which word triggered sentence selection; it does NOT affect ratings.
5. Falls back to word-only cards when no sentences available for uncovered due words
6. **Comprehension-aware recency**: sentences repeat based on last comprehension — understood: 7 day cooldown, partial: 2 day cooldown, no_idea: 4 hour cooldown
7. Inline intro candidates: up to 2 new words suggested at positions 4 and 8 in reading sessions (gated by 75% accuracy over last 20 reviews). **Not auto-introduced** — candidates are returned to the frontend for user to accept via Learn mode. No intro candidates in listening mode.

### Reading Mode (implemented)
1. User sees Arabic sentence (diacritized, large RTL text)
2. **Front phase**: user can tap non-function words to look them up (calls GET /api/review/word-lookup/{lemma_id}). Tapped words auto-marked as missed.
3. **Lookup panel**: Shows root, root meaning. If root has 2+ known siblings → prediction mode ("You know words from this root: X, Y. Can you guess the meaning?") before revealing English. Otherwise shows meaning immediately.
4. Taps "Show Answer" to reveal: English translation, transliteration, root info for missed words
5. **Back phase**: triple-tap words to cycle state: off → missed (red, rating 1 Again) → confused (yellow, rating 2 Hard) → off. Builds missed_lemma_ids + confused_lemma_ids
6. Rates: Got it (understood) / Continue (partial, if words marked) / I have no idea (no_idea)
7. **Back/Undo**: after submitting, can go back to previous card — undoes the review (restores pre-review FSRS state via backend undo endpoint), removes from sync queue if not yet flushed, restores word markings

### Listening Mode (implemented, real TTS via expo-av)
1. Audio plays via ElevenLabs TTS (speed 0.7x, multilingual_v2 model)
2. Tap to reveal Arabic text — tap words you didn't catch
3. Tap to reveal English translation + transliteration
4. Rate comprehension
5. Listening-ready filter: non-due words must have times_seen ≥ 3 AND FSRS stability ≥ 7 days

### Learn Mode (implemented)
1. **Pick phase**: Shows 5 candidate words one at a time — Arabic, English, transliteration, POS, verb/noun/adj forms table, example sentence, root + sibling count, TTS play button
2. Actions per word: Learn (introduces, creates FSRS card), Skip, Never show (suspend)
3. Selection algorithm: 40% frequency + 30% root familiarity (peaks at 30-60% of root known) + 20% recency bonus (sibling introduced 1-3 days ago) + 10% grammar pattern coverage
4. **Quiz phase**: After introducing words, polls for generated sentences (20s timeout). Sentence quiz or word-only fallback. Got it → rating 3, Missed → rating 1.
5. **Done phase**: Shows count introduced, quiz accuracy, CEFR level

### Story Mode (implemented)
1. **Generate**: LLM generates micro-fiction (2-12 sentences) using known vocabulary, random genre
2. **Import**: Paste any Arabic text → morphological analysis + LLM batch translation creates Lemma entries for unknown words (`source="story_import"`, `source_story_id` set). Proper nouns (personal/place names) detected by LLM are marked as function words with `name_type` instead of creating Lemma entries. Unknown vocab words become Learn mode candidates with `story_bonus` priority. No ULK created — Learn mode handles introduction.
3. **Reader**: Word-by-word Arabic with tap-to-lookup (shows gloss, transliteration, root, POS). Arabic/English tab toggle. Actions at end of scroll (not fixed bottom bar).
4. **Completion flow**: Complete (FSRS credit for ALL words including previously-unseen: rating=3 for un-looked-up, rating=1 for looked-up — `submit_review` auto-creates ULK), Skip (only rates looked-up words), Too Difficult (same as skip)
5. **List view**: Cards with readiness indicators (green ≤3 unknown, orange, red), generate + import modals

## Critical Rules for All Agents

### 1. IDEAS.md — Always Update
The file `IDEAS.md` in this project root is the master record of ALL project ideas. **Every agent must**:
- Read IDEAS.md at the start of work
- Add any new ideas, insights, or possibilities discovered during research or development
- Add ideas mentioned in conversation even if they won't be implemented now
- Never remove ideas — mark them as "deferred" or "rejected" with reasoning if needed
- Keep the file organized by category

### 2. Interaction Logging — Log Everything
Every user interaction with the learning app must be logged in a structured format:
- Word reviews (which word, rating given, time taken, context shown)
- Sentence comprehension attempts
- Words marked known/unknown
- Text imports and analysis results
- Session start/end times
- Any UI interaction that reveals learning behavior

Store logs in append-only JSONL files (`data/logs/interactions_YYYY-MM-DD.jsonl`). Schema:
```json
{"ts": "ISO8601", "event": "review", "lemma_id": 42, "rating": 3, "response_ms": 2100, "context": "sentence_id:17", "session_id": "abc123"}
```
This data is essential for algorithm analysis and optimization. Never skip logging.

### 3. Testability — Claude Must Be Able to Test Everything
Everything built must be trivially testable by Claude Code:

**Backend/Algorithms:**
- All NLP and algorithm logic must be in the API, never in the UI
- Every service must have pytest tests
- Every API endpoint must be testable with `curl`
- Include a `scripts/` directory with standalone test scripts for manual validation
- The import pipeline, morphology analysis, FSRS scheduling, and sentence validation must all be independently testable

**Frontend/UI:**
- Web preview via `npx expo start --web` or Vite dev server — must work in browser
- UI components should be viewable in isolation where possible
- API client should have a mock mode for offline testing
- Include screenshot-friendly test states (e.g., `/test/review-card` route showing a card in each state)

**Integration:**
- `scripts/smoke_test.sh` — starts backend, hits key endpoints, verifies responses
- `scripts/seed_test_data.py` — populates DB with known test words for reproducible testing

### 4. Skills — Generate and Update
As we build features, create reusable Claude Code skills (`.claude/skills/`) for common operations:
- Testing the backend API
- Running the full test suite
- Importing word lists
- Analyzing a text for difficulty
- Checking NLP pipeline accuracy
- Deploying to production
- Any repetitive multi-step workflow

### 6. Experiment Tracking — Document Everything
This app is an ongoing learning experiment. Every algorithm change, data structure modification, or analysis must be documented:
- **`research/experiment-log.md`**: Running log of all changes with date, hypothesis, expected effect, and verification plan
- **`research/analysis-YYYY-MM-DD.md`**: Detailed analysis reports linked from the experiment log
- When making algorithm changes, ALWAYS add an entry to experiment-log.md BEFORE implementing
- When running production data analysis, ALWAYS save findings to a dated analysis file
- Never delete entries — mark them as superseded if outdated

### 7. Code Style
- Python: Use type hints, pydantic models for API schemas
- TypeScript: Strict mode, functional components
- No unnecessary comments — only when logic isn't self-evident
- No test plans or checklists in PR descriptions
- Branch prefix: `sh/` for all GitHub branches

## Key Files

### Backend Services
- `backend/app/services/fsrs_service.py` — FSRS spaced repetition. Auto-creates UserLemmaKnowledge + Card() for unknown lemmas. submit_review handles missing records gracefully (no ValueError). Snapshots pre-review state (card, times_seen, times_correct, knowledge_state) in fsrs_log_json for undo support.
- `backend/app/services/sentence_selector.py` — Session assembly: greedy set cover, comprehension-aware recency (7d/2d/4h), difficulty matching, easy-bookend ordering. Returns root data per word. Includes intro candidate selection.
- `backend/app/services/sentence_review_service.py` — Reviews ALL words in a sentence equally via FSRS. Ternary ratings: missed=1, confused=2, rest=3. The `credit_type` field (primary/collateral) is metadata only — all words receive identical FSRS treatment based on user marking. Stores last_comprehension on sentence. Tracks total_encounters + variant_stats_json per surface form. `undo_sentence_review()` restores pre-review FSRS state from fsrs_log_json snapshots, deletes ReviewLog + SentenceReviewLog entries, resets sentence metadata.
- `backend/app/services/word_selector.py` — Next-word algorithm: 40% frequency + 30% root familiarity + 20% recency bonus + 10% grammar pattern. Excludes wiktionary reference entries and variant lemmas (canonical_lemma_id set). Root family query also filters variants. `introduce_word()` accepts `source` param (study/auto_intro/collocate).
- `backend/app/services/sentence_generator.py` — LLM generation with 3-attempt retry loop. Samples up to 50 known words for prompt with diversity weighting. Full diacritics required. Feeds validation failures back to LLM as retry feedback.
- `backend/app/services/sentence_validator.py` — Rule-based: tokenize → strip diacritics → strip clitics (proclitics + enclitics + taa marbuta) → match against known bare forms. 60+ function words hardcoded. Public API: `lookup_lemma()` (clitic-aware), `resolve_existing_lemma()` (for import dedup), `build_lemma_lookup()` (indexes forms_json + al-variants).
- `backend/app/services/grammar_service.py` — 24 features, 5 tiers (cascading comfort thresholds: 10 words → 30% → 40% → 50%). Comfort score: 60% log-exposure + 40% accuracy, decayed by recency.
- `backend/app/services/grammar_tagger.py` — LLM-based grammar feature tagging for sentences and lemmas.
- `backend/app/services/story_service.py` — Generate (LLM micro-fiction, random genre, up to 80 known words in prompt), import, complete/skip/too_difficult (FSRS credit), lookup, readiness recalculation.
- `backend/app/services/listening.py` — Listening confidence: min(per-word) * 0.6 + avg * 0.4. Requires times_seen ≥ 3, stability ≥ 7d.
- `backend/app/services/tts.py` — ElevenLabs REST, eleven_multilingual_v2, Chaouki voice, speed 0.7. Learner pauses: inserts Arabic commas every 2 words. SHA256 cache in data/audio/.
- `backend/app/services/llm.py` — LiteLLM: sentence generation uses GPT-5.2 (model_override="openai") for best Arabic quality. General fallback chain: gemini/gemini-3-flash-preview → gpt-5.2 → claude-haiku-4-5. JSON mode, markdown fence stripping, model_override support. Batch generation supports `rejected_words` param to steer LLM away from unknown vocabulary.
- `backend/app/services/morphology.py` — CAMeL Tools morphological analyzer. Functions: `analyze_word_camel()` (all analyses), `get_base_lemma()` (top lex), `get_best_lemma_mle()` (MLE disambiguator, probability-weighted), `is_variant_form()` (possessive/enclitic check, hamza-aware), `find_matching_analysis()` (disambiguate against known lemma, hamza-aware), `find_best_db_match()` (iterate all analyses, return first matching a known DB bare form, hamza-aware), `get_word_features()` (lex/root/pos/enc0/num/gen/stt). Falls back to stub if camel_tools not installed.
- `backend/app/services/variant_detection.py` — Two-phase variant detection: `detect_variants_llm()` (CAMeL candidates → Gemini Flash confirmation, with VariantDecision cache), `detect_definite_variants()` (al-prefix, hamza-normalized, 100% precision), `mark_variants()`. LLM confirmation eliminated the 34% true positive rate problem. Used by ALL import paths (Duolingo, Wiktionary, AVP, OCR) as post-import pass. Graceful fallback if LLM unavailable.
- `backend/app/services/interaction_logger.py` — Append-only JSONL to `data/logs/interactions_YYYY-MM-DD.jsonl`. Skipped when TESTING env var is set.
- `backend/app/services/ocr_service.py` — Gemini Vision OCR: `extract_text_from_image()` (full text extraction for story import), `extract_words_from_image()` (3-step pipeline: OCR → CAMeL morphology → LLM translation, returns base_lemma from morphological analysis), `process_textbook_page()` (background task: OCR + match/import words + post-import LLM-confirmed variant detection). Uses base_lemma from Step 2 for DB lookup (falls back to bare form). Runs detect_variants_llm + detect_definite_variants after import.
- `backend/app/services/flag_evaluator.py` — Background LLM evaluation for flagged content. Uses GPT-5.2 (model_override="openai") to evaluate word glosses, sentence Arabic/English/transliteration. Auto-fixes high-confidence corrections, retires unfixable sentences. Writes to ActivityLog.
- `backend/app/services/activity_log.py` — Shared helper `log_activity(db, event_type, summary, detail, commit)` for writing ActivityLog entries from any service or script.
- `backend/app/services/grammar_lesson_service.py` — LLM-generated grammar lessons for specific features. Caches lessons in DB.
- `backend/app/services/material_generator.py` — Orchestrates sentence + audio generation for a word. Used by Learn mode (post-introduce) and OCR import (background).

### Backend Other
- `backend/app/models.py` — SQLAlchemy models: Root, Lemma (+ example_ar/en, forms_json, canonical_lemma_id), UserLemmaKnowledge (+ variant_stats_json), ReviewLog, Sentence (+ last_comprehension), SentenceWord, SentenceReviewLog, GrammarFeature, SentenceGrammarFeature, UserGrammarExposure, Story, StoryWord, PageUpload (batch_id, status, extracted_words_json, new_words, existing_words)
- `backend/app/schemas.py` — Pydantic models. SentenceReviewSubmitIn includes confused_lemma_ids. SentenceWordMeta includes root/root_meaning/root_id.
- `backend/scripts/` — import_duolingo.py, import_wiktionary.py, import_avp_a1.py (all use clitic-aware dedup via `resolve_existing_lemma()`), benchmark_llm.py, pregenerate_material.py, generate_audio.py, generate_sentences.py, backfill_lemma_grammar.py, backfill_examples.py, backfill_forms.py, backfill_forms_llm.py, backfill_frequency.py (CAMeL MSA frequency + Kelly CEFR), backfill_roots.py, backfill_root_meanings.py, simulate_usage.py, update_material.py (cron: backfill sentences + audio by FSRS due-date priority, pipeline capped at 200 active sentences, MIN_SENTENCES=2), merge_al_lemmas.py, merge_lemma_variants.py, cleanup_lemma_variants.py, cleanup_glosses.py, cleanup_lemma_text.py, identify_leeches.py (find high-review low-accuracy words, optional --suspend), retire_sentences.py (remove low-quality/overused sentences), verify_sentences.py (GPT-5.2 batch verification of Arabic naturalness, parallel execution), normalize_and_dedup.py (3-pass production cleanup: LLM-confirmed variant detection + al-prefix dedup + forms_json enrichment), log_activity.py (CLI tool for manual ActivityLog entries), backfill_story_words.py (resolve null lemma_ids via morphology + LLM import), backfill_story_proper_nouns.py (convert proper nouns to function words), db_analysis.py, analyze_word_distribution.py, tts_comparison.py, test_llm_variants.py (benchmark LLM variant detection against ground truth)

### Frontend
- `frontend/app/index.tsx` — Review screen: sentence-first + word-only fallback, reading + listening modes, front-phase word lookup with root prediction, triple-tap word marking (off → missed → confused → off), back/undo (restores previous card + undoes FSRS review), inline intro cards, session completion with analytics
- `frontend/app/learn.tsx` — Learn: 5-candidate pick → quiz → done. Forms display, TTS, sentence polling.
- `frontend/app/words.tsx` — Word browser: two-column grid with category tabs (Vocabulary/Function/Names), smart filter chips (Leeches/Struggling/Recent/Solid/Next Up + state filters), review sparklines, search (AR/EN/translit). Next Up tab shows learn algorithm candidates with score breakdown.
- `frontend/app/stats.tsx` — Analytics: today banner, CEFR card, pace grid, quick stats, 14-day bar chart
- `frontend/app/story/[id].tsx` — Story reader: word-by-word Arabic with tap-to-lookup (AsyncStorage persisted), AR/EN tabs, complete/skip/too-difficult
- `frontend/app/stories.tsx` — Story list: generate modal (length + topic), import modal (title + text + image OCR)
- `frontend/app/scanner.tsx` — Textbook page scanner: camera/gallery upload, batch processing with polling, upload history with expandable results (new/existing words)
- `frontend/app/more.tsx` — Consolidated "More" tab: navigation to Scanner, Chats, Stats + inline Activity Log section
- `frontend/app/word/[id].tsx` — Word detail screen: forms table, grammar features, root family, review history chart, sentence stats
- `frontend/app/chats.tsx` — Chat/conversation list and viewer
- `frontend/app/listening.tsx` — Dedicated listening mode screen
- `frontend/lib/review/ActionMenu.tsx` — Generic "⋯" action menu. Bottom sheet with: Ask AI, Suspend word, Flag content. Used across all card screens.
- `frontend/lib/review/WordInfoCard.tsx` — Detailed word info panel for review screens (forms, root family, grammar)
- `frontend/lib/AskAI.tsx` — AI chat component for asking questions about Arabic content
- `frontend/lib/api.ts` — API client with configurable BASE_URL. Typed response interfaces. Includes lookupReviewWord(), undoSentenceReview() (queue removal + unmark + backend undo).
- `frontend/lib/types.ts` — All interfaces. SentenceWordMeta has root/root_meaning/root_id. WordLookupResult for review lookup.
- `frontend/lib/offline-store.ts` — AsyncStorage: session cache (3 per mode), reviewed tracking (+ unmarkReviewed for undo), data cache (words/stats/analytics)
- `frontend/lib/sync-queue.ts` — Enqueue reviews offline, bulk flush via POST /api/review/sync, invalidates session cache on success. removeFromQueue() for undo support.
- `frontend/lib/theme.ts` — Dark theme (0f0f1a bg), semantic colors (including confused yellow #f39c12), Arabic/English font sizes
- `frontend/lib/__tests__/` — Jest tests: sync-queue (enqueue/remove/pending), offline-store (mark/unmark/cache/invalidate), smart-filters (leech/struggling/recent/solid logic), api (sentence review, undo, word lookup, stories, learn mode, flagging, offline fallback)

## API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/words?limit=50&status=learning&category=function` | List words with knowledge state. category: function\|names. Returns last_ratings (last 8 review ratings for sparkline) and knowledge_score. |
| GET | `/api/words/{id}` | Word detail with review stats + root family + review history |
| GET | `/api/review/next?limit=10` | Due review cards (legacy word-only) |
| GET | `/api/review/next-listening` | Listening-suitable review cards (legacy) |
| GET | `/api/review/next-sentences?limit=10&mode=reading` | Sentence-centric review session (primary) |
| POST | `/api/review/submit` | Submit single-word review (legacy) |
| POST | `/api/review/submit-sentence` | Submit sentence review — all words get FSRS credit. Accepts confused_lemma_ids |
| POST | `/api/review/undo-sentence` | Undo a sentence review — restores pre-review FSRS state, deletes logs |
| GET | `/api/review/word-lookup/{lemma_id}` | Word detail + root family for review lookup |
| POST | `/api/review/sync` | Bulk sync offline reviews |
| GET | `/api/learn/next-words?count=5` | Best next words to introduce |
| POST | `/api/learn/introduce` | Introduce word (create FSRS card + trigger sentence generation) |
| POST | `/api/learn/introduce-batch` | Batch introduce |
| GET | `/api/learn/root-family/{root_id}` | Words from a root with knowledge state |
| POST | `/api/learn/quiz-result` | Submit learn-mode quiz result |
| POST | `/api/learn/suspend` | Suspend a word (never show again) |
| GET | `/api/learn/sentences/{lemma_id}` | Poll for generated sentence (ready/not ready) |
| GET | `/api/learn/sentence-params/{lemma_id}` | Max words + difficulty hint for sentence generation |
| GET | `/api/grammar/features` | All 24 grammar features with categories |
| GET | `/api/grammar/progress` | User's grammar exposure/comfort per feature |
| GET | `/api/grammar/unlocked` | Current tier and unlocked grammar features |
| GET | `/api/stats` | Basic stats (total, known, learning, due) |
| GET | `/api/stats/analytics` | Full analytics (pace, CEFR estimate, daily history) |
| GET | `/api/stats/cefr` | CEFR reading level estimate |
| POST | `/api/import/duolingo` | Run Duolingo import |
| POST | `/api/sentences/generate` | Generate sentence for a target word |
| POST | `/api/sentences/validate` | Validate sentence against known vocabulary |
| GET | `/api/stories` | List all stories |
| GET | `/api/stories/{id}` | Story detail with words |
| POST | `/api/stories/generate` | Generate story (LLM) |
| POST | `/api/stories/import` | Import Arabic text as story |
| POST | `/api/stories/{id}/complete` | Complete story (FSRS credit for all words) |
| POST | `/api/stories/{id}/skip` | Skip story |
| POST | `/api/stories/{id}/too-difficult` | Mark story too difficult |
| POST | `/api/stories/{id}/lookup` | Look up word in story |
| GET | `/api/stories/{id}/readiness` | Recalculate readiness |
| POST | `/api/analyze/word` | Analyze word morphology (CAMeL Tools or stub fallback) |
| POST | `/api/analyze/sentence` | Analyze sentence morphology (CAMeL Tools or stub fallback) |
| GET | `/api/tts/speak/{text}` | Generate TTS audio (async) |
| GET | `/api/tts/voices` | List available TTS voices |
| POST | `/api/tts/generate` | Generate TTS audio (async) |
| POST | `/api/tts/generate-for-sentence` | Generate sentence TTS with slow mode |
| GET | `/api/tts/audio/{cache_key}.mp3` | Serve cached audio file |
| POST | `/api/ocr/scan-pages` | Upload textbook page images for OCR word extraction (multipart, background processing) |
| GET | `/api/ocr/batch/{batch_id}` | Get batch upload status with per-page results |
| GET | `/api/ocr/uploads` | List recent upload batches with results |
| POST | `/api/ocr/extract-text` | Extract Arabic text from image for story import (synchronous) |
| POST | `/api/words/{lemma_id}/suspend` | Suspend a word (stops appearing in reviews) |
| POST | `/api/words/{lemma_id}/unsuspend` | Reactivate a suspended word with fresh FSRS card |
| POST | `/api/flags` | Flag content for LLM re-evaluation (word_gloss, sentence_arabic/english/transliteration) |
| GET | `/api/flags` | List content flags (optional ?status= filter) |
| GET | `/api/activity` | Recent activity log entries (flag resolutions, job runs) |
| POST | `/api/chat/ask` | Ask AI a question (with learning context). Creates conversation. |
| GET | `/api/chat/conversations` | List conversation summaries |
| GET | `/api/chat/conversations/{id}` | Full conversation messages |
| GET | `/api/grammar/lesson/{key}` | Get grammar lesson content for a feature |
| POST | `/api/grammar/introduce` | Introduce a grammar feature |
| GET | `/api/grammar/confused` | List grammar features causing confusion |
| GET | `/api/stats/deep-analytics` | Deep analytics: difficulty tiers, grammar progress, learning velocity |
| POST | `/api/review/reintro-result` | Submit re-introduction quiz result |

## Data Model
- `roots` — 3/4 consonant roots with core meaning, productivity score
- `lemmas` — Base dictionary forms with root FK, POS, gloss, frequency_rank (from CAMeL MSA corpus), cefr_level (A1–C2 from Kelly Project), grammar_features_json, forms_json, example_ar/en, transliteration, audio_url, canonical_lemma_id (FK to self — set when lemma is a variant of another), source_story_id (FK to stories — set when word was imported from a story)
- `user_lemma_knowledge` — FSRS card state per lemma (knowledge_state: new/learning/known/lapsed/suspended, fsrs_card_json, introduced_at, times_seen, times_correct, total_encounters, source: study/auto_intro/collocate/duolingo/encountered, variant_stats_json — per-variant-form seen/missed/confused counts)
- `review_log` — Full review history (rating 1-4, timing, mode, comprehension signal, sentence_id, credit_type: primary/collateral as metadata only — does not affect FSRS ratings, client_review_id for dedup, fsrs_log_json contains pre-review state snapshots for undo: pre_card, pre_times_seen, pre_times_correct, pre_knowledge_state)
- `sentences` — Generated/imported sentences with target word, last_shown_at, times_shown, **last_comprehension** (understood/partial/no_idea — drives recency filter)
- `sentence_words` — Word-level breakdown with position, surface_form, lemma_id, is_target_word, grammar_role_json
- `sentence_review_log` — Per-sentence review tracking (comprehension, timing, session, client_review_id)
- `grammar_features` — 24 grammar features across 5 categories (number, gender, verb_tense, verb_form, syntax)
- `sentence_grammar_features` — Sentence ↔ grammar feature junction table (is_primary, source)
- `user_grammar_exposure` — Per-feature tracking (times_seen, times_correct, comfort_score, first/last_seen_at)
- `stories` — Generated/imported stories (title_ar/en, body_ar/en, transliteration, status, readiness_pct, difficulty_level)
- `story_words` — Per-token breakdown (position, surface_form, lemma_id, sentence_index, gloss_en, is_known_at_creation, is_function_word, name_type: "personal"/"place"/null for proper nouns)
- `page_uploads` — Textbook page OCR tracking (batch_id, filename, status: pending/processing/completed/failed, extracted_words_json, new_words, existing_words, error_message)
- `content_flags` — Flagged content for LLM re-evaluation (content_type, lemma_id/sentence_id, status: pending/reviewing/fixed/dismissed, original_value, corrected_value, resolution_note)
- `activity_log` — System activity entries (event_type, summary, detail_json) — tracks flag resolutions, batch job results
- `variant_decisions` — LLM variant decision cache (word_bare, base_bare, is_variant, reason, decided_at) — prevents re-querying known pairs
- `chat_messages` — AI chat conversations (conversation_id, screen context, role: user/assistant, content, context_summary)

## NLP Pipeline
**Current (rule-based in sentence_validator.py):**
1. Whitespace tokenization + Arabic punctuation removal
2. Diacritic stripping + tatweel removal + alef normalization (أ إ آ ٱ → ا)
3. Clitic stripping: proclitics (و، ف، ب، ل، ك، وال، بال، فال، لل، كال) and enclitics (ه، ها، هم، هن، هما، كم، كن، ك، نا، ني)
4. Taa marbuta handling (ة → ت before suffixes)
5. Match against known bare forms set (with and without ال prefix variants)
6. 60+ hardcoded function words treated as always-known

**CAMeL Tools (integrated in morphology.py):**
1. Input word → `analyze_word_camel()` → list of morphological analyses
2. Each analysis dict: `lex` (base lemma), `root`, `pos`, `enc0` (pronominal enclitic), `num`, `gen`, `stt`
3. `get_base_lemma()` returns top analysis lex; `get_best_lemma_mle()` uses MLE disambiguator for probability-weighted analysis (reduces false positives)
4. `is_variant_form()` and `find_matching_analysis()` use hamza normalization (`normalize_alef`) at comparison time — hamza preserved in storage, normalized only for matching
5. `find_best_db_match()` iterates ALL analyses, matches against known DB lemma bare forms with hamza normalization
6. Graceful fallback: if `camel-tools` not installed, all functions return stub/empty data. MLE falls back to raw analyzer if model unavailable.
7. Requires `cmake` build dep + `camel_data -i light` download (~660MB) in Docker
8. **Variant cleanup**: `scripts/cleanup_lemma_variants.py` uses DB-aware CAMeL Tools disambiguation. `scripts/normalize_and_dedup.py` does 3-pass cleanup: variant detection + clitic-aware dedup + forms_json enrichment.

**Function Words:**
- Function words (pronouns, prepositions, conjunctions, demonstratives, copular verbs like كان/ليس) are:
  - **Tappable in sentence review**: show correct gloss, root, forms, with a "function word" badge
  - **NOT given FSRS cards**: no spaced repetition scheduling, no "due" state, no review cards
  - **Tracked in SentenceWord**: keep lemma_id for lookup purposes, but sentence_review_service skips them for credit
  - **Have Lemma entries in DB**: with proper glosses and forms, but no ULK (UserLemmaKnowledge) records
  - **Defined in FUNCTION_WORDS set** in sentence_validator.py (60+ entries, bare forms)
  - **FUNCTION_WORD_FORMS dict** maps conjugated forms to base lemma (كانت→كان, يكون→كان, etc.)
  - **Clitic stripping is NOT applied** to function words in map_tokens_to_lemmas() to prevent false analysis (e.g., كانت → ك+انت)

**Planned (future):**
1. MLE disambiguator for sentence-level analysis (currently single-word only)
2. Validate LLM grammar tags against morphological analysis

## LLM Benchmarking
```bash
cd backend && python scripts/benchmark_llm.py --task all
# Or specific: --task diacritization --models gemini,anthropic
```
Tests 3 models across 5 tasks (105 ground truth cases): diacritization, translation, transliteration, sentence generation, grammar tagging.

## Testing
```bash
cd backend && python -m pytest  # 564 tests
cd frontend && npm test         # 73 tests
```
Backend: all services have dedicated test files in `backend/tests/`.
Frontend: Jest + ts-jest in `frontend/lib/__tests__/`. Tests cover sync queue (enqueue/remove/pending), offline store (mark/unmark reviewed, session cache, invalidation), smart filter logic (leech/struggling/recent/solid detection), and API interactions (sentence review submit/undo, word lookup with caching, story operations, learn mode, content flagging, offline fallback). Mocks in `__mocks__/` for AsyncStorage, expo-constants, netinfo.

## Deployment
```bash
# Deploy backend + pull latest
ssh alif "cd /opt/alif && git pull && docker compose up -d --build"

# Expo dev server is a systemd service
ssh alif "systemctl restart alif-expo"

# Expo URL (always display after deploy):
# exp://alifstian.duckdns.org:8081
# Web: http://alifstian.duckdns.org:8081
```

## Current State
Sentence-centric architecture with:
- Sentence-first review with greedy set cover scheduling + comprehension-aware recency
- All words in reviewed sentences get full FSRS cards (no encounter-only tracking)
- Ternary word marking: off → missed (red, FSRS Again) → confused (yellow, FSRS Hard) → off
- Front-phase word lookup with root prediction (2+ known siblings triggers prediction mode)
- Root info displayed on sentence reveal for missed words
- CAMeL Tools morphology: lemmatization, root extraction, MLE disambiguator (fallback to stub)
- LLM-confirmed variant detection: CAMeL candidates → Gemini Flash confirmation with VariantDecision cache. Eliminates false positives from CAMeL-only approach.
- Lemma variant system: canonical_lemma_id marks variants, variant_stats_json tracks per-form accuracy, root family/learn mode filter variants out
- Sentence diversity: weighted sampling + avoid overused words in LLM generation + rejected word feedback
- Word introduction is user-driven (Learn mode only) — no auto-introduction during review sessions or sentence generation
- ULK provenance tracking: source field distinguishes study/auto_intro/collocate/duolingo/encountered
- Story mode (generate/import) with tap-to-lookup reading and FSRS completion credit
- Learn mode with 5-candidate pick → sentence quiz flow
- Grammar feature tracking with 5-tier progression (24 features) + grammar lessons
- LLM: GPT-5.2 for sentence generation (Arabic quality), Gemini 3 Flash for general tasks, Claude Haiku tertiary
- TTS: ElevenLabs eleven_multilingual_v2 with learner pauses
- Word frequency data (CAMeL MSA corpus, 12.6B tokens) + CEFR levels (Kelly Project, 9K lemmas) — displayed in Learn mode, word browser, word detail, review lookup
- Suspend/flag system: suspend words (never show), flag content for LLM re-evaluation (auto-fix or retire)
- ActionMenu ("⋯" button): Ask AI, Suspend word, Flag content — replaces old AskAI FAB
- Tab consolidation: 6 tabs (Review, Listen, Learn, Words, Stories, More)
- Activity log: tracks flag resolutions, batch job results, manual actions. Displayed in More tab.
- Chat/AskAI: contextual AI questions about Arabic content, conversation history
- Deep analytics: difficulty tiers, grammar progress, learning velocity
- Word detail screen: forms, grammar features, root family, review history, sentence stats
- Back/undo in review: go back to previous card, undo submitted review (restores FSRS state from pre-review snapshots)
- Sentence pipeline: due-date priority generation, capped at 200 active sentences, MIN_SENTENCES=2
- Word list: two-column grid, category tabs (Vocabulary/Function/Names), smart filters (Leeches/Struggling/Recent/Solid/Next Up), review sparklines
- Offline sync queue + session caching
- Deployed to Hetzner via direct docker-compose

Next: grammar-aware sentence selection, adaptive session pacing, CAMeL Tools sentence-level disambiguation
