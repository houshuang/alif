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
- **Backend**: Python 3.11+ / FastAPI / SQLite (single user, no auth, WAL mode, 15s busy_timeout) — `backend/`
- **Frontend**: Expo (React Native) with web + iOS mode — `frontend/`
- **SRS**: py-fsrs v6 (FSRS-6 with same-day review support) — `backend/app/services/fsrs_service.py`
- **LLM**: Two-tier model strategy. Background/cron tasks use Claude CLI (free via Max plan): Sonnet for sentence gen, Haiku for quality gate + enrichment + hooks. On-demand/user-facing tasks keep Gemini Flash (fast, ~1s). Story gen: Claude Opus (retry loop). General fallback: Gemini 3 Flash → GPT-5.2 → Claude Haiku API. Keys: GEMINI_KEY, OPENAI_KEY, ANTHROPIC_API_KEY in `.env`
- **Claude Code CLI**: `claude -p` wrapper for free LLM via Max plan. Integrated into `llm.py` as `claude_sonnet`/`claude_haiku` model overrides. Also: standalone `generate_structured()` + `generate_with_tools()` in `claude_code.py`. See `docs/backend-services.md`.
- **TTS**: ElevenLabs REST, `eleven_multilingual_v2`, Chaouki voice, learner pauses. Key: ELEVENLABS_API_KEY. Audio cached by SHA256 in `backend/data/audio/`.
- **NLP**: Rule-based clitic stripping + known-form matching + CAMeL disambiguation fallback. `LemmaLookupDict` tracks collisions (hamza-sensitive resolution). Two-pass lookup: bare forms first, forms_json second (prevents derived forms shadowing direct lemmas). Extended forms_json indexing (past_3fs, past_3p, imperative, passive_participle). LLM mapping verification active in production (`VERIFY_MAPPINGS_LLM=1`) — Gemini Flash checks sentence_word mappings for homograph errors, discards sentences with bad mappings. See `docs/nlp-pipeline.md`.
- **Migrations**: Alembic for SQLite. Every schema change needs a migration. Auto-runs on startup.
- **Hosting**: Hetzner (46.225.75.29), docker-compose. Backend port 3000→8000. Frontend systemd (`alif-expo`) port 8081. DuckDNS: `alifstian.duckdns.org`. Claude CLI bind-mounted into container (node + claude binary + auth config from host).
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
- **Reading Mode**: front-phase word lookup (root/pattern/sibling navigation), triple-tap marking, back/undo, confusion analysis on yellow tap
- **Listening Mode**: ElevenLabs TTS, reveal Arabic → reveal English
- **Learn Mode**: 5-candidate pick → done (info-dense card with root/pattern chips → detail pages, pattern examples, forms strip, etymology, mnemonic)
- **Story Mode**: generate/import, tap-to-lookup reader (root/pattern navigation), complete/suspend

