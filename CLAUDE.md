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
- **Frontend**: Expo (React Native) with web mode — `frontend/`
- **SRS**: py-fsrs (FSRS algorithm) — `backend/app/services/fsrs_service.py`
- **LLM**: LiteLLM for unified multi-model (Gemini Flash primary, GPT fallback). Keys: GEMINI_KEY, OPENAI_KEY in `.env`
- **TTS**: ElevenLabs REST API (not SDK). Model: `eleven_turbo_v2_5`. Key: ELEVENLABS_API_KEY in `.env`
- **NLP**: CAMeL Tools (morphology, lemmatization, root extraction) — planned, stub in place
- **Migrations**: Alembic for SQLite schema migrations. Every schema change must have a migration file. Migrations run automatically on startup.
- **Hosting**: Local for now. When ready: Hetzner CAX11 + Coolify (~$4/mo). Dockerfile + docker-compose.yml included.
- **Transliteration**: ALA-LC standard (kitāb, madrasa) with macrons for long vowels
- **Diacritics**: Always show on all Arabic text

## Review Modes

### Reading Mode (implemented)
1. User sees Arabic word (diacritized, large RTL text)
2. Taps "Show Answer" to reveal: English translation
3. Rates: Got it / Missed
4. With sentences: tap individual words you didn't know

### Listening Mode (implemented, audio simulated)
1. Audio plays (simulated with timer, TTS integration pending)
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
- `backend/app/models.py` — SQLAlchemy models (Root, Lemma, UserLemmaKnowledge, ReviewLog, Sentence, SentenceWord)
- `backend/app/services/fsrs_service.py` — FSRS spaced repetition (get_due_cards, submit_review)
- `backend/app/services/word_selector.py` — Next-word selection algorithm (frequency + root familiarity scoring)
- `backend/app/services/sentence_generator.py` — LLM sentence generation with validation
- `backend/app/services/sentence_validator.py` — Deterministic sentence validation against known words
- `backend/app/services/listening.py` — Listening mode candidate selection
- `backend/app/services/llm.py` — LiteLLM wrapper with retry/fallback
- `backend/app/services/tts.py` — ElevenLabs TTS integration
- `backend/app/services/morphology.py` — NLP wrapper (stub, CAMeL Tools planned)
- `backend/app/services/interaction_logger.py` — JSONL interaction logging
- `backend/app/data/duolingo_raw.json` — Raw Duolingo export (302 lexemes)
- `backend/scripts/import_duolingo.py` — Duolingo import (196 words, 23 roots after filtering)
- `backend/data/logs/` — Interaction logs (JSONL, gitignored)
- `frontend/lib/api.ts` — API client (maps backend responses to frontend types)
- `frontend/lib/types.ts` — TypeScript interfaces for all data shapes
- `frontend/app/index.tsx` — Review screen (reading + listening modes)
- `frontend/app/learn.tsx` — Learn new words screen
- `frontend/app/words.tsx` — Word browser with search + filter
- `frontend/app/stats.tsx` — Analytics dashboard (CEFR, pace, history chart)
- `frontend/app/word/[id].tsx` — Word detail with root family

## API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/words?limit=50&status=learning` | List words with knowledge state |
| GET | `/api/words/{id}` | Word detail with review stats + root family |
| GET | `/api/review/next?limit=10` | Due review cards (FSRS scheduling) |
| GET | `/api/review/next-listening` | Listening-suitable review cards |
| POST | `/api/review/submit` | Submit review rating |
| GET | `/api/learn/next-words?count=3` | Best next words to introduce |
| POST | `/api/learn/introduce` | Introduce a word (create FSRS card) |
| GET | `/api/learn/root-family/{root_id}` | Words from a root with knowledge state |
| GET | `/api/stats` | Basic stats (total, known, learning, due) |
| GET | `/api/stats/analytics` | Full analytics (pace, CEFR estimate, history) |
| GET | `/api/stats/cefr` | CEFR reading level estimate |
| POST | `/api/import/duolingo` | Run Duolingo import |
| POST | `/api/sentences/generate` | Generate sentence for a target word |
| GET | `/api/tts/audio/{text}` | Generate TTS audio |

## Data Model
- `roots` — 3/4 consonant roots with core meaning (23 roots imported)
- `lemmas` — Base dictionary forms with root FK, POS, gloss, frequency rank (196 words)
- `user_lemma_knowledge` — FSRS card state per lemma (knowledge_state: new/learning/known/lapsed)
- `review_log` — Full review history (rating, timing, mode, comprehension signal)
- `sentences` — Generated/imported sentences with target word
- `sentence_words` — Word-level breakdown of sentences
- `interaction_log` — All app interactions (JSONL files in `backend/data/logs/`)

## NLP Pipeline (planned)
1. Input word → CAMeL Tools `analyze_word()` → multiple analyses
2. In sentence context → CAMeL Tools MLE disambiguator → best analysis
3. Extract: lemma (diacritized), root, POS, morphological features
4. For sentence validation: tokenize → lemmatize each word → check against known-lemma set

Currently using a stub morphology service. CAMeL Tools integration is the next major step.

## Testing
```bash
cd backend && python -m pytest  # 188 tests, ~3s
```
All services have dedicated test files in `backend/tests/`.

## Current Phase
Phase 0 complete. Working MVP with:
- 196 Duolingo words imported with root families
- FSRS spaced repetition with reading + listening review modes
- Word selection algorithm for learning new words
- LLM sentence generation + validation (needs API keys)
- Full analytics (CEFR estimate, learning pace, streak tracking)
- Expo web frontend connected to real backend API

Next: CAMeL Tools integration, real TTS audio, deploy to Hetzner
