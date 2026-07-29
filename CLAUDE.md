# Alif — Arabic Reading & Listening Trainer

## Project Overview
A personal Arabic (MSA/fusha) learning app focused exclusively on reading and listening comprehension. No production/writing exercises. Tracks word knowledge at root, lemma, and conjugation levels using FSRS spaced repetition. Combines LLM sentence generation with deterministic rule-based validation (clitic stripping + known-form matching).

**North-star metric:** genuinely-known words growing week over week (not activity/review volume). **The user's end goal is classical-literature breadth** — Quran, commentaries, medieval poetry, the full literary tradition — not just MSA. **Near-term curriculum preference (2026-07-09): contemporary literature first, to reach independent real-text reading sooner, while retaining a smaller path toward classical Arabic.** Curriculum and content choices should serve both horizons, not generic frequency lists alone.

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
- **Hosting**: Hetzner (46.225.75.29), venv + systemd (no Docker). Backend: systemd service `alif-backend`, port 3000, venv at `/opt/alif/backend/.venv/`. Frontend: systemd service `alif-expo`, port 8081 (serves the **web** version; since 2026-07-15 the iOS app is a standalone EAS `preview` build with expo-updates OTA — it embeds its JS bundle and does not touch Metro; see Deployment). Spanish Pilot: systemd service `alif-spanish-pilot`, port 3100. Polyglot: systemd service `polyglot-backend`, port 3002, fronted by an in-app reverse proxy at `/polyglot/*` (`backend/app/routers/polyglot_proxy.py`, mounted in `backend/app/main.py`, forwarding to `127.0.0.1:3002`) so the client only needs alif's port 3000. DuckDNS: `alifstian.duckdns.org`. Data at `/opt/alif/backend/data/`. Limbic at `/opt/limbic` (PYTHONPATH), cost DB at `/opt/limbic-data/llm_costs.db`.
- **Spanish Pilot**: Standalone UX-validation prototype at `spanish-pilot/` — separate SQLite, separate systemd `alif-spanish-pilot` on port 3100 (`/opt/alif-pilot/`). Norwegian UI, no English. Tests Alif's word-level SRS + intro cards + memory hooks on 60 Norwegian school students learning Spanish. See `spanish-pilot/README.md`. Does NOT share any code with main Alif backend — completely isolated.
- **Polyglot**: Sister app at `polyglot/` for Modern Greek (primary), Ancient Greek, and Latin. Separate Python package (`polyglot-backend`), separate venv (`polyglot/.venv`), separate SQLite (`polyglot/polyglot.db`), separate FastAPI process on port 3002 (prod; 3001 dev). Reading-as-mapping primary UX (lazy PDF intake → tap unknowns → next-page presumes rest known). Uses simplemma for lemmatization with a dual-provider LLM-in-context quality gate — Codex `gpt-5.5` primary + Claude failover via `polyglot/app/services/llm_cli.py`. FSRS + acquisition Leitner + leech engine ported from Alif (`/api/reviews/{submit,introduce,due,stats}`); `mark_lemma(state='unknown')` enrols into Box 1 immediately. **Do NOT confuse `backend/` and `polyglot/`** — see `polyglot/CLAUDE.md` for project-specific rules. **Mirror Alif's design and code by default; divergence requires a specific Greek/Latin-driven reason recorded in the change. Alif is the product of 100+ days of real-user iteration — do not redesign it.** See `polyglot/CLAUDE.md` § "Ground design and code in Alif". Frontend (`frontend/`) talks to both; user picks via a Globe tab driven by `frontend/lib/language-context.tsx`. Plan: after ~6 weeks of dogfooding both languages, extract a shared `alif_core/` Python package (FSRS, acquisition, session builder) — premature now.
- **LLM Cost Tracking**: All litellm calls auto-logged via `limbic.cerebellum.cost_log` callback. Sync to local: `python -m limbic.cerebellum.cost_log sync`. Reports: `python -m limbic.cerebellum.cost_log report --days 7`.
- **Offline**: AsyncStorage sync queue for all mutable actions. Auto-prefetch, background refresh, 12s fetch timeout with stale-cache fallback. See `docs/frontend-files.md`.

