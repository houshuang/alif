# Alif — Arabic Reading & Listening Trainer

## Project Overview
A personal Arabic (MSA/fusha) learning app focused exclusively on reading and listening comprehension. No production/writing exercises. Tracks word knowledge at root, lemma, and conjugation levels using FSRS spaced repetition. Combines LLM sentence generation with deterministic NLP validation (CAMeL Tools).

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
- **TTS**: ElevenLabs REST API (not SDK). Model: `eleven_turbo_v2_5`. Key: ELEVENLABS_API_KEY in `.env`. Frontend uses expo-av for playback.
- **NLP**: Rule-based clitic stripping in sentence_validator.py. CAMeL Tools (morphology, lemmatization, root extraction) — planned for Phase 2.
- **Migrations**: Alembic for SQLite schema migrations. Every schema change must have a migration file. Migrations run automatically on startup.
- **Hosting**: Hetzner (46.225.75.29) via Coolify. Docker-compose deploys backend on port 3000. Frontend configurable via `app.json` extra.apiUrl.
- **Transliteration**: ALA-LC standard (kitāb, madrasa) with macrons for long vowels
- **Diacritics**: Always show on all Arabic text
- **CORS**: Wide open (`*`) — single-user app, no auth

## Review Modes

### Sentence-First Review (primary mode)
Reviews are sentence-centric: greedy set cover selects sentences that maximize due-word coverage per session.
1. `GET /api/review/next-sentences` assembles session via 6-stage pipeline (fetch due → candidates → greedy cover → gap fill → order → return)
2. All words in a reviewed sentence get FSRS credit (primary word + collateral credit for others)
3. Binary ratings: understood (rating=3 for all) / partial (tap missed words, rating=1) / no_idea (rating=1 for all)
4. Falls back to word-only cards when no sentences available

### Reading Mode (implemented)
1. User sees Arabic sentence (diacritized, large RTL text)
2. Taps "Show Answer" to reveal: English translation
3. Taps words they didn't know → builds missed_lemma_ids
4. Rates: Got it / Missed

### Listening Mode (implemented, real TTS via expo-av)
1. Audio plays via ElevenLabs TTS (falls back to 2s timer if TTS fails)
2. Tap to reveal Arabic text — tap words you didn't catch
3. Tap to reveal English translation + transliteration
4. Rate comprehension

### Learn Mode (implemented)
1. Algorithm selects best next words (frequency + root familiarity scoring)
2. Tap a word to introduce it (creates FSRS card, shows root family)
3. Quick quiz on introduced words

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
- `IDEAS.md` — Master ideas file (always update)
- `research/` — Detailed research reports on Arabic NLP tools, datasets, APIs, hosting
- `backend/app/models.py` — SQLAlchemy models (Root, Lemma, UserLemmaKnowledge, ReviewLog, Sentence, SentenceWord, SentenceReviewLog, GrammarFeature, SentenceGrammarFeature, UserGrammarExposure)
- `backend/app/services/fsrs_service.py` — FSRS spaced repetition (get_due_cards, submit_review)
- `backend/app/services/sentence_selector.py` — Sentence-centric session assembly (greedy set cover algorithm)
- `backend/app/services/sentence_review_service.py` — Multi-word FSRS credit on sentence review
- `backend/app/services/word_selector.py` — Next-word selection algorithm (frequency + root familiarity + grammar pattern scoring)
- `backend/app/services/sentence_generator.py` — LLM sentence generation with validation
- `backend/app/services/sentence_validator.py` — Deterministic sentence validation with clitic stripping
- `backend/app/services/grammar_service.py` — Grammar feature tracking, comfort scores, tier progression
- `backend/app/services/grammar_tagger.py` — LLM-based grammar feature tagging for sentences/lemmas
- `backend/app/services/listening.py` — Listening mode candidate selection
- `backend/app/services/llm.py` — LiteLLM wrapper with retry/fallback + model_override support
- `backend/app/services/tts.py` — ElevenLabs TTS integration
- `backend/app/services/morphology.py` — NLP wrapper (stub, CAMeL Tools planned)
- `backend/app/services/interaction_logger.py` — JSONL interaction logging
- `backend/app/data/duolingo_raw.json` — Raw Duolingo export (302 lexemes)
- `backend/scripts/import_duolingo.py` — Duolingo import (196 words, 23 roots after filtering)
- `backend/scripts/benchmark_llm.py` — LLM model benchmarking across 5 tasks (105 test cases)
- `backend/scripts/benchmark_data.json` — Ground truth data for benchmarking
- `backend/scripts/backfill_lemma_grammar.py` — Backfill grammar features on existing lemmas
- `backend/data/logs/` — Interaction logs (JSONL, gitignored)
- `frontend/lib/api.ts` — API client with configurable BASE_URL (via expo-constants)
- `frontend/lib/types.ts` — TypeScript interfaces including sentence review types
- `frontend/app/index.tsx` — Review screen (sentence-first + word-only fallback, reading + listening modes, TTS via expo-av)
- `frontend/app/learn.tsx` — Learn new words screen
- `frontend/app/words.tsx` — Word browser with search + filter
- `frontend/app/stats.tsx` — Analytics dashboard (CEFR, pace, history chart)
- `frontend/app/word/[id].tsx` — Word detail with root family

