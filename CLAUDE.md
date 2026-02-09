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
- **LLM**: LiteLLM for unified multi-model (Gemini 3 Flash primary, GPT-5.2 fallback, Claude Haiku tertiary). Keys: GEMINI_KEY, OPENAI_KEY, ANTHROPIC_API_KEY in `.env`
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
4. Credit types: primary (target word) + collateral (all other words)
5. Falls back to word-only cards when no sentences available for uncovered due words
6. **Comprehension-aware recency**: sentences repeat based on last comprehension — understood: 7 day cooldown, partial: 2 day cooldown, no_idea: 4 hour cooldown
7. Inline intro candidates: up to 2 new words suggested at positions 4 and 8 in session (gated by 75% accuracy over last 20 reviews)

### Reading Mode (implemented)
1. User sees Arabic sentence (diacritized, large RTL text)
2. **Front phase**: user can tap non-function words to look them up (calls GET /api/review/word-lookup/{lemma_id}). Tapped words auto-marked as missed.
3. **Lookup panel**: Shows root, root meaning. If root has 2+ known siblings → prediction mode ("You know words from this root: X, Y. Can you guess the meaning?") before revealing English. Otherwise shows meaning immediately.
4. Taps "Show Answer" to reveal: English translation, transliteration, root info for missed words
5. **Back phase**: triple-tap words to cycle state: off → confused (yellow, rating 2 Hard) → missed (red, rating 1 Again) → off. Builds missed_lemma_ids + confused_lemma_ids
6. Rates: Got it (understood) / Continue (partial, if words marked) / I have no idea (no_idea)

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
2. **Import**: Paste any Arabic text, analyzes known/unknown, calculates readiness
3. **Reader**: Word-by-word Arabic with tap-to-lookup (shows gloss, transliteration, root, POS). Arabic/English tab toggle.
4. **Completion flow**: Complete (FSRS credit: rating=3 for un-looked-up words, rating=1 for looked-up), Skip (only rates looked-up words), Too Difficult (same as skip)
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

### 5. Code Style
- Python: Use type hints, pydantic models for API schemas
- TypeScript: Strict mode, functional components
- No unnecessary comments — only when logic isn't self-evident
- No test plans or checklists in PR descriptions
- Branch prefix: `sh/` for all GitHub branches

## Key Files

### Backend Services
- `backend/app/services/fsrs_service.py` — FSRS spaced repetition. Auto-creates UserLemmaKnowledge + Card() for unknown lemmas. submit_review handles missing records gracefully (no ValueError).
- `backend/app/services/sentence_selector.py` — Session assembly: greedy set cover, comprehension-aware recency (7d/2d/4h), difficulty matching, easy-bookend ordering. Returns root data per word. Includes intro candidate selection.
- `backend/app/services/sentence_review_service.py` — Distributes FSRS credit to ALL words in sentence. Ternary ratings: missed=1, confused=2, rest=3. Stores last_comprehension on sentence. Tracks total_encounters + variant_stats_json per surface form.
- `backend/app/services/word_selector.py` — Next-word algorithm: 40% frequency + 30% root familiarity + 20% recency bonus + 10% grammar pattern. Excludes wiktionary reference entries and variant lemmas (canonical_lemma_id set). Root family query also filters variants.
- `backend/app/services/sentence_generator.py` — LLM generation with 3-attempt retry loop. Samples up to 50 known words for prompt. Full diacritics required.
- `backend/app/services/sentence_validator.py` — Rule-based: tokenize → strip diacritics → strip clitics (proclitics + enclitics + taa marbuta) → match against known bare forms. 60+ function words hardcoded.
- `backend/app/services/grammar_service.py` — 24 features, 5 tiers (cascading comfort thresholds: 10 words → 30% → 40% → 50%). Comfort score: 60% log-exposure + 40% accuracy, decayed by recency.
- `backend/app/services/grammar_tagger.py` — LLM-based grammar feature tagging for sentences and lemmas.
- `backend/app/services/story_service.py` — Generate (LLM micro-fiction, random genre, up to 80 known words in prompt), import, complete/skip/too_difficult (FSRS credit), lookup, readiness recalculation.
- `backend/app/services/listening.py` — Listening confidence: min(per-word) * 0.6 + avg * 0.4. Requires times_seen ≥ 3, stability ≥ 7d.
- `backend/app/services/tts.py` — ElevenLabs REST, eleven_multilingual_v2, Chaouki voice, speed 0.7. Learner pauses: inserts Arabic commas every 2 words. SHA256 cache in data/audio/.
- `backend/app/services/llm.py` — LiteLLM: gemini/gemini-3-flash-preview → gpt-5.2 → claude-haiku-4-5. JSON mode, markdown fence stripping, model_override support.
- `backend/app/services/morphology.py` — CAMeL Tools morphological analyzer. Functions: `analyze_word_camel()` (all analyses), `get_base_lemma()` (top lex), `is_variant_form()` (possessive/enclitic check), `find_matching_analysis()` (disambiguate against known lemma), `get_word_features()` (lex/root/pos/enc0/num/gen/stt). Falls back to stub if camel_tools not installed.
- `backend/app/services/interaction_logger.py` — Append-only JSONL to `data/logs/interactions_YYYY-MM-DD.jsonl`.

