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
- **Backend**: Python 3.11+ / FastAPI / SQLite (single user, no auth, WAL mode, 5s busy_timeout) — `backend/`
- **Frontend**: Expo (React Native) with web + iOS mode — `frontend/`
- **SRS**: py-fsrs v6 (FSRS-6 with same-day review support) — `backend/app/services/fsrs_service.py`
- **LLM**: LiteLLM multi-model. Sentence gen: Gemini Flash. Story gen: Claude Opus (retry loop). Quality gate: Claude Haiku (fail-closed). General: Gemini 3 Flash → GPT-5.2 → Claude Haiku. Keys: GEMINI_KEY, OPENAI_KEY, ANTHROPIC_API_KEY in `.env`
- **Claude Code CLI**: Optional `claude -p` wrapper for free Opus via Max plan. `generate_structured()` (single-turn) and `generate_with_tools()` (multi-turn agentic). See `docs/backend-services.md`.
- **TTS**: ElevenLabs REST, `eleven_multilingual_v2`, Chaouki voice, learner pauses. Key: ELEVENLABS_API_KEY. Audio cached by SHA256 in `backend/data/audio/`.
- **NLP**: Rule-based clitic stripping + known-form matching + CAMeL disambiguation fallback. `LemmaLookupDict` tracks collisions (hamza-sensitive resolution). Extended forms_json indexing (past_3fs, past_3p, imperative, passive_participle). Optional LLM mapping verification (env `VERIFY_MAPPINGS_LLM=1`, default off). See `docs/nlp-pipeline.md`.
- **Migrations**: Alembic for SQLite. Every schema change needs a migration. Auto-runs on startup.
- **Hosting**: Hetzner (46.225.75.29), docker-compose. Backend port 3000→8000. Frontend systemd (`alif-expo`) port 8081. DuckDNS: `alifstian.duckdns.org`.
- **Offline**: AsyncStorage sync queue, 30-min session staleness TTL, background session refresh (15-min gap detection).

## Reference Docs
| Doc | Contents |
|-----|----------|
| `docs/scheduling-system.md` | Word lifecycle, session building, FSRS/acquisition phases, all constants |
| `docs/backend-services.md` | All backend service descriptions with key behaviors |
| `docs/frontend-files.md` | All frontend screens, components, and infrastructure files |
| `docs/data-model.md` | SQLAlchemy models and table schemas |
| `docs/api-reference.md` | Full API endpoint reference |
| `docs/nlp-pipeline.md` | NLP pipeline: clitic stripping, CAMeL Tools, morphology |
| `docs/review-modes.md` | Full UX flows for all review modes |
| `docs/scripts-catalog.md` | All import, backfill, cleanup, analysis scripts |

## Review Modes (summary)
- **Sentence-First Review**: greedy set cover, ternary ratings, all words get equal FSRS credit
- **Reading Mode**: front-phase word lookup, triple-tap marking, back/undo
- **Listening Mode**: ElevenLabs TTS, reveal Arabic → reveal English
- **Learn Mode**: 5-candidate pick → sentence quiz → done
- **Story Mode**: generate/import, tap-to-lookup reader, complete/suspend

## Design Principles
- **Word introduction is automatic** — `build_session()` auto-introduces encountered words when session is undersized. No global cap on acquiring count — session limit is the natural throttle. Accuracy-based rate: <70%→0, 70-85%→4, 85-92%→7, ≥92%→10 slots. Per-call cap: MAX_AUTO_INTRO_PER_SESSION=10. Fill phase runs a second pass if still undersized. OCR/story import creates "encountered" state only.
- **No concept of "due"** — the app picks the most relevant cards. Don't use "due" in UI text. Use "ready for review".
- **No bare word cards in review** — ONLY sentences. Generate on-demand or skip if no comprehensible sentence.
- **Comprehensibility gate** — ≥60% known scaffold words required. Acquiring box-1 words excluded from "known" count. All words are learnable (no function word exclusions).
- **On-demand sentence generation** — max 10/session, uses current vocabulary for fresher sentences.
- **Tapped words are always marked missed** — front-phase tapping auto-marks as missed (rating≤2).
- **al-prefix is NOT a separate lemma** — الكلب and كلب are the same lemma. All import paths dedup.
- **Be conservative with ElevenLabs TTS** — costs real money. Only generate for sentences that will be shown.
- **Sentence pipeline cap**: 300 active sentences. Cron runs `rotate_stale_sentences.py` then `update_material.py` every 6h.
- **Canonical lemma is the unit of scheduling** — variant forms tracked via `variant_stats_json` but never get independent FSRS cards.
- **All import paths must run variant detection** — `detect_variants_llm()` + `detect_definite_variants()` + `mark_variants()` post-import.
- **All import paths must run quality gate** — `import_quality.classify_lemmas()` filters junk, classifies standard/proper_name/onomatopoeia.
- **Every sentence_word must have a lemma_id** — all 5 storage paths reject unmapped words. Exception: book_import keeps sentences with `lemma_id=None`. Mapping uses `build_comprehensive_lemma_lookup()`.

## Critical Rules for All Agents

### 1. IDEAS.md — Always Update
The file `IDEAS.md` is the master record of ALL project ideas. Read at start of work, add new ideas discovered during development, never remove ideas.

### 2. Interaction Logging — Log Everything
Every user interaction must be logged. Append-only JSONL files (`data/logs/interactions_YYYY-MM-DD.jsonl`). Schema:
```json
{"ts": "ISO8601", "event": "review", "lemma_id": 42, "rating": 3, "response_ms": 2100, "context": "sentence_id:17", "session_id": "abc123"}
```

### 3. Testability — Claude Must Be Able to Test Everything
- All logic in the API, never in the UI. Every service has pytest tests, every endpoint testable with curl.
- Web preview via `npx expo start --web`. Mock data in `frontend/lib/mock-data.ts`.

### 4. Skills — Generate and Update
Create reusable Claude Code skills (`.claude/skills/`) for common operations.

### 5. Experiment Tracking — Document Everything
- `docs/scheduling-system.md`: Update on ANY scheduling change
- `research/experiment-log.md`: Add entry BEFORE algorithm changes
- `research/analysis-YYYY-MM-DD.md`: Save analysis findings

### 6. Code Style
- Python: type hints, pydantic models for API schemas
- TypeScript: strict mode, functional components
- No test plans or checklists in PR descriptions
- Branch prefix: `sh/` for all GitHub branches

## Key Backend Files
- `backend/app/models.py` — SQLAlchemy models (see `docs/data-model.md`)
- `backend/app/schemas.py` — Pydantic request/response models
- `backend/app/routers/` — API routes (see `docs/api-reference.md`)
- `backend/app/services/` — All services (see `docs/backend-services.md`)
- `backend/scripts/` — All scripts (see `docs/scripts-catalog.md`)

## Testing
```bash
cd backend && python3 -m pytest
cd frontend && npm test
```

### Simulation Framework
End-to-end simulation of multi-day learning journeys:
```bash
python3 scripts/simulate_sessions.py --days 30 --profile beginner
```
Profiles: `beginner` (55%), `strong` (85%), `casual` (70%), `intensive` (75%). Code: `backend/app/simulation/`.

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

Next: more story imports, listening mode improvements
