# Alif — Arabic Reading & Listening Trainer

## Project Overview
A personal Arabic (MSA/fusha) learning app focused exclusively on reading and listening comprehension. No production/writing exercises. Tracks word knowledge at root, lemma, and conjugation levels using FSRS spaced repetition. Combines LLM sentence generation with deterministic rule-based validation (clitic stripping + known-form matching).

## Quick Start
```bash
# Backend
cd backend
cp .env.example .env  # add API keys
pip install -e ".[dev]"
python3 scripts/import_duolingo.py  # import 196 words
python3 -m uvicorn app.main:app --port 8000

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
See `docs/review-modes.md` for full UX flows.
- **Sentence-First Review**: greedy set cover scheduling, ternary ratings (understood/partial/no_idea), all words get equal FSRS credit. credit_type is metadata only.
- **Reading Mode**: front-phase word lookup with root prediction, triple-tap marking (off→missed→confused→off), back/undo
- **Listening Mode**: ElevenLabs TTS, reveal Arabic → reveal English, listening-ready filter (times_seen≥3, stability≥7d)
- **Learn Mode**: 5-candidate pick → sentence quiz → done. Selection: 40% freq + 30% root + 20% recency + 10% grammar
- **Story Mode**: generate/import, tap-to-lookup reader, complete/skip/too-difficult

## Design Principles
- **Word introduction is user-driven only** — Learn mode only. No auto-introduction during review sessions, sentence generation, or story completion. OCR/story import creates "encountered" state (no FSRS card), not introduced.
- **No concept of "due"** — the app picks the most relevant cards for the next session. Don't use "due" in UI text or stats. Use "ready for review" or similar.
- **No bare word cards in review** — review sessions ONLY show sentences. If a due word has no comprehensible sentence, generate one on-demand or skip the word. Never show a word-only fallback card.
- **Comprehensibility gate** — sentences must have ≥70% known content words (excluding function words) to be shown in review. Incomprehensible sentences are skipped.
- **On-demand sentence generation** — when a due word has no comprehensible sentence, generate 1-2 synchronously during session building (max 3/session). Uses current vocabulary, not stale pre-generated pool.
- **Tapped words are always marked missed** — front-phase tapping auto-marks as missed (rating≤2). Never give rating 3 to a word the user looked up.
- **al-prefix is NOT a separate lemma** — الكلب and كلب are the same lemma. All import paths must dedup al-prefix forms. Distinct lemmas only for genuinely different words (e.g. الآن "now" vs آن "time").
- **Be conservative with ElevenLabs TTS** — costs real money. Only generate audio for sentences that will actually be shown (due-date priority). Don't blanket-generate for all sentences.
- **Sentence pipeline cap**: MAX 200 active sentences, MIN_SENTENCES=2 per word. Generation prioritized by FSRS due date via `update_material.py`.
- **Canonical lemma is the unit of scheduling** — variant forms (possessives, conjugations, al-prefix) tracked for diagnostics via `variant_stats_json` but never get independent FSRS cards. Reviews of variant words redirect credit to canonical lemma.
- **All import paths must run variant detection** — Duolingo, Wiktionary, AVP, OCR, story import all run `detect_variants_llm()` + `detect_definite_variants()` + `mark_variants()` post-import.
- **All import paths must run quality gate** — `import_quality.filter_useful_lemmas()` filters out junk (transliterations, abbreviations, letter names) before importing. Integrated in OCR, story import, and Duolingo paths.

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
- Test key endpoints manually with `curl http://localhost:8000/api/stats`
- Use mock data in `frontend/lib/mock-data.ts` for reproducible frontend testing

### 4. Skills — Generate and Update
As we build features, create reusable Claude Code skills (`.claude/skills/`) for common operations:
- Testing the backend API
- Running the full test suite
- Importing word lists
- Analyzing a text for difficulty
- Checking NLP pipeline accuracy
- Deploying to production
- Any repetitive multi-step workflow