### Backend Other
- `backend/app/models.py` — SQLAlchemy models: Root, Lemma (+ example_ar/en, forms_json, canonical_lemma_id), UserLemmaKnowledge (+ variant_stats_json), ReviewLog, Sentence (+ last_comprehension), SentenceWord, SentenceReviewLog, GrammarFeature, SentenceGrammarFeature, UserGrammarExposure, Story, StoryWord
- `backend/app/schemas.py` — Pydantic models. SentenceReviewSubmitIn includes confused_lemma_ids. SentenceWordMeta includes root/root_meaning/root_id.
- `backend/scripts/` — import_duolingo.py, import_wiktionary.py, import_avp_a1.py, benchmark_llm.py, pregenerate_material.py, generate_audio.py, generate_sentences.py, backfill_lemma_grammar.py, backfill_examples.py, backfill_forms.py, simulate_usage.py, update_material.py, merge_al_lemmas.py, merge_lemma_variants.py, cleanup_lemma_variants.py

### Frontend
- `frontend/app/index.tsx` — Review screen: sentence-first + word-only fallback, reading + listening modes, front-phase word lookup with root prediction, triple-tap word marking (off → confused → missed → off), inline intro cards, session completion with analytics
- `frontend/app/learn.tsx` — Learn: 5-candidate pick → quiz → done. Forms display, TTS, sentence polling.
- `frontend/app/words.tsx` — Word browser: search (AR/EN/translit), filter by state, sort by review status (failed first)
- `frontend/app/stats.tsx` — Analytics: today banner, CEFR card, pace grid, quick stats, 14-day bar chart
- `frontend/app/story/[id].tsx` — Story reader: word-by-word Arabic with tap-to-lookup (AsyncStorage persisted), AR/EN tabs, complete/skip/too-difficult
- `frontend/app/stories.tsx` — Story list: generate modal (length + topic), import modal (title + text)
- `frontend/lib/api.ts` — API client with configurable BASE_URL. Includes lookupReviewWord().
- `frontend/lib/types.ts` — All interfaces. SentenceWordMeta has root/root_meaning/root_id. WordLookupResult for review lookup.
- `frontend/lib/offline-store.ts` — AsyncStorage: session cache (3 per mode), reviewed tracking, data cache (words/stats/analytics)
- `frontend/lib/sync-queue.ts` — Enqueue reviews offline, bulk flush via POST /api/review/sync, invalidates session cache on success
- `frontend/lib/theme.ts` — Dark theme (0f0f1a bg), semantic colors (including confused yellow #f39c12), Arabic/English font sizes

## API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/words?limit=50&status=learning` | List words with knowledge state |
| GET | `/api/words/{id}` | Word detail with review stats + root family + review history |
| GET | `/api/review/next?limit=10` | Due review cards (legacy word-only) |
| GET | `/api/review/next-listening` | Listening-suitable review cards (legacy) |
| GET | `/api/review/next-sentences?limit=10&mode=reading` | Sentence-centric review session (primary) |
| POST | `/api/review/submit` | Submit single-word review (legacy) |
| POST | `/api/review/submit-sentence` | Submit sentence review — all words get FSRS credit. Accepts confused_lemma_ids |
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

## Data Model
- `roots` — 3/4 consonant roots with core meaning, productivity score
- `lemmas` — Base dictionary forms with root FK, POS, gloss, frequency rank, grammar_features_json, forms_json, example_ar/en, transliteration, audio_url, canonical_lemma_id (FK to self — set when lemma is a variant of another)
- `user_lemma_knowledge` — FSRS card state per lemma (knowledge_state: new/learning/known/lapsed, fsrs_card_json, introduced_at, times_seen, times_correct, total_encounters, source: study/import/encountered, variant_stats_json — per-variant-form seen/missed/confused counts)
- `review_log` — Full review history (rating 1-4, timing, mode, comprehension signal, sentence_id, credit_type: primary/collateral, client_review_id for dedup)
- `sentences` — Generated/imported sentences with target word, last_shown_at, times_shown, **last_comprehension** (understood/partial/no_idea — drives recency filter)
- `sentence_words` — Word-level breakdown with position, surface_form, lemma_id, is_target_word, grammar_role_json
- `sentence_review_log` — Per-sentence review tracking (comprehension, timing, session, client_review_id)
- `grammar_features` — 24 grammar features across 5 categories (number, gender, verb_tense, verb_form, syntax)
- `sentence_grammar_features` — Sentence ↔ grammar feature junction table (is_primary, source)
- `user_grammar_exposure` — Per-feature tracking (times_seen, times_correct, comfort_score, first/last_seen_at)
- `stories` — Generated/imported stories (title_ar/en, body_ar/en, transliteration, status, readiness_pct, difficulty_level)
- `story_words` — Per-token breakdown (position, surface_form, lemma_id, sentence_index, gloss_en, is_known_at_creation, is_function_word)

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
3. `get_base_lemma()` returns top analysis lex; `is_variant_form()` checks enc0 for possessive suffixes
4. `find_matching_analysis()` disambiguates by matching against known DB lemma bare forms
5. Graceful fallback: if `camel-tools` not installed, all functions return stub/empty data
6. Requires `cmake` build dep + `camel_data -i light` download (~660MB) in Docker
7. **Variant cleanup**: `scripts/cleanup_lemma_variants.py` uses CAMeL Tools to detect possessives (بنتي→بنت), feminine forms (جميلة→جميل), and definite duplicates (الكتاب→كتاب). Sets `canonical_lemma_id` on variants. Run with `--dry-run` first, `--merge` to also transfer review data.

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
cd backend && python -m pytest  # 300 tests
```
All services have dedicated test files in `backend/tests/`.

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
- Ternary word marking: off → confused (yellow, FSRS Hard) → missed (red, FSRS Again) → off
- Front-phase word lookup with root prediction (2+ known siblings triggers prediction mode)
- Root info displayed on sentence reveal for missed words
- CAMeL Tools morphology: lemmatization, root extraction, variant detection (possessives, feminine forms, definite duplicates)
- Lemma variant system: canonical_lemma_id marks variants, variant_stats_json tracks per-form accuracy, root family/learn mode filter variants out
- Sentence diversity: weighted sampling + avoid overused words in LLM generation
- Story mode (generate/import) with tap-to-lookup reading and FSRS completion credit
- Learn mode with 5-candidate pick → sentence quiz flow
- Grammar feature tracking with 5-tier progression (24 features)
- LLM: Gemini 3 Flash / GPT-5.2 / Claude Haiku with model_override
- TTS: ElevenLabs eleven_multilingual_v2 with learner pauses
- Offline sync queue + session caching
- Deployed to Hetzner via direct docker-compose

Next: grammar-aware sentence selection, adaptive session pacing, CAMeL Tools sentence-level disambiguation