## API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/words?limit=50&status=learning` | List words with knowledge state |
| GET | `/api/words/{id}` | Word detail with review stats + root family |
| GET | `/api/review/next?limit=10` | Due review cards (FSRS scheduling, legacy) |
| GET | `/api/review/next-listening` | Listening-suitable review cards (legacy) |
| GET | `/api/review/next-sentences?limit=10&mode=reading` | Sentence-centric review session (primary) |
| POST | `/api/review/submit` | Submit single-word review rating (legacy) |
| POST | `/api/review/submit-sentence` | Submit sentence review with multi-word FSRS credit |
| GET | `/api/learn/next-words?count=3` | Best next words to introduce |
| POST | `/api/learn/introduce` | Introduce a word (create FSRS card) |
| GET | `/api/learn/root-family/{root_id}` | Words from a root with knowledge state |
| GET | `/api/grammar/features` | All grammar features with categories |
| GET | `/api/grammar/progress` | User's grammar exposure/comfort per feature |
| GET | `/api/grammar/unlocked` | Current tier and unlocked grammar features |
| GET | `/api/stats` | Basic stats (total, known, learning, due) |
| GET | `/api/stats/analytics` | Full analytics (pace, CEFR estimate, history) |
| GET | `/api/stats/cefr` | CEFR reading level estimate |
| POST | `/api/import/duolingo` | Run Duolingo import |
| POST | `/api/sentences/generate` | Generate sentence for a target word |
| GET | `/api/tts/audio/{text}` | Generate TTS audio |

## Data Model
- `roots` — 3/4 consonant roots with core meaning (23 roots imported)
- `lemmas` — Base dictionary forms with root FK, POS, gloss, frequency rank, grammar_features_json (196 words)
- `user_lemma_knowledge` — FSRS card state per lemma (knowledge_state: new/learning/known/lapsed)
- `review_log` — Full review history (rating, timing, mode, comprehension signal, sentence_id, credit_type)
- `sentences` — Generated/imported sentences with target word, last_shown_at
- `sentence_words` — Word-level breakdown of sentences with grammar_role_json
- `sentence_review_log` — Per-sentence review tracking (comprehension, timing, session)
- `grammar_features` — 24 grammar features across 5 categories (number, gender, verb_tense, verb_form, syntax)
- `sentence_grammar_features` — Sentence ↔ grammar feature junction table
- `user_grammar_exposure` — Per-feature tracking (times_seen, times_correct, comfort_score, tier progression)
- `interaction_log` — All app interactions (JSONL files in `backend/data/logs/`)

## NLP Pipeline
**Current (rule-based):**
1. Whitespace tokenization + diacritic stripping
2. Clitic stripping: proclitics (و، ف، ب، ل، ك، وال، بال، فال، لل، كال) and enclitics (ه، ها، هم، هن، هما، كم، كن، ك، نا، ني)
3. Taa marbuta handling (ة → ت before suffixes)
4. Match against known bare forms set (with ال prefix variants)

**Planned (CAMeL Tools):**
1. Input word → CAMeL Tools `analyze_word()` → multiple analyses
2. In sentence context → CAMeL Tools MLE disambiguator → best analysis
3. Extract: lemma (diacritized), root, POS, morphological features
4. Validate LLM grammar tags against morphological analysis

## LLM Benchmarking
```bash
cd backend && python scripts/benchmark_llm.py --task all
# Or specific: --task diacritization --models gemini,anthropic
```
Tests 3 models across 5 tasks (105 ground truth cases): diacritization, translation, transliteration, sentence generation, grammar tagging.

## Testing
```bash
cd backend && python -m pytest  # ~236+ tests
```
All services have dedicated test files in `backend/tests/`.

## Current Phase
Phase 1: Sentence-centric architecture. Features:
- Sentence-first review with greedy set cover scheduling
- Multi-word FSRS credit (primary + collateral) per sentence review
- Arabic clitic handling (proclitics, enclitics, taa marbuta)
- Grammar feature tracking with 5-tier progression (24 features)
- LLM model config: Gemini 3 Flash / GPT-5.2 / Claude Haiku with model_override
- LLM benchmarking suite (105 test cases, 5 tasks)
- Real TTS audio playback via expo-av
- Configurable API URL for deployment
- Deployed to Hetzner via Coolify

Next: CAMeL Tools integration, sentence pre-generation pipeline, grammar-aware sentence selection