### 5. Experiment Tracking — Document Everything
This app is an ongoing learning experiment. Every algorithm change, data structure modification, or analysis must be documented:
- **`research/experiment-log.md`**: Running log of all changes with date, hypothesis, expected effect, and verification plan
- **`research/analysis-YYYY-MM-DD.md`**: Detailed analysis reports linked from the experiment log
- When making algorithm changes, ALWAYS add an entry to experiment-log.md BEFORE implementing
- When running production data analysis, ALWAYS save findings to a dated analysis file
- Never delete entries — mark them as superseded if outdated

### 6. Code Style
- Python: Use type hints, pydantic models for API schemas
- TypeScript: Strict mode, functional components
- No unnecessary comments — only when logic isn't self-evident
- No test plans or checklists in PR descriptions
- Branch prefix: `sh/` for all GitHub branches

## Key Files

### Backend Services
- `fsrs_service.py` — FSRS spaced repetition. Auto-creates ULK + Card for unknown lemmas. Snapshots pre-review state in fsrs_log_json for undo.
- `sentence_selector.py` — Session assembly: greedy set cover, comprehension-aware recency (7d/2d/4h), difficulty matching, easy-bookend ordering. Focus cohort filtering (MAX_COHORT_SIZE=40). Acquisition-due words with pseudo-stability mapping. Comprehensibility gate (≥70% known content words). On-demand sentence generation for uncovered words (MAX_ON_DEMAND=3). No word-only fallbacks. **Variant→canonical resolution**: sentences with variant forms correctly cover canonical due words.
- `sentence_review_service.py` — Reviews ALL words equally. Routes acquiring→acquisition, skips encountered. **Variant→canonical redirect**: reviews of variant words credit the canonical lemma, with surface forms tracked in variant_stats_json. credit_type is metadata only. Post-review leech check for words rated ≤2. Undo restores pre-review state from snapshots.
- `word_selector.py` — Next-word algorithm: 40% freq + 30% root + 20% recency + 10% grammar + encountered/story bonus. Root-sibling interference guard. Excludes wiktionary refs and variant lemmas. introduce_word() calls start_acquisition().
- `sentence_generator.py` — LLM generation with 3-attempt retry loop, diversity weighting, full diacritics. Feeds validation failures back as retry feedback.
- `sentence_validator.py` — Rule-based: tokenize → strip diacritics → strip clitics → match known forms. 60+ function words. Public API: lookup_lemma(), resolve_existing_lemma(), build_lemma_lookup().
- `grammar_service.py` — 24 features, 5 tiers. Comfort score: 60% log-exposure + 40% accuracy, decayed by recency.
- `grammar_tagger.py` — LLM-based grammar feature tagging.
- `story_service.py` — Generate/import stories. Completion creates "encountered" ULK (no FSRS card); only real FSRS review for words with active cards.
- `listening.py` — Listening confidence: min(per-word) * 0.6 + avg * 0.4. Requires times_seen ≥ 3, stability ≥ 7d.
- `tts.py` — ElevenLabs REST, eleven_multilingual_v2, Chaouki voice, speed 0.7. Learner pauses. SHA256 cache.
- `llm.py` — LiteLLM: GPT-5.2 for sentence gen, Gemini 3 Flash general, Claude Haiku tertiary. JSON mode, markdown fence stripping, model_override.
- `morphology.py` — CAMeL Tools analyzer. Hamza normalized at comparison time only (preserved in storage). Falls back to stub if not installed.
- `variant_detection.py` — Two-phase: CAMeL candidates → Gemini Flash LLM confirmation with VariantDecision cache. Used by ALL import paths. Graceful fallback if LLM unavailable.
- `interaction_logger.py` — Append-only JSONL. Skipped when TESTING env var set.
- `ocr_service.py` — Gemini Vision OCR: text extraction, word extraction (OCR→morphology→LLM translation), textbook page processing. Creates "encountered" ULK. Runs variant detection after import.
- `flag_evaluator.py` — Background LLM evaluation of flagged content. Auto-fixes or retires. Writes to ActivityLog.
- `activity_log.py` — Shared helper for writing ActivityLog entries.
- `grammar_lesson_service.py` — LLM-generated grammar lessons, cached in DB.
- `material_generator.py` — Orchestrates sentence + audio generation for a word.
- `acquisition_service.py` — Leitner 3-box (4h→1d→3d). Graduation: box 3 + rating≥3 + times_seen≥5 + accuracy≥60%.
- `cohort_service.py` — Focus cohort: MAX_COHORT_SIZE=40. Acquiring words always included, rest filled by lowest-stability due words.
- `leech_service.py` — Auto-manage failing words. Detection: times_seen≥8 AND accuracy<40%. 14-day reintro to acquisition box 1.
- `import_quality.py` — LLM batch filter for word imports. Rejects transliterations, abbreviations, letter names, partial words. Used by OCR, story import, and Duolingo paths.