## LLM Architecture
- **Claude CLI (`claude -p`)** is the primary LLM backend for ALL batch/background text tasks — free via Max plan. Integrated into `llm.py` as `claude_sonnet`/`claude_haiku` model overrides (default when no override specified). Also: `generate_structured()` + `generate_with_tools()` in `claude_code.py`. **For JSON responses, prefer `json_schema=` over `json_mode=True`** — uses `--json-schema` constrained decoding which guarantees valid JSON. Without it, CLI models wrap JSON in explanation text that can fail to parse (caused a major verification bug 2026-04-14).
- **Model routing**: `claude_sonnet` for sentence generation, `claude_haiku` for quality gate + enrichment + tagging + flags + disambiguation + verification. Story gen: Claude Opus via `claude_code.py` (retry loop).
- **Hybrid Codex provider (default since 2026-05-26)**: the `claude_haiku` alias routes through Codex `gpt-5.5` CLI first, then falls back to Claude CLI, then to the API chain. `claude_sonnet` (sentence + story generation) is never routed through Codex — the 2026-05-26 A/B (`research/codex-vs-claude-sentence-gen-2026-05-26.md`) showed Codex weaker on Arabic naturalness under vocab constraint. The flip on the audit/enrichment side is justified by `research/codex-vs-claude-enrichment-arabic-2026-05-26.md` (Codex canonical Arabic pattern names 9/9 vs Haiku 5/9; cultural notes 10/10 vs 3/10; 1.7× faster). Implementation: `backend/app/services/codex_cli.py` (Codex subprocess shim) + `_audit_provider()` / `_generate_via_codex_cli_with_logging()` in `llm.py`. Failover order for haiku calls: Codex CLI → Claude CLI → GPT-5.2 / Claude Haiku API. **Set `ALIF_AUDIT_PROVIDER=claude` to opt out** if Codex breaks. Plan: `research/alif-codex-migration-plan-2026-05-26.md`. Codex requires `codex` installed and authenticated (`codex auth`); on the prod server tokens live in `/opt/alif/.codex` (shared with polyglot). All `codex exec` calls pass `-c tools.web_search=false` — web search is on by default for gpt-5.6 models and made it cite dictionary URLs (`utm_source=openai`) inside stored learner-facing text (memory hooks, 2026-07-25).
- **Sentence generation defaults to the bounded legacy batch path** — `batch_generate_material` (and by delegation `generate_material_for_word`) uses one Sonnet generation call plus deterministic validation, batched mapping verification, and Haiku quality review. The tool-enabled self-correct session in `app/services/sentence_self_correct.py` is available only when `ALIF_USE_LEGACY_BATCH=0`; keep it off for production cron/background work until the empty structured-result failures seen on 2026-05-12 are fixed.
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
| `docs/discover-api-integration.md` | External-service integration guide for `/api/discover/*` (Dragoman-style vocab discovery from Arabic text) |
| `docs/nlp-pipeline.md` | NLP pipeline: clitic stripping, CAMeL Tools, morphology |
| `docs/review-modes.md` | Full UX flows for all review modes |
| `docs/scripts-catalog.md` | All import, backfill, cleanup, analysis scripts |
| `docs/design-principles.md` | Feature-level design decisions (lemma identity, intro cards, tashkeel, fonts, graduation, etc.) |
| `~/src/bookifier/bilingual/RUNBOOK.md` | Bilingual EPUB build pipeline (AR + tashkīl + faithful EN). Use when generating reader-grade bilingual material from raw Arabic text rather than studying it inside alif. |

## Review Modes
See `docs/review-modes.md` for full UX flows. Modes: Sentence-First Review (primary), Reading Mode, Listening Mode, Learn Mode, Story Mode, Quran Reading Mode (suspended 2026-04-07), Podcast Mode.

## Hard Invariants
These rules have all caused production bugs or data corruption when violated. For feature-level design details (intro cards, tashkeel, fonts, graduation tiers, etc.), see `docs/design-principles.md`.

