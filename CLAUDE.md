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
- **Backend**: Python 3.11+ / FastAPI / SQLite (single user, no auth, WAL mode, 30s busy_timeout) — `backend/`
- **Frontend**: Expo (React Native) with web + iOS mode — `frontend/`
- **SRS**: py-fsrs v6 (FSRS-6 with same-day review support) — `backend/app/services/fsrs_service.py`
- **TTS**: ElevenLabs REST, `eleven_multilingual_v2`, PVC voice. Audio cached by SHA256 in `backend/data/audio/`. Story audio in `backend/data/story-audio/`.
- **NLP**: Rule-based clitic stripping + known-form matching + CAMeL disambiguation + LLM disambiguation. See `docs/nlp-pipeline.md`.
- **Migrations**: Alembic for SQLite. Every schema change needs a migration. Auto-runs on startup.
- **Hosting**: Hetzner (46.225.75.29), venv + systemd (no Docker). Backend: systemd service `alif-backend`, port 3000, venv at `/opt/alif/backend/.venv/`. Frontend: systemd service `alif-expo`, port 8081. DuckDNS: `alifstian.duckdns.org`. Data at `/opt/alif/backend/data/`. Limbic at `/opt/limbic` (PYTHONPATH), cost DB at `/opt/limbic-data/llm_costs.db`.
- **Spanish Pilot**: Standalone UX-validation prototype at `spanish-pilot/` — separate SQLite, separate systemd `alif-spanish-pilot` on port 3100 (`/opt/alif-pilot/`). Norwegian UI, no English. Tests Alif's word-level SRS + intro cards + memory hooks on 60 Norwegian school students learning Spanish. See `spanish-pilot/README.md`. Does NOT share any code with main Alif backend — completely isolated.
- **LLM Cost Tracking**: All litellm calls auto-logged via `limbic.cerebellum.cost_log` callback. Sync to local: `python -m limbic.cerebellum.cost_log sync`. Reports: `python -m limbic.cerebellum.cost_log report --days 7`.
- **Offline**: AsyncStorage sync queue for all mutable actions. Auto-prefetch, background refresh, 12s fetch timeout with stale-cache fallback. See `docs/frontend-files.md`.

## LLM Architecture
- **Claude CLI (`claude -p`)** is the primary LLM backend for ALL batch/background text tasks — free via Max plan. Integrated into `llm.py` as `claude_sonnet`/`claude_haiku` model overrides (default when no override specified). Also: `generate_structured()` + `generate_with_tools()` in `claude_code.py`. **For JSON responses, prefer `json_schema=` over `json_mode=True`** — uses `--json-schema` constrained decoding which guarantees valid JSON. Without it, CLI models wrap JSON in explanation text that can fail to parse (caused a major verification bug 2026-04-14).
- **Model routing**: `claude_sonnet` for sentence generation (87% pass rate), `claude_haiku` for quality gate + enrichment + tagging + flags + disambiguation + verification. Story gen: Claude Opus via `claude_code.py` (retry loop).
- **Latency-sensitive paths use Anthropic API directly** (`model_override="anthropic"` in `llm.py`) — CLI subprocess startup adds ~2-3s which is unacceptable for interactive UX. Current direct-API paths: interactive chat (`/api/chat/ask`). **Do NOT change these to CLI without asking** — the speed difference matters.
- **API fallback chain** (when CLI unavailable): GPT-5.2 -> Claude Haiku API.
- **Gemini**: OCR/Vision ONLY (`ocr_service.py`). Keys: GEMINI_KEY (OCR only), OPENAI_KEY, ANTHROPIC_API_KEY in `.env`.
- **Claude CLI on server**: `/usr/bin/claude`, authenticated via `claude setup-token`, Max plan.

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
| `docs/design-principles.md` | Feature-level design decisions (intro cards, tashkeel, fonts, graduation, etc.) |

## Review Modes
See `docs/review-modes.md` for full UX flows. Modes: Sentence-First Review (primary), Reading Mode, Listening Mode, Learn Mode, Story Mode, Quran Reading Mode (suspended 2026-04-07), Podcast Mode.