All services in `backend/app/services/`.

### Backend Other
- `backend/app/models.py` — SQLAlchemy models (see Data Model below)
- `backend/app/schemas.py` — Pydantic request/response models
- `backend/scripts/` — Import, backfill, cleanup, analysis scripts. See `docs/scripts-catalog.md`. Most-used: update_material.py (cron), import_duolingo.py, retire_sentences.py, normalize_and_dedup.py, log_activity.py (CLI), reset_ocr_cards.py (OCR→encountered), backfill_etymology.py (LLM etymology), backfill_themes.py (thematic domains), cleanup_review_pool.py (reset under-learned→acquiring, suspend variant ULKs with stat merge, suspend junk, retire bad sentences, run variant detection on uncovered words)

### Frontend
- `app/index.tsx` — Review screen: sentence-only (no word-only fallback), reading + listening, word lookup, word marking, back/undo, wrap-up mini-quiz (acquiring + missed words), next-session recap, session word tracking, story source badges on intro cards
- `app/learn.tsx` — Learn mode: 5-candidate pick → quiz → done. Etymology display on pick cards. Story source badge for story words.
- `app/words.tsx` — Word browser: grid, category tabs (Vocab/Function/Names), smart filters (Leeches/Struggling/Recent/Solid/Next Up/Acquiring/Encountered), sparklines, search
- `app/stats.tsx` — Analytics dashboard with acquiring/encountered stat cards
- `app/story/[id].tsx` — Story reader with tap-to-lookup
- `app/stories.tsx` — Story list with generate + import
- `app/scanner.tsx` — Textbook page OCR scanner
- `app/more.tsx` — More tab: Scanner, Chats, Stats, Activity Log
- `app/word/[id].tsx` — Word detail: forms, grammar, root family, review history, sentence stats, etymology section, acquisition badge
- `app/chats.tsx` — AI chat conversations
- `app/listening.tsx` — Dedicated listening mode
- `lib/review/ActionMenu.tsx` — "⋯" menu: Ask AI, Suspend, Flag
- `lib/review/WordInfoCard.tsx` — Word info panel for review
- `lib/api.ts` — API client with typed interfaces for all endpoints
- `lib/types.ts` — TypeScript interfaces
- `lib/offline-store.ts` — AsyncStorage session cache + reviewed tracking
- `lib/sync-queue.ts` — Offline review queue, bulk sync
- `lib/theme.ts` — Dark theme, semantic colors
- `lib/net-status.ts` — Network status singleton + useNetStatus hook
- `lib/sync-events.ts` — Event emitter for sync notifications
- `lib/frequency.ts` — Frequency band + CEFR color utilities
- `lib/WordCardComponents.tsx` — Reusable word display (posLabel, FormsRow, GrammarRow, PlayButton)
- `lib/AskAI.tsx` — AI chat modal (used in ActionMenu)
- `lib/MarkdownMessage.tsx` — Markdown renderer for chat/AI responses
- `lib/mock-data.ts` — Mock words, stats, learn candidates for testing
- `lib/__tests__/` — Jest tests for sync, store, smart-filters, API, typechecks
- `app/review-lab.tsx` — Hidden route for testing review UI variants

All frontend in `frontend/`.