- **FOUNDATIONAL: Every word in every sentence earns review credit** — when a sentence is reviewed, ALL non-function words get a review (acquisition or FSRS), regardless of whether they are the "target" word or collateral scaffold. This is the core learning mechanism. A word seen 10 times collaterally with correct ratings has been learned — the system must recognize this. No word should be invisible to the review engine. Encountered words that appear in reviewed sentences are auto-introduced to acquisition and get their first review immediately; Tier 0 instant graduation handles familiar words. **No artificial throttles on this flow** — with one exception (2026-05-15): the daily intro cap (`DAILY_INTRO_CAP=30`, enforced inside `start_acquisition()`) defers further encountered→acquiring promotions for the rest of the UTC day once 30 net-new acquisitions have been started. Cap-deferred words keep their `encountered` state, get `total_encounters` incremented on each appearance, and can be promoted on a later day. `leech_reintro` bypasses the cap.
- **Acquisition provenance is not episode type** — `UserLemmaKnowledge.source` records the durable curriculum/source (`book`, `textbook_scan`, `frequency_core`, etc.). `acquisition_episode_kind` records why the current acquisition episode began (`new` or `leech_reintro`). Never overwrite meaningful provenance to mark a leech restart, and never count an explicit `leech_reintro` episode as net-new intake. Historical NULL-kind rows are not safe to rewrite without a reviewed production dry-run.
- **Recovery permission uses strict debt + primary evidence** — the true-new `0/8/30` recovery budget activates at actionable/protected Box 1 ≥5, due Box 2 ≥30, or strict main-lane FSRS due ≥750. The FSRS count excludes function/inert rows and variants shadowed by known/learning canonicals. Permission is earned only from primary reading cards/accuracy, never collateral rows or passage child logs. Do not replace this with a raw due count or blended ReviewLog accuracy.
- **A reintroduced leech gets a fresh treatment episode** — lifetime `times_seen`/`times_correct` stay preserved, but suspension evidence for `acquisition_episode_kind='leech_reintro'` begins at `acquisition_started_at` and needs five fresh reviews before a verdict. Reintroduction admission is independently capped at 8/UTC day and closes at actionable Box 1 ≥20, due Box 2 ≥30, or strict main-lane FSRS due ≥750. Never let pre-treatment failures immediately re-suspend a word after a Good/Tier-E review.
- **Speculative prefetch must not change learning state** — calls with `prefetch=true` pass `allow_intro_mutations=False`: no ULK creation/promotion, acquisition start, or intro-budget consumption. A speculative response must also exclude a card that needs a cold-word promotion to preserve intro-first ordering. Stateful introduction happens only in a fresh non-prefetch build.
- **No bare word cards in review** — ONLY sentences. Generate on-demand or skip if no comprehensible sentence.
- **No LLM calls in session build critical path** — `build_session()` must stay fast (<1s). All LLM work happens at generation time or in `warm_sentence_cache` background tasks. A previous synchronous verification gate caused 30-60s timeouts (2026-03-17).
- **No on-demand sentence generation in session build** — sessions build entirely from pre-generated sentences (DB queries only, <1s). **`warm_sentence_cache()` (live, post-session background task) is the primary generator** — it produces the bulk of sentences for the focus cohort + acquiring-rescue + intro candidates. The 3-hourly cron (`deploy/alif-update-material.sh`) runs maintenance only (Step A generation is off, `--max-step-a-sentences 0`) plus `refill_due_deficit.py`, which covers the one gap warm-cache misses: FSRS-due words outside the focus cohort with zero reviewable sentences. The `material_jobs` coordinator queue was retired 2026-06-16 (never drained; see experiment-log). All paths still go through the single verified pipeline (`batch_generate_material` / `generate_material_for_word`).
- **All sentence generation must go through `generate_material_for_word()`** — this is the single verified pipeline: disambiguation -> LLM verification -> correction -> `mappings_verified_at`. Never create a separate generation path that skips verification — this was the source of 29 bad-mapping flags (2026-03-21 fix).
- **All import paths must call `run_quality_gates()`** — centralized post-creation pipeline in `lemma_quality.py`. Runs: finalize -> variant detection -> enrichment -> stamps `gates_completed_at`. **Model-level guard**: `select_next_words()` and `_build_reintro_cards()` filter out lemmas where `gates_completed_at IS NULL` — ungated lemmas never appear in sessions.
- **All text→lemma mapping must go through `build_comprehensive_lemma_lookup()` + `lookup_lemma()`** — the single hardened path (clitic stripping + CAMeL disambiguation + collision handling). Full Arabic identity comes before lossy normalization: the comprehensive lookup records unique vocalized/hamza-preserving identities, and correction of identity-sensitive grammatical tokens fails closed rather than collapsing أَنْ/إِنْ/إِنَّ-class lexemes onto one normalized bare. Bare hamzated `أن`/`إن` and prefixed `بأن`/`وإن`/`وأن`/`فأن`/`فإن` are contextual two-identity sets, never fixed aliases; unhamzated `ان`/`فان` are unresolved, exact madda `آن` stays lexical, and `بان` remains lexical. Productive attached-pronoun forms under the approved و/ف/ب prefixes canonicalize to base `أَنَّ`/`إِنَّ` while preserving `SentenceWord.surface_form`; legacy compound lemma rows do not steal those surfaces. Stored lexical `لأنّ` and its pronoun forms beat derived prefix aliases, exact `لِأَنْ` remains `أَنْ`, and unsupported ب+إن compositions fail closed. Contextless import/dedup and strict citation lookup must fail closed on every ambiguous base or prefixed form. Target spelling is only a candidate signal: map exact identity first, then recompute target flags after every verifier disambiguation/correction and reject publication if the required canonical target disappeared. Classify each token by the **resolved lemma's** attributes — `_is_function_word(lemma_ar_bare)` and `word_category in {proper_name, onomatopoeia}` — NEVER a surface-only check (it misses clitic-attached forms, e.g. بعضهم→بعض, inflating gaps) or a CAMeL POS guess for proper names (`word_category` is authoritative). Reference impl: `scripts/reading_readiness.py` `analyze()`. Source-specific normalization (e.g. `quran_frequency.normalize_qac_lemma`) still calls the shared `normalize_arabic`/`lookup_lemma` underneath. Don't hand-roll tokenize+normalize+classify for analysis/import/scan work (2026-06-03 lesson).
- **Every sentence_word must have a lemma_id at *display* time** — two distinct gates:
  - **Storage gate**: book/corpus imports MAY persist `SentenceWord` rows with `lemma_id IS NULL` for surface forms not in the vocabulary at import time, so authentic passages aren't lost. This is the only path allowed to do so.
  - **Reviewability gate**: every selection query that returns a sentence to the user adds `reviewable_sentence_clauses()` from `app/services/sentence_eligibility.py`. A sentence with any NULL lemma_id, missing `mappings_verified_at`, stale verification before `MAPPING_VERIFICATION_MIN_AT` (currently 2026-05-17 18:59, in `sentence_eligibility.py`), or any corpus lifecycle sentinel is invisible regardless of `is_active`: `2000-01-01` is a transient claim, `2000-01-02` is a durable inventory/mapping blocker, and `2000-01-03` is a durable completed linguistic-QA rejection. Ordinary rescue/reverification may retry the first, never the durable two. For `source in {"book", "corpus"}`, a completed quality review is also fail-closed: both `quality_natural` and `quality_translation_correct` must be exactly true; legacy authentic rows with no completed review retain their historical eligibility.
  - **Healing**: `update_material.py` step 0b runs `fix_null_lemma_ids.remap_unmapped_sentence_words` every cron pass — re-tries comprehensive lookup, and auto-creates `word_category="proper_name"` lemmas for residual unmapped surface forms detected as proper names. New common-word imports activate stuck sentences automatically on the next pass. Mapping uses `build_comprehensive_lemma_lookup()`.
  - **Proper-name lemmas** are inert: filtered from `word_selector` (no auto-introduction), excluded from comprehensibility scaffold count in `sentence_selector`, and skipped in `sentence_review_service` (no FSRS / acquisition credit). They exist solely so the SentenceWord row carries a real `lemma_id`. **Filter sites all key on `word_category='proper_name'`, not on `pos='noun_prop'`** — these are independent fields, and CAMeL-driven imports populated only `pos`. A `before_insert` listener in `models.py` (2026-05-06) now forces `word_category='proper_name'` whenever `pos='noun_prop'` and category is NULL, so the two fields can't drift apart at row creation. Past leak: 101 lemmas including Thameena, Al-Razi, Bakr, Zakariya appeared as full intro cards before the fix.