## Design Principles
- **Word introduction is automatic** — `build_session()` reserves `INTRO_RESERVE_FRACTION` (20%) of session slots for new words, even when due queue exceeds limit. Accuracy-based rate: <70%→0, 70-85%→4, 85-92%→7, ≥92%→10 slots. Per-call cap: MAX_AUTO_INTRO_PER_SESSION=10. **Pipeline backlog gate**: reserved intro slots suppressed when acquiring count > `PIPELINE_BACKLOG_THRESHOLD` (40); undersized-session fill still works. **Fill phase always runs** when session is undersized — in fast mode uses `_find_pregenerated_sentences_for_words()` (DB queries only, no LLM); in prefetch mode uses `_generate_on_demand()`. OCR/story import creates "encountered" state only.
- **No concept of "due"** — the app picks the most relevant cards. Don't use "due" in UI text. Use "ready for review".
- **No bare word cards in review** — ONLY sentences. Generate on-demand or skip if no comprehensible sentence.
- **Comprehensibility gate** — ≥60% known scaffold words required. Acquiring box-1 excluded, encountered excluded (only actively studied words count).
- **Function words** — ~80 particles/prepositions/pronouns/conjunctions (populated from `FUNCTION_WORD_GLOSSES` in `sentence_validator.py`). Excluded from story/book "to learn" counts, book page word introduction, FSRS review credit, scheduling/due counts, and scaffold diversity checks. They still appear in sentences and get glosses. Detection checks both surface form AND resolved lemma bare form (catches cliticized forms like بِهِ → بِ).
- **Story word counts are deduped** — `total_words`, `known_count`, `unknown_count` count unique lemmas, not tokens. Each lemma counted once even if it appears multiple times in the story.
- **On-demand sentence generation** — max 10/session, uses current vocabulary for fresher sentences. Skipped in fast mode (`skip_on_demand=True`); fill phase still runs using pre-generated sentences.
- **Tapped words are always marked missed** — front-phase tapping auto-marks as missed (rating≤2).
- **Confusion analysis on yellow tap** — when a word transitions to "did not recognize" (yellow), `confusion_service.py` analyzes why: (1) morphological decomposition via clitic stripping + form matching, (2) visual similarity via edit distance + rasm skeleton (dots-removed), (3) phonetic similarity via `PHONETIC_MAP` (ص≈س, ح≈ه, ع≈أ, ط≈ت, etc. — catches sound-alike confusions like سبع↔صباح), (4) prefix disambiguation hint (و/ف/ب/ل/ك — "part of root" vs "is prefix"). Similarity pool includes `encountered` state. All rule-based, no LLM, <50ms. Endpoint: `GET /api/review/confusion-help/{lemma_id}?surface_form=...`.
- **al-prefix is NOT a separate lemma** — الكلب and كلب are the same lemma. All import paths dedup.
- **Be conservative with ElevenLabs TTS** — costs real money. Only generate for sentences that will be shown.
- **Sentence pipeline**: tier-based lifecycle, no fixed cap binding. Tier 1 (due ≤12h): target 3 sentences, floor 2. Tier 2 (12-36h): target 2, floor 1. Tier 3 (36-72h): target 1, floor 0. Tier 4 (72h+): target 0, floor 0 — sentences actively retired. Safety valve cap at 2000 (should never bind). Pool size bounded by review urgency (~200 tier 1-3 words), not vocabulary size. Cron runs `update_material.py` every 3h. `warm_sentence_cache()` runs after every session load.
- **Verb conjugation recognition** — `build_lemma_lookup()` Pass 3 generates ~36 conjugation forms per verb (past suffixes + present prefix/suffix combinations). Weak verb support: uses `past_1s` from forms_json for irregular stems (قال→قلت, مشى→مشيت). Noun inflection: generates sound plurals (ـات/ـون/ـين) and dual forms. Pass 2 indexes ALL string keys from forms_json (no hardcoded whitelist). LLM enrichment provides expanded forms: `past_1s`, `past_3fp`, `present_3fp`, `present_3mp`, `sound_f_plural`, `sound_m_plural`, `dual`.
- **Canonical lemma is the unit of scheduling** — variant forms tracked via `variant_stats_json` but never get independent FSRS cards.
- **All import paths must run variant detection** — `detect_variants_llm()` + `detect_definite_variants()` + `mark_variants()` post-import.
- **All import paths must run quality gate** — `import_quality.classify_lemmas()` filters junk, classifies standard/proper_name/onomatopoeia.
- **Every sentence_word must have a lemma_id** — all 5 storage paths reject unmapped words. Exception: book_import keeps sentences with `lemma_id=None`. Mapping uses `build_comprehensive_lemma_lookup()`.
- **Tashkeel fading is backend-driven** — `show_tashkeel` boolean per word in API response. Backend knows both the setting (mode + threshold) and word stability. Three modes: always (default), fade (hide diacritics for words with stability ≥ threshold), never. Applies only to review sessions — story reader always shows full tashkeel.
- **Tiered graduation** — acquisition uses aggressive graduation tiers: (0) first correct review → instant graduation, (1) 100% accuracy + 3 reviews → any box, (2) ≥80% accuracy + 4 reviews + box ≥ 2, (3) standard: box ≥ 3 + 5 reviews + ≥60% accuracy + 2 calendar days. FSRS safety net catches false positives from fast graduation.
- **Root-aware stability boost** — words graduating from acquisition with 2+ known root siblings get `Rating.Easy` (~3.6x stability boost). `ROOT_SIBLING_THRESHOLD=2` in `acquisition_service.py`.
- **Morphological patterns (wazn)** — `Lemma.wazn` stores normalized pattern (e.g. "fa'il", "maf'ul", "form_2"), `Lemma.wazn_meaning` stores human description. Displayed in learn cards, word info cards, and word detail. Pattern family (other words with same wazn) returned in word detail endpoint. API: `/api/patterns` lists patterns, `/api/patterns/{wazn}` lists words with enrichment, `/api/patterns/roots/{root_id}/tree` shows root derivation tree. `PatternInfo` table stores per-pattern enrichment. Backfill: `scripts/backfill_wazn.py`, `scripts/backfill_pattern_enrichment.py`.
- **Root & pattern enrichment** — LLM-generated cultural/linguistic content for roots (`Root.enrichment_json`) and patterns (`PatternInfo.enrichment_json`). Auto-triggered when a word enters acquisition and its root/pattern has 2+ studied words but no enrichment. Root: Claude Sonnet (etymology, cultural significance, literary examples, fun facts, related roots). Pattern: Claude Haiku (explanation, recognition tips, semantic fields, example derivations, register notes). Backfill: `scripts/backfill_root_enrichment.py`, `scripts/backfill_pattern_enrichment.py`.
- **Explore tab** — Frontend tab with three sub-tabs: Words (existing word browser), Roots (browse/search all roots with coverage stats), Patterns (browse/search all patterns with coverage stats). Detail pages: `/root/{id}` shows enrichment + derivation tree, `/pattern/{wazn}` shows enrichment + word list. Cross-linked from word detail. API: `/api/roots`, `/api/roots/{id}`.

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

### 8. Code Style
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
Profiles: `beginner` (55%), `strong` (85%), `casual` (70%), `intensive` (75%), `calibrated` (80%, from production data). Code: `backend/app/simulation/`.

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

## Server Operations — MUST READ
See `.claude/skills/server-ops.md` for full details. Summary of hard-won rules:

1. **ALL `ssh` commands require `dangerouslyDisableSandbox: true`** — SSH is always blocked by local sandbox. Never try without it.
2. **NEVER write inline Python in `docker exec python3 -c`** — Triple-nested quoting fails ~50% of the time. For any Python > 2 lines, write to `/tmp/claude/script.py`, then `scp` + `docker cp` + run.
3. **Read `backend/app/models.py` BEFORE writing DB queries** — Don't guess table/column names. They've caused repeated failures (e.g., `lemma` vs `lemmas`, `query()` vs `get()`).
4. **Check `backend/scripts/` before writing ad-hoc queries** — Existing scripts cover most analytics and maintenance tasks.
5. **One deploy per session** — Get code right locally (tests pass), then deploy once. Multiple deploys waste time and risk inconsistent state.

Next: more story imports, listening mode improvements