## Primary API Endpoints
Full list: `docs/api-reference.md` or `backend/app/routers/`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/review/next-sentences?limit=10&mode=reading` | Sentence-centric review session |
| POST | `/api/review/submit-sentence` | Submit sentence review (all words get FSRS credit) |
| POST | `/api/review/undo-sentence` | Undo sentence review |
| GET | `/api/review/word-lookup/{lemma_id}` | Word detail for review lookup |
| POST | `/api/review/sync` | Bulk sync offline reviews |
| POST | `/api/review/wrap-up` | Wrap-up mini-quiz for acquiring + missed words |
| POST | `/api/review/recap` | Next-session recap for acquisition words |
| GET | `/api/learn/next-words?count=5` | Best next words to introduce |
| POST | `/api/learn/introduce` | Introduce word (starts acquisition + triggers sentence gen) |
| GET | `/api/words?limit=50&status=learning` | List words with knowledge state |
| GET | `/api/stories` | List stories |
| POST | `/api/stories/import` | Import Arabic text as story |
| POST | `/api/stories/{id}/complete` | Complete story |
| GET | `/api/stats/analytics` | Full analytics |
| POST | `/api/ocr/scan-pages` | Upload textbook pages for OCR |

## Data Model
- `roots` — 3/4 consonant roots: core_meaning, productivity_score
- `lemmas` — Dictionary forms: root FK, pos, gloss, frequency_rank, cefr_level, grammar_features_json, forms_json, example_ar/en, transliteration, audio_url, canonical_lemma_id (variant FK), source_story_id, thematic_domain, etymology_json
- `user_lemma_knowledge` — Per-lemma SRS state: knowledge_state (encountered/acquiring/new/learning/known/lapsed/suspended), fsrs_card_json, times_seen, times_correct, total_encounters, source, variant_stats_json, acquisition_box (1/2/3), acquisition_next_due, graduated_at, leech_suspended_at
- `review_log` — Review history: rating 1-4, mode, sentence_id, credit_type (metadata only), is_acquisition, fsrs_log_json (pre-review snapshots for undo)
- `sentences` — Generated/imported: target_lemma_id, times_shown, last_reading_shown_at/last_listening_shown_at, last_reading_comprehension/last_listening_comprehension, is_active, max_word_count
- `sentence_words` — Word breakdown: position, surface_form, lemma_id, is_target_word, grammar_role_json
- `sentence_review_log` — Per-sentence review: comprehension, timing, session_id
- `grammar_features` — 24 features across 5 categories
- `sentence_grammar_features` — Sentence ↔ grammar junction
- `user_grammar_exposure` — Per-feature: times_seen, times_correct, comfort_score
- `stories` — title_ar/en, body_ar/en, transliteration, status, readiness_pct, difficulty_level
- `story_words` — Per-token: position, surface_form, lemma_id, gloss_en, is_function_word, name_type
- `page_uploads` — OCR tracking: batch_id, status, extracted_words_json, new_words, existing_words
- `content_flags` — Flagged content: content_type, status (pending/reviewing/fixed/dismissed)
- `activity_log` — System events: event_type, summary, detail_json
- `variant_decisions` — LLM variant cache: word_bare, base_bare, is_variant, reason
- `chat_messages` — AI conversations: conversation_id, role, content

## NLP Pipeline
See `docs/nlp-pipeline.md` for full details.
- **Rule-based** (sentence_validator.py): tokenize → strip diacritics → strip clitics → match known forms. 60+ function words hardcoded.
- **CAMeL Tools** (morphology.py): lemmatization, root extraction, MLE disambiguator. Hamza normalized at comparison only. Graceful stub fallback.
- **Function words**: tappable in review but NOT given FSRS cards. Clitic stripping NOT applied to function words.

## LLM Benchmarking
```bash
cd backend && python3 scripts/benchmark_llm.py --task all
# Or specific: --task diacritization --models gemini,anthropic
```

## Testing
```bash
cd backend && python3 -m pytest
cd frontend && npm test
```
Backend: all services have dedicated test files in `backend/tests/`.
Frontend: Jest + ts-jest in `frontend/lib/__tests__/`.

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

Next: py-fsrs v4→v6 upgrade, more story imports, listening mode improvements