- **Authentic corpus enrichment is always scoped, inventory-closed, and two-phase** — use `update_material.py --only-corpus-enrichment` (or the explicitly opted-in full-workflow Step A2) with `--kind` and/or repeatable `--corpus-sentence-id`; kind and IDs intersect. A bounded deterministic preflight advances a scope-specific cursor, exposes prospective mapping/inventory risk before LLM calls, and skips guaranteed-incomplete rows. Preparation performs early Arabic/translation QA, requires an explicit verifier verdict for every row and ambiguity, retries only the exact unavailable/invalid row, requires gated lemmas for both lookup and correction, and uses content + mapping-state compare-and-set writes across every external call so stale verdicts cannot overwrite concurrent text or `SentenceWord` work. A concurrent edit preserves the external work, keeps the row inactive, and independently clears only an unchanged mapping or QA stamp; a translation write also guards the exact Arabic sent to the provider. Enrichment writes Arabic/transliteration only when tashkīl was missing and translation only when translation was missing—provider echoes may never replace an existing field. It never creates corpus vocabulary—not even proper-name or frequency-core rescue lemmas. Inventory/mapping failures become the durable Jan-2 blocker; completed linguistic QA failures become Jan-3; a target that becomes suspended during external work is a transient retry and preserves any prior Jan-2 disposition. Only explicitly named Jan-2 rows may be retried with `--corpus-retry-blocked` after separately confirmed source/inventory curation. Successful rows remain inactive. Preparation and activation limits must never both be nonzero: prepare with `--corpus-activate-limit 0`; activate only with `--corpus-limit 0`. Once a `source in {"book", "corpus"}` row has completed QA, **only this governor may activate it**—due-dense salvage and green-page book reactivation exclude it. Activation defaults to zero, is capped at 20, and treats the read-only plan as advisory: one short SQLite writer boundary reloads live capacity plus selected-lemma demand, then planned parent/mapping snapshots and the ceiling are checked before visibility. It blocks acquiring/no-FSRS-demand rows, never retires material, and invalidates stale derived stamps rather than trusting a changed row on the next pass. Never enable corpus cron globally or bulk-reset durable sentinel debt.
- **No auto-created lemmas from corrections, with one frequency-gated exception** — `correct_mapping()` and flag resolution only use existing DB lemmas. If the correct lemma isn't in the vocabulary, the sentence is rejected/retired. This prevents orphan lemmas that bypass quality gates. **All correction application must go through `apply_corrections()`** in `sentence_validator.py` — the single shared function for the correct_mapping → 3-way check pattern. If corrected mappings remove the stored primary target, repair `Sentence.target_lemma_id` atomically from the uniquely corrected former-target word; preserve multi-target flags, fail closed when the replacement is ambiguous, require that the canonical primary is represented before every fresh verification stamp, and never rewrite historical `ReviewLog` observations. Never duplicate this loop inline; the "fix one site, miss the clones" pattern caused 63 bad corpus sentences (2026-04-16). **Exception (2026-05-13, hardened 2026-07-28)**: `mapping_rescue.py` may create a Lemma when the verifier proposal matches a `FrequencyCoreEntry` row whose `lemma_id IS NULL` — but only via `_try_frequency_gated_proposal()`. Linking the row uses a conditional compare-and-set as the portable cross-worker claim (`FOR UPDATE` alone is insufficient on SQLite); mapping rescue excludes the claimed lemma until `run_quality_gates()` has persisted `gates_completed_at`. An interrupted claim remains invisible and Step G2 heals it later. Proposals without an FCE match are logged and the sentence stays stale. This is the only sanctioned auto-create path; do not generalise it elsewhere without an equivalent vocabulary-driven gate.
- **No words without English gloss — EVER** — Three validation gates: (1) `generate_material_for_word()` rejects sentences where any lemma has empty `gloss_en`. (2) Quran 6-layer fallback pipeline. (3) Frontend cache bypass when cached result has no `gloss_en`. Tests: `test_gloss_coverage.py`.
- **Canonical lemma is the unit of scheduling** — variant forms tracked via `variant_stats_json` but never get independent FSRS cards or `UserLemmaKnowledge` rows. **Multi-hop chain resolution**: variant chains (A->B->C) are followed to the root canonical everywhere. Bug fix (2026-03-23): single-hop resolution caused variants to be introduced despite root canonical being known. Bug fix (2026-05-06): 36 variant ULKs accumulated in prod because `start_acquisition()` and direct `db.add(UserLemmaKnowledge(...))` sites bypassed the redirect — review credit went to canonical (correct) but variant's own box never advanced, so the same sentence kept reappearing as a "Rescue" card forever. Now enforced via `app/services/canonical_resolution.py`: `start_acquisition()`, `introduce_word()`, `book_import_service`, `ocr_service`, and the `build_session()` scheduler all redirect via `resolve_canonical_lemma_id` / `resolve_canonical_via_map` before any ULK creation or due-list inclusion. **When adding a new ULK-creation path, redirect at function entry — don't trust callers.**
- **Exact-form intervention may select context, never create a form schedule** — `__exact_surface_v1` can reserve at most one already-due reading sentence for the canonical lemma and failed normalized surface. It must not create/advance a card, change the learner's Hard rating, operate in listening/acquisition mode, reuse the trigger sentence, overlap unresolved episodes on one lemma, or compare arms only among exact-form deliveries; preserve the any-form intention-to-treat endpoint and undo integration.
- **Surface/tashkeel diagnosis never discounts review validity** — word-evidence protocol v1 records the exact `SentenceWord` token, actual initial/front/back tashkeel exposure, product rating, and optional rating-2 causes. It is diagnostic only: the canonical lemma rating remains authoritative, and primary/collateral words remain equally valid. Do not create a form/tashkeel schedule or cause-specific FSRS mapping without version-segmented replay and an experiment-log entry. Analyses must include successful unvocalized tokens as the denominator and must not infer historical visibility from `show_tashkeel`.
- **Active story is not primary curriculum** — an imported story receives `_TIER_STORY_IMPORTED` only with `Story.metadata_json.curriculum_role='primary'`. `status='active'` means reader-visible and must not by itself redirect high-priority intake. Do not mark a story primary without an explicit learner target and a mapping/readiness review.
- **Verification failure != success** — `verify_and_correct_mappings_llm()` returns `None` on LLM failure (distinct from `[]` = verified OK). `batch_verify_sentences(..., return_invalid_rows=True)` keeps top-level/cardinality/index-ownership failures batch-fatal but returns an explicit `invalid_reason` marker for semantic failures attributable to one trustworthy row. Every opt-in caller must explicitly skip/retry that marker; it is never a clean verdict. Callers discard/skip sentences that can't be verified.
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