## Hard Invariants
These rules have all caused production bugs or data corruption when violated. For feature-level design details (intro cards, tashkeel, fonts, graduation tiers, etc.), see `docs/design-principles.md`.

- **FOUNDATIONAL: Every word in every sentence earns review credit** — when a sentence is reviewed, ALL non-function words get a review (acquisition or FSRS), regardless of whether they are the "target" word or collateral scaffold. This is the core learning mechanism. A word seen 10 times collaterally with correct ratings has been learned — the system must recognize this. No word should be invisible to the review engine. Encountered words that appear in reviewed sentences are auto-introduced to acquisition and get their first review immediately; Tier 0 instant graduation handles familiar words. **No artificial throttles on this flow.**
- **No bare word cards in review** — ONLY sentences. Generate on-demand or skip if no comprehensible sentence.
- **No LLM calls in session build critical path** — `build_session()` must stay fast (<1s). All LLM work happens at generation time or in `warm_sentence_cache` background tasks. A previous synchronous verification gate caused 30-60s timeouts (2026-03-17).
- **No on-demand sentence generation in session build** — sessions build entirely from pre-generated sentences (DB queries only, <1s). `warm_sentence_cache()` generates for gaps after each session. The cron generates via `generate_material_for_word()` every 3h.
- **All sentence generation must go through `generate_material_for_word()`** — this is the single verified pipeline: disambiguation -> LLM verification -> correction -> `mappings_verified_at`. Never create a separate generation path that skips verification — this was the source of 29 bad-mapping flags (2026-03-21 fix).
- **All import paths must call `run_quality_gates()`** — centralized post-creation pipeline in `lemma_quality.py`. Runs: finalize -> variant detection -> enrichment -> stamps `gates_completed_at`. **Model-level guard**: `select_next_words()` and `_build_reintro_cards()` filter out lemmas where `gates_completed_at IS NULL` — ungated lemmas never appear in sessions.
- **Every sentence_word must have a lemma_id** — all 5 storage paths reject unmapped words. Exception: book_import keeps sentences with `lemma_id=None`. Mapping uses `build_comprehensive_lemma_lookup()`.
- **No auto-created lemmas from corrections** — `correct_mapping()` and flag resolution only use existing DB lemmas. If the correct lemma isn't in the vocabulary, the sentence is rejected/retired. This prevents orphan lemmas that bypass quality gates.
- **No words without English gloss — EVER** — Three validation gates: (1) `generate_material_for_word()` rejects sentences where any lemma has empty `gloss_en`. (2) Quran 6-layer fallback pipeline. (3) Frontend cache bypass when cached result has no `gloss_en`. Tests: `test_gloss_coverage.py`.
- **Canonical lemma is the unit of scheduling** — variant forms tracked via `variant_stats_json` but never get independent FSRS cards. **Multi-hop chain resolution**: variant chains (A->B->C) are followed to the root canonical everywhere. Bug fix (2026-03-23): single-hop resolution caused variants to be introduced despite root canonical being known.
- **Verification failure != success** — `verify_and_correct_mappings_llm()` returns `None` on LLM failure (distinct from `[]` = verified OK). Callers discard/skip sentences that can't be verified.
- **Be conservative with ElevenLabs TTS** — costs real money. Only generate for sentences that will be shown. Story audio is more expensive — only generate when requested or via cron.
- **Always prefer Claude CLI (`claude -p`) for LLM tasks** — Claude CLI is free via Max plan and is the primary LLM backend. When designing new LLM-powered features or scripts: (1) Default to `claude -p` via `generate_completion()` in `llm.py` — don't reach for API keys. (2) Design multi-step workflows that leverage Claude's reasoning, not just one-shot prompts. Feed context (vocabulary files, validation results, previous attempts) so Claude can self-correct. Use `generate_with_tools()` for agentic sessions where Claude reads files and runs validation in a loop. (3) Batch related items into single calls — 15 words in one prompt beats 15 separate calls (4s/word vs 30s/word, proven in sentence generation). (4) Only use Anthropic API directly for latency-sensitive user-facing paths (currently: `/api/chat/ask`). The ~2-3s CLI startup overhead is unacceptable for interactive UX but irrelevant for background/batch work. (5) Exception: Gemini for vision/OCR only. API fallback chain (GPT-5.2 -> Claude Haiku API) only when CLI unavailable.

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
- `research/experiment-log.md` is **append-only** — NEVER delete existing entries. New entries go at the top (after the header).
- `research/research-hub.html`: Update when adding new research documents (add doc-row entry in appropriate category section)
- `research/README.md`: Update when adding new research documents
- `research/analysis-YYYY-MM-DD.md`: Save analysis findings
- **All reports and analysis HTML pages go inside the repo** (in `research/`), not in external dirs like `~/.agent/diagrams/`. Link them from the experiment log entry that prompted them.

