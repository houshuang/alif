# Alif — Arabic Reading & Listening Trainer

## Project Overview
A personal Arabic (MSA/fusha) learning app focused exclusively on reading and listening comprehension. No production/writing exercises. Tracks word knowledge at root, lemma, and conjugation levels using FSRS spaced repetition. Combines LLM sentence generation with deterministic NLP validation (CAMeL Tools).

## Architecture
- **Backend**: Python + FastAPI + SQLite (single user, no auth)
- **Frontend**: Expo (React Native) with web mode for testing on iPhone via Expo Go
- **NLP**: CAMeL Tools (morphology, lemmatization, root extraction), CATT (diacritization)
- **SRS**: py-fsrs (FSRS algorithm)
- **LLM**: LiteLLM for unified multi-model (Gemini Flash primary, GPT fallback). See `/Users/stian/src/nrk/kulturperler/web/scripts/api_client.py` for reference pattern. Keys: GEMINI_KEY, OPENAI_KEY in `.env`
- **TTS**: ElevenLabs REST API (not SDK). See `/Users/stian/src/ninjaord/src/lib/elevenlabs.ts` for reference pattern. Model: `eleven_turbo_v2_5`. Key: ELEVENLABS_API_KEY in `.env`
- **Offline**: App works fully offline for reviews. Backend needed only for NLP processing, LLM sentence generation, and TTS.
- **Hosting**: Local for now. When ready: Hetzner CAX11 + Coolify (~$4/mo). Code must be deploy-ready (Dockerfile, .env separation).
- **Migrations**: Alembic for SQLite schema migrations. Every schema change must have a migration file.
- **Transliteration**: ALA-LC standard (kitāb, madrasa) with macrons for long vowels
- **Diacritics**: Always show on all Arabic text
- **Related projects**: Comenius (`/Users/stian/src/comenius`) has production-ready patterns for schema, ingestion, SRS, offline sync, and Gemini integration. Adapt patterns but keep Python backend.

## Review Flow
1. User sees Arabic sentence (fully diacritized, large RTL text)
2. Self-assesses comprehension
3. Taps to reveal: English translation + ALA-LC transliteration
4. Rates: Again / Hard / Good / Easy
5. Future mode: Audio-first (blank screen → hear sentence → tap to reveal Arabic → tap for English)

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
- `backend/app/services/morphology.py` — Core NLP wrapper (CAMeL Tools)
- `backend/app/services/fsrs_service.py` — Spaced repetition engine
- `backend/app/models.py` — SQLAlchemy data model
- `backend/app/data/duolingo_raw.json` — Raw Duolingo export (302 lexemes)
- `data/logs/` — Interaction logs (JSONL)

## Data Model (MVP)
- `roots` — 3/4 consonant roots with core meaning
- `lemmas` — Base dictionary forms with root FK, POS, gloss, frequency rank
- `user_lemma_knowledge` — FSRS card state per lemma
- `review_log` — Full review history (rating, timing, context)
- `interaction_log` — All app interactions (JSONL files)

## NLP Pipeline
1. Input word → CAMeL Tools `analyze_word()` → multiple analyses
2. In sentence context → CAMeL Tools MLE disambiguator → best analysis
3. Extract: lemma (diacritized), root, POS, morphological features
4. For sentence validation: tokenize → lemmatize each word → check against known-lemma set

## Current Phase
Phase 0: NLP pipeline validation + Duolingo import + simple web preview