Events of note:
- `session_start` — fired by `/api/review/next-sentences` when `prefetch=False`. Carries `total_due_words`, `covered_due_words`, `sentence_count`. Use to audit session size.
- `card_shown` — fired by the frontend whenever a card transitions onto the user's screen (intro / sentence / verse / reintro / grammar / wrapup). Carries `card_type`, `session_id`, `lemma_id`/`sentence_id`, `card_index`, `total_cards`. Lets the analyzer reconstruct the exact card sequence the user experienced — invisible to ack-only events when cards are auto-skipped, re-rendered, or replaced by a background prefetch.
- `sentence_review` / `experiment_intro_shown` / `auto_introduce` / `word_graduated` / `leech_suspended` — ack-driven; fire on user action. `sentence_review` carries `parent_card_type` (`"passage"`/`"sentence"`/`"wrapup"`, added 2026-05-13) plus word-evidence protocol/count/saved summaries (2026-07-27); exact token-level surface/tashkeel/cause evidence lives in `word_review_evidence`.

### 3. Testability — Claude Must Be Able to Test Everything
- All logic in the API, never in the UI. Every service has pytest tests, every endpoint testable with curl.
- Web preview via `npx expo start --web`. Mock data in `frontend/lib/mock-data.ts`.

### 4. Skills — Generate and Update
Create reusable Claude Code skills (`.claude/skills/`) for common operations.

### 5. Experiment Tracking — Document Everything
- `docs/scheduling-system.md`: Update on ANY scheduling change
- `research/experiment-log.md`: Add entry BEFORE algorithm changes
- `research/experiment-log.md` is **append-only** — NEVER delete existing entries. New entries go at the top, directly **below the `ENTRIES (newest first)` marker** (i.e. after the header + the `📑 Index by area`, never above the index). When an entry becomes the definitive reference for an area, add/update its line in that index.
- `research/research-hub.html`: Update when adding new research documents (add doc-row entry in appropriate category section)
- `research/README.md`: Update when adding new research documents
- `research/analysis-YYYY-MM-DD.md`: Save analysis findings
- **All reports and analysis HTML pages go inside the repo** (in `research/`), not in external dirs like `~/.agent/diagrams/`. Link them from the experiment log entry that prompted them.
- **Update `CLAUDE.md` itself and `docs/` after EVERY change** — new/renamed services, endpoints, scripts, tables, constants, architecture. Stale docs are the user's #1 documented frustration (complained 15+ times). Do this proactively, without being asked.