### 6. Git Diff Discipline — Prevent Silent Reverts
**CRITICAL**: Before every commit, run `git diff --stat HEAD` and review what changed. Watch for:
- **Append-only files shrinking** (`experiment-log.md`, `IDEAS.md`) — this means entries were deleted. NEVER acceptable.
- **Large service files with net deletions** — if `sentence_selector.py` or similar core files show significant line removals, verify those removals are intentional, not regressions.
- **Schema files losing fields** — if `schemas.py` or `types.ts` show removed fields, verify the backend doesn't still compute them.
- **When replacing/rewriting a file**, always diff the old version against the new one to check nothing was lost: `git diff HEAD -- path/to/file`
- **Bundled commits are dangerous** — if a commit touches >5 files across different features, split it or review each file's diff individually.

### 7. Branch Workflow for Non-Trivial Changes — Self-Review Gate
For changes that touch core algorithm files (`sentence_selector.py`, `session_builder`, `fsrs_service.py`, `acquisition_service.py`) or modify >3 files:
1. Create a branch: `git checkout -b sh/<feature-name>`
2. Make changes and commit on the branch
3. Create a PR: `gh pr create --title "..." --body "..."`
4. **Self-review the PR diff** before merging — look at every file's diff on GitHub (`gh pr diff`) and verify:
   - No unintended deletions in append-only files
   - No features silently removed from large files
   - No schema fields lost that the backend still computes
   - Net line counts make sense (a "feature addition" shouldn't have large net deletions)
5. If the self-review passes, merge the PR: `gh pr merge --squash`
6. If issues found, fix on the branch, push, and re-review

Direct commits to `main` are OK for: documentation-only changes, single-file bug fixes, test additions, and changes the user explicitly asked to deploy immediately.

### 8. Gate Audit on Lifecycle Changes
When changing how words move between states (encountered -> acquiring -> FSRS) or adding new flows that alter word states, **audit every gate and filter that operates on those states**. Gates include: comprehensibility gate (x2), unknown scaffold cap, pipeline backlog gate, focus cohort, variant resolution, intro card filter, listening readiness, function word exclusion. The full gate registry is in `docs/scheduling-system.md` §19.17. Lesson learned: the collateral credit change (2026-03-18) broke sessions because the comprehensibility gate wasn't updated for the new box-1 acquiring words it created.

### 9. Code Style
- Python: type hints, pydantic models for API schemas
- TypeScript: strict mode, functional components
- No test plans or checklists in PR descriptions
- Branch prefix: `sh/` for all GitHub branches

### 10. SQLite Write Lock Discipline — Never Hold During Slow Calls
**CRITICAL**: SQLite WAL mode allows only one writer at a time. `db.flush()` and `db.add()`+autoflush acquire the write lock, which is held until `db.commit()` or `db.rollback()`. If an LLM call (5-90s), TTS call, or any network I/O runs between flush and commit, **every other writer in the app blocks for that duration**, causing "database is locked" errors.

**Required pattern** for any function that does both DB writes and slow external calls:
```
Phase 1: Read — query DB, collect data, close/commit session
Phase 2: Slow work — LLM calls, TTS, network I/O (no DB session dirty)
Phase 3: Write — open/reuse session, write results, commit (milliseconds)
```

**Checklist when writing new code:**
- `db.flush()` must NEVER be followed by an LLM/network call before `db.commit()`
- Functions receiving a `db` parameter must not make LLM calls while the session has dirty state
- Background tasks (`BackgroundTasks.add_task`) must not receive the request's `db` session
- Long-running scripts must commit between steps, not hold one session for the entire run
- Non-critical writes (cache updates, counts) should use try/except with rollback so lock contention doesn't crash read endpoints

**Past incidents**: `store_multi_target_sentence` held write lock 30-60s during LLM verification (broke OCR uploads). `_import_unknown_words` held lock during batch translation. Chat endpoint held session during 15s LLM call. All fixed 2026-03-29.

## Key Backend Files
- `backend/app/models.py` — SQLAlchemy models (see `docs/data-model.md`)
- `backend/app/schemas.py` — Pydantic request/response models
- `backend/app/routers/` — API routes (see `docs/api-reference.md`)
- `backend/app/services/` — All services (see `docs/backend-services.md`)
- `backend/scripts/` — All scripts (see `docs/scripts-catalog.md`)

## Testing
```bash
cd backend && python3 -m pytest          # fast tests only (~2 min), slow tests auto-skipped
cd backend && python3 -m pytest -m slow  # slow tests only (real LLM calls, ~40 min)
cd backend && python3 -m pytest -m ''    # all tests
cd frontend && npm test
```

### Simulation Framework
End-to-end simulation of multi-day learning journeys:
```bash
python3 scripts/simulate_sessions.py --days 30 --profile beginner
```
Profiles: `beginner` (55%), `strong` (85%), `casual` (70%), `intensive` (75%), `calibrated` (80%, from production data). Code: `backend/app/simulation/`.

## Deployment
```bash
# Deploy backend (venv + systemd, no Docker)
ssh alif "cd /opt/alif && git pull && cd backend && .venv/bin/pip install -e . --no-deps -q && systemctl restart alif-backend"

# If dependencies changed in pyproject.toml:
ssh alif "cd /opt/alif/backend && .venv/bin/pip install -e . -q && systemctl restart alif-backend"

# Deploy frontend (Expo dev server is a systemd service)
ssh alif "cd /opt/alif && git pull && systemctl restart alif-expo"

# Full deploy (both):
ssh alif "cd /opt/alif && git pull && cd backend && .venv/bin/pip install -e . --no-deps -q && systemctl restart alif-backend && systemctl restart alif-expo"

# Expo URL (always display after deploy):
# exp://alifstian.duckdns.org:8081
# Web: http://alifstian.duckdns.org:8081
```

## Server Operations — MUST READ
See `.claude/skills/server-ops.md` for full details. Summary of hard-won rules:

1. **ALL `ssh` commands require `dangerouslyDisableSandbox: true`** — SSH is always blocked by local sandbox. Never try without it.
2. **For remote Python scripts > 2 lines**: write to `/tmp/claude/script.py`, then `scp alif:/tmp/` and run with `ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 /tmp/script.py"`.
3. **Read `backend/app/models.py` BEFORE writing DB queries** — Don't guess table/column names. They've caused repeated failures (e.g., `lemma` vs `lemmas`, `query()` vs `get()`).
4. **Check `backend/scripts/` before writing ad-hoc queries** — Existing scripts cover most analytics and maintenance tasks.
5. **One deploy per session** — Get code right locally (tests pass), then deploy once. Multiple deploys waste time and risk inconsistent state.
6. **Push before deploy** — `git push` BEFORE running deploy commands. The deploy does `git pull` on the server — if you haven't pushed, the server pulls stale code.
7. **`limbic` install on server** — `pyproject.toml` specifies `limbic @ git+https://...` which tries GitHub clone. On server, install from local: `.venv/bin/pip install -e /opt/limbic` first, then `pip install -e . --no-deps` for alif, then remaining deps separately. CPU-only PyTorch: `pip install torch --index-url https://download.pytorch.org/whl/cpu`.

Next: more story imports, listening mode improvements