### 6. Git Diff Discipline — Prevent Silent Reverts
**CRITICAL**: Before every commit, run `git diff --stat HEAD` and review what changed. Watch for:
- **Append-only files shrinking** (`experiment-log.md`, `IDEAS.md`) — this means entries were deleted. NEVER acceptable.
- **Large service files with net deletions** — if `sentence_selector.py` or similar core files show significant line removals, verify those removals are intentional, not regressions.
- **Schema files losing fields** — if `schemas.py` or `types.ts` show removed fields, verify the backend doesn't still compute them.
- **When replacing/rewriting a file**, always diff the old version against the new one to check nothing was lost: `git diff HEAD -- path/to/file`
- **Bundled commits are dangerous** — if a commit touches >5 files across different features, split it or review each file's diff individually.

### 7. Branch Workflow for Non-Trivial Changes — Self-Review Gate
**The user does not review PRs — Claude owns self-review end-to-end.** Do NOT pause after opening a PR to ask the user to review; do NOT ask for approval to merge. Self-review and merge yourself.

For changes that touch core algorithm files (`sentence_selector.py`, `session_builder`, `fsrs_service.py`, `acquisition_service.py`) or modify >3 files:
1. Create a branch: `git checkout -b sh/<feature-name>`
2. Make changes and commit on the branch
3. Push main first if there are unpushed commits on main, so the PR diff is scoped to just the new work
4. Create a PR: `gh pr create --title "..." --body "..."`
5. **Self-review the PR diff** by reading the actual diff (`gh pr diff <N>`) — not just the stats. Verify:
   - No unintended deletions in append-only files (`experiment-log.md`, `IDEAS.md`)
   - No features silently removed from large files
   - No schema fields lost that the backend still computes
   - Net line counts make sense (a "feature addition" shouldn't have large net deletions)
   - Logic equivalence in refactors (trace through the new code vs old behavior)
6. If the self-review passes, merge immediately: `gh pr merge <N> --squash --delete-branch`
7. If issues found, fix on the branch, push, and re-review. Never merge with known issues.

After merge: `git checkout main && git pull --ff-only`. Deploy only if the user explicitly asked for it — merging and deploying are separate decisions.

Direct commits to `main` are OK for: documentation-only changes, single-file bug fixes, test additions, and changes the user explicitly asked to deploy immediately.

### 8. Gate Audit on Lifecycle Changes
When changing how words move between states (encountered -> acquiring -> FSRS) or adding new flows that alter word states, **audit every gate and filter that operates on those states**. Gates include: comprehensibility gate (x2), unknown scaffold cap, pipeline backlog gate, focus cohort, variant resolution, intro card filter, listening readiness, function word exclusion. The full gate registry is in `docs/scheduling-system.md` §19.17. Lesson learned: the collateral credit change (2026-03-18) broke sessions because the comprehensibility gate wasn't updated for the new box-1 acquiring words it created.

### 9. Code Style
- Python: type hints, pydantic models for API schemas
- TypeScript: strict mode, functional components
- No test plans or checklists in PR descriptions
- Branch prefix: `sh/` for all GitHub branches
- **Never leak the user's other projects into Alif** — don't put paths, keys, or details from unrelated projects (tana, polaris, etc.) into Alif docs, scripts, or commit messages. If a borrowed key/config is needed, use it silently; don't document its origin. (The bookifier RUNBOOK link in Reference Docs is a deliberate, sanctioned dependency — this rule is about not introducing *new* cross-project leakage.)

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
- Autoflush counts as flush — **any `db.query(...)` in SQLAlchemy triggers autoflush**, so a dirty-session + query + LLM-call sequence holds the lock through the LLM call just as surely as an explicit flush. Commit before the query if the next step is slow.
- Background tasks (`BackgroundTasks.add_task`) must not receive the request's `db` session
- Long-running scripts must commit between steps, not hold one session for the entire run
- Loops that do "write → LLM → write" per iteration must `db.commit()` at the end of each iteration, not at the end of the loop — one iteration's dirty state autoflushes at the next iteration's query
- Non-critical writes (cache updates, counts) should use try/except with rollback so lock contention doesn't crash read endpoints
- Functions that mix LLM + DB writes: prefer splitting into `validate_*(db, ...)` (LLM + read-only queries, returns validated data) + `write_*(db, ...)` (pure DB write) so callers can run validate-all → write-all, keeping the lock released during validation. See `validate_multi_target_sentence` / `write_multi_target_sentence` in `material_generator.py`.

**Past incidents**:
- 2026-03-29: `store_multi_target_sentence` held write lock 30-60s during LLM verification (broke OCR uploads). `_import_unknown_words` held lock during batch translation. Chat endpoint held session during 15s LLM call.
- 2026-04-17: 2-hour `update_material.py` hang + `sqlite3.OperationalError: database is locked` in web backend. Four sites fixed: `store_multi_target_sentence` split into `validate_multi_target_sentence` + `write_multi_target_sentence`; `enrich_corpus_sentences`, `create_book_sentences`, and `_verify_new_story_mappings` got per-iteration `db.commit()`. The recurrence despite the 2026-03-29 fix was because the dirty-session-before-query-autoflush path wasn't as obvious as explicit `db.flush()`; checklist above now calls it out.

### 11. Frontend-Backend Type Sync — Verify at Runtime Boundaries
TypeScript types (`frontend/lib/types.ts`) can declare fields the backend never sends. `tsc` passes because the types are structurally valid, but `.field.map()` crashes at runtime when `field` is `undefined`. When changing backend response schemas:
- Check that `schemas.py` Pydantic models match `types.ts` TypeScript interfaces
- If removing or renaming a backend field, grep for it in `frontend/`
- Frontend mock data (`frontend/lib/mock-data.ts`) must also stay in sync

### 12. Commit Incrementally in Long Sessions
5+ sessions lost significant work because uncommitted changes were lost during context compression or git operations. For any session touching >2 files:
- Commit working intermediate states (even if not deploy-ready) — you can squash later
- Never `git stash` and continue other work — the stash will be forgotten
- Never `git reset --hard` to debug — use `git stash` + `git stash pop` or a branch

### 13. Fallback Chains Can Silently Mask Failures
The LLM fallback chain (CLI Sonnet -> CLI Haiku -> API Haiku) and model routing mean failures in the primary model silently degrade to a weaker model. This masked the CLI JSON parsing bug for weeks (1,225 wasted calls) and the un-deployed migration for 3 days (3,600+ wasted Gemini calls). When debugging quality issues:
- Check LLM call logs: `ssh alif "ls -la /opt/alif/backend/data/logs/llm_calls_*.jsonl | tail -3"` then inspect which models are actually being used
- A high rate of fallback calls means the primary model is failing silently

### 14. Investigation Discipline in Iterated Areas
Before proposing any fix in an area with a long change history (generation pipeline, FSRS scheduling, sentence selector, lemma quality, validator), the first three reads are mandatory — not aspirational:
- `git log --since="3 months ago" --oneline -- <file>`
- `grep -i <topic> IDEAS.md docs/scripts-catalog.md research/experiment-log.md`
- `ls backend/scripts/ | grep -i <related-keyword>`

If a file has 10+ commits in the last 3 months, the most likely shape of an issue is "previous fix didn't fully close" or "operational gap (script exists, isn't being run)" — not "new bug." Skipping these has produced re-proposals of already-tried fixes (~5 documented occurrences). Confirming "this looks new" before going further is cheap; re-doing rejected work is expensive. The 2026-05-03 generation-pipeline investigation re-proposed weakening the `same_lemma` rejection (load-bearing per 4 prior commits + a docstring guard) before catching itself.

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

**Deploy discipline — ALWAYS deploy from `main` (a prod incident on 2026-05-29 came from violating this):**
1. **Merge to `main` first.** Every change ships via PR → squash-merge to `main` → `git push`. Never deploy from a feature branch; never leave prod checked out on one. (On 2026-05-29 prod was silently on an unmerged feature branch incl. a "WIP untested" commit, so a bare `git pull` no-op'd and the restart ran stale code.)
2. **Force the server onto `main` and verify HEAD** — a bare `git pull` on a non-`main` branch merges the wrong upstream or no-ops. Every deploy starts with:
   ```bash
   ssh alif "cd /opt/alif && git checkout main && git pull --ff-only && git log --oneline -1"
   ```
   Confirm the printed HEAD is the commit you just merged before restarting anything.
3. **Verify the effect, not the status line.** `systemctl is-active` reports "active" even when stale code/schema is running. Confirm the real change landed (new column exists via `sqlite3 ... pragma_table_info`, endpoint returns expected data, etc.).
4. **Back up the DB before any data-touching step** (migrations that rewrite rows, backfills): `ssh alif "cp <db> /opt/<app>-backups/<name>_$(date +%Y%m%d_%H%M%S).db"`.

```bash
# Deploy alif backend (venv + systemd, no Docker)
ssh alif "cd /opt/alif && git checkout main && git pull --ff-only && git log --oneline -1 && cd backend && .venv/bin/pip install -e . --no-deps -q && systemctl restart alif-backend"

# If alif dependencies changed in pyproject.toml:
ssh alif "cd /opt/alif/backend && .venv/bin/pip install -e . -q && systemctl restart alif-backend"

# Deploy POLYGLOT backend (separate systemd service `polyglot-backend` + venv at
# /opt/alif/polyglot/.venv, port 3002). No Alembic — additive schema deltas in
# app/database.py `_ADDITIVE_COLUMN_DELTAS` auto-apply via ensure_schema() on
# startup, so a restart picks up new columns/indexes. Verify the column/effect after.
ssh alif "cd /opt/alif && git checkout main && git pull --ff-only && git log --oneline -1 && systemctl restart polyglot-backend && systemctl is-active polyglot-backend"
# If polyglot dependencies changed: ssh alif "cd /opt/alif/polyglot && .venv/bin/pip install -e . -q && systemctl restart polyglot-backend"

# Deploy frontend — TWO targets since 2026-07-15:
#
# (a) iOS app (primary): standalone EAS "preview" build with expo-updates (OTA).
#     The JS bundle is embedded — the app launches instantly offline, NO Metro
#     dependency. UI changes ship over the air from the local checkout (merge to
#     main + pull first so you publish exactly what's merged):
cd frontend && eas update --channel preview --message "<what changed>"
#     The phone picks the update up in the background on next launch and applies
#     it the launch after (fallbackToCacheTimeout=0 default). NATIVE changes
#     (new native module, permissions, icon, app.json ios/android keys) do NOT
#     ship via OTA: bump "version" in app.json (runtimeVersion policy is
#     appVersion — the bump is what stops old binaries receiving incompatible JS),
#     then rebuild + reinstall:
#       cd frontend && eas build --platform ios --profile preview
#     Install from the expo.dev build page link on the phone. Ad-hoc profile
#     expires Mar 2027; device UDID already registered.
#
# (b) Web version (+ legacy Expo Go): still the Hetzner Metro dev server.
# NOTE: a bare `systemctl restart alif-expo` keeps Metro's transform cache and can
# serve a STALE bundle (frontend code changes silently don't appear; symptom seen
# 2026-05-25 — a screen showed the wrong language's data though source + backend
# were correct). Clear the Metro cache too, then hard-reload the client:
ssh alif "cd /opt/alif && git checkout main && git pull --ff-only && rm -rf /tmp/metro-* /tmp/haste-map-* /opt/alif/frontend/node_modules/.cache /opt/alif/frontend/.expo && systemctl restart alif-expo"

# Full deploy (alif backend + frontend):
ssh alif "cd /opt/alif && git checkout main && git pull --ff-only && cd backend && .venv/bin/pip install -e . --no-deps -q && systemctl restart alif-backend && rm -rf /tmp/metro-* /tmp/haste-map-* /opt/alif/frontend/node_modules/.cache /opt/alif/frontend/.expo && systemctl restart alif-expo"

# Cron wrapper:
# scripts/deploy.sh links /opt/alif-update-material.sh to deploy/alif-update-material.sh
# from the checked-out Git main. Do not scp cron wrappers to production.

# Web URL (always display after a web deploy):
# http://alifstian.duckdns.org:8081
# iOS: standalone app (EAS preview build + OTA) — exp:// URL no longer used on the phone
```

Server-side config that lives outside the app process (cron wrappers, etc.) is versioned under `deploy/`. The cron wrapper at `/opt/alif-update-material.sh` is the authoritative supply-chain entry for daily intros — it sets `ALIF_RUN_CRON_PREGENERATION=1`, `ALIF_RUN_CRON_LEMMA_ENRICHMENT=1`, `ALIF_FREQ_CORE_INTAKE_MAX_RANK=3000`, `ALIF_FREQ_CORE_INTAKE_LIMIT=10` so the intake script can keep filling the top-frequency lemma pool past rank 1000. Without these, intros silently dry up once you graduate past the easy tier. See `deploy/README.md` and the 2026-05-13 experiment-log entry.

## Server Operations — MUST READ
See `.claude/skills/server-ops.md` for full details. Summary of hard-won rules:

1. **ALL `ssh` commands require `dangerouslyDisableSandbox: true`** — SSH is always blocked by local sandbox. Never try without it.
2. **For remote Python scripts > 2 lines**: write to `/tmp/claude/script.py`, then `scp alif:/tmp/` and run with `ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 /tmp/script.py"`.
3. **Read `backend/app/models.py` BEFORE writing DB queries** — Don't guess table/column names. They've caused repeated failures (e.g., `lemma` vs `lemmas`, `query()` vs `get()`).
4. **Check `backend/scripts/` before writing ad-hoc queries** — Existing scripts cover most analytics and maintenance tasks.
5. **One deploy per session** — Get code right locally (tests pass), then deploy once. Multiple deploys waste time and risk inconsistent state.
6. **Push before deploy** — `git push` BEFORE running deploy commands. The deploy does `git pull` on the server — if you haven't pushed, the server pulls stale code. This has failed 5+ times.
7. **`limbic` install on server** — `pyproject.toml` specifies `limbic @ git+https://...` which tries GitHub clone. On server, install from local: `.venv/bin/pip install -e /opt/limbic` first, then `pip install -e . --no-deps` for alif, then remaining deps separately. CPU-only PyTorch: `pip install torch --index-url https://download.pytorch.org/whl/cpu`.
8. **Long-running remote scripts: use nohup** — SSH drops after ~60s idle, causing exit code 255 with no output. Use `nohup ... > /tmp/script.log 2>&1 &` then check the log file later. See `server-ops.md` for pattern.
9. **Backup DB before manual data changes** — `ssh alif "cp /opt/alif/backend/data/alif.db /opt/alif-backups/alif_pre_fix_$(date +%Y%m%d_%H%M%S).db"` then log the action via `scripts/log_activity.py`.

## Periodic Maintenance Checks

Run these every month or two when working on alif. Append a date + result line to each checklist entry below after running.

- **Check forks for upstream-worthy work** — `gh api repos/houshuang/alif/forks --jq '.[] | {full_name, pushed_at, created_at}'` then for each fork: `gh api repos/houshuang/alif/compare/main...OWNER:main --jq '{ahead_by, behind_by}'`. Only forks with `ahead_by > 0` are worth inspecting. A fork where `pushed_at < created_at` has zero new commits. Note: `gh` in the Claude Code sandbox hits TLS error `OSStatus -26276`; use sandbox-disabled bash.
  - 2026-04-24 — 1 fork (`eurunuela/alif`), 0 ahead, 119 behind. Nothing to merge.
