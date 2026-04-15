# Memory

## Algorithm Redesign (2026-02-12) — Deployed
- **Status**: Backend + frontend complete, deployed to production. OCR cards reset (393 -> encountered).
- **Entry point**: `research/README.md` (read-order index of all files)
- **Scheduling reference**: `docs/scheduling-system.md` — complete doc covering word lifecycle, session building, all constants, divergence analysis. **Must be updated on any scheduling change.**
- **Key files**: `research/learner-profile-2026-02-12.md` (user interview), `research/deep-research-compilation-2026-02-12.md` (8-agent deep research), `research/learning-algorithm-redesign-2026-02-12.md` (original plan + codebase change points)
- **Core change**: Three-phase word lifecycle: Encountered -> Acquiring (Leitner 3-box: 4h->1d->3d) -> FSRS-6
- **North star metric**: genuinely known words growing week over week
- **py-fsrs**: v6.3.0, pinned `>=6.0.0`. Same-day review support via w17-w19.

## Local Dev Environment
- **Use `python3`** not `python` — macOS `python` resolves to Python 2.7
- Run tests: `cd backend && python3 -m pytest`

## Backups
- **Server-side**: cron every 6h, script at `/opt/alif-backup.sh`, backups in `/opt/alif-backups/`
- **Local**: `./scripts/backup.sh` pulls DB + logs to `~/alif-backups/`
- **Retention (GFS)**: daily 7 days, weekly (Sundays) 4 weeks, monthly (1st) forever
- **Log rotation**: compress after 7 days, delete after 90 days
- **Restore**: `scp backup.db alif:/tmp/ && ssh alif "cp /tmp/backup.db /opt/alif/backend/data/alif.db && systemctl restart alif-backend"`

## Deployment Lessons (non-obvious gotchas)
- Fresh DB + alembic: if initial migration is empty (pass), `create_all` then `stamp head`. Or better: consolidate into one real migration.
- `ALIF_SKIP_MIGRATIONS=1` when generating new alembic revisions
- LLM model names: use `gemini/gemini-3-flash-preview` (not `gemini-3-flash`), `gpt-5.2`, `claude-haiku-4-5`, `claude-opus-4-6`, `claude-sonnet-4-5-20250929`
- Anthropic returns JSON wrapped in markdown fences — strip with regex before json.loads()
- Code expects `ANTHROPIC_API_KEY` and `ELEVENLABS_API_KEY` (see .env.example). User's `.env` uses `ELEVENLABS_KEY` (no _API suffix). The TTS-only key (`sk_2cea...`) can generate audio but NOT manage voices. Full-permission key (`sk_6952...`) needed for voice cloning API.
- TTS model: `eleven_multilingual_v2` with learner-tuned pauses (stability 0.85, similarity 0.75)
- SQLite busy_timeout: 30s. `warm_sentence_cache` has concurrency guard (threading lock prevents overlapping runs). Chat + story detail commits are best-effort to survive lock contention.

## Activity Logging
- **All batch scripts log to ActivityLog** via `app.services.activity_log.log_activity()`
- **Manual logging from Claude**: `ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 scripts/log_activity.py EVENT_TYPE 'Summary text' --detail '{\"key\": \"val\"}'"`
- **When doing manual backfills/fixes from Claude, always log the action** using the CLI tool above
- Event types: `manual_action`, `material_updated`, `sentences_generated`, `audio_generated`, `sentences_retired`, `frequency_backfill_completed`, `grammar_backfill_completed`, `examples_backfill_completed`, `variant_cleanup_completed`, `flag_resolved`
- Logs visible in app's More tab -> Activity section

## CRITICAL: Never Reference User's Other Projects
Never mention paths, keys, or details from user's other projects (e.g. tana, polaris, bookifier) in Alif documentation, scripts, or commit messages. If a key or config is found in another project, use it silently but don't document where it came from.

## CRITICAL: Always Update Docs After Changes
The user has complained 15+ times about Claude forgetting to update docs. This is the #1 source of frustration.
After EVERY implementation change, proactively update:
- CLAUDE.md (data model, new services, new endpoints, new scripts, architecture changes)
- research/experiment-log.md (algorithm changes, data changes)
- IDEAS.md (any new ideas discovered)
DO NOT wait to be asked. DO NOT skip this. The user considers this a critical failure when missed.

## Context Window Management
Long sessions that touch multiple features (UI + backend + deploy + data analysis) frequently blow the context window (~20 sessions had to be continued from context overflow).
Prefer focused sessions. If a task has 4+ distinct parts, suggest breaking into separate sessions.

## Voice Cloning & TTS (2026-03-23)
- [Details](project_voice_cloning.md) — PVC/IVC voice IDs, ظ/ض issue, TTS alternatives, PVC API multi-step process

## Podcast System (2026-03-22)
- [Details](project_podcast_system.md) — Passive listening podcast with 6 format variants, ElevenLabs TTS, segment caching

## Box-1 Starvation Bug (fixed 2026-03-14)
- [Details](project_box1_starvation_bug.md) — Fixed with NEVER_REVIEWED_BOOST (5.0x) in sentence_selector.py

## Mapping Correction Pipeline (operational)
- [Details](project_mapping_correction_review.md) — verify+correct via Claude Haiku CLI, deployed and working

## User's Arabic Learning Goal
- [Details](user_arabic_learning_goal.md) — Classical literature breadth, not just MSA. Quran, commentaries, medieval poetry (Sicilian Arabic poets), full literary tradition.

## Target vs Collateral Words Are Equal
- [Details](feedback_target_collateral_equal.md) — No distinction between target and collateral words for learning, credit, or intro cards. Repeated user feedback.

## Intro Card Overload (fixed 2026-03-30)
- [Details](feedback_intro_card_overload.md) — Fixed: interleaved among sentences, dynamic cap. Never front-load.

## Arabic Educational Reference Pages
- [Details](reference_arabic_educational_pages.md) — Index of 4 standalone HTML pages: Quran function words, ligatures, Quranic marks, font comparison. All in research/, indexed in research-hub.html.

## Claude CLI Migration Lesson (2026-04-03)
- [Details](project_llm_cli_monitoring.md) — Lesson: code committed locally != deployed. Push-before-deploy rule.

## Dirty Lemma Cleanup (done 2026-04-06)
- [Details](project_dirty_lemma_cleanup.md) — Fixed: LLM-powered cleanup in import_quality.py + one-off script. 41 cleaned.

## Batch Sentence Generation (2026-04-06)
- [Details](project_batch_sentence_generation.md) — 15 words in 2 CLI calls (~4s/word vs 30s/word). Also fixed broken pipeline (gemini defaults, same-lemma kill, GPT-5.2 fallback)

## Learner Review (2026-04-05)
- [Details](project_learner_review_2026_04_05.md) — 1,279 FSRS words, 91.3% retention, pipeline deficit (90/142 acquiring words have no sentences), textbook scans bypass backlog gate

## DB Query Gotchas
- [Details](feedback_db_queries.md) — Always read docs/data-model.md before ad-hoc scripts; key table/column name gotchas

## Ask Before Changing Design Decisions
- [Details](feedback_ask_before_changing.md) — Don't change intentional architecture (e.g. API vs CLI for chat speed) without asking first

## History + PKM Research / Petrarca Integration (2026-04-07)
- [Details](reference_history_pkm_research.md) — 12 historians using PKM tools, Petrarca integration plan. Key: Graham's KG repos, Zotero Translation Server, Hypothesis API, discourser novelty detection.

## CLI JSON Parse Bug (fixed 2026-04-14)
- [Details](feedback_json_schema_cli.md) — Always use `json_schema=` not `json_mode=True` for CLI models. Old parser silently dropped Sonnet's answers, fell back to weak API Haiku.

## Hindawi Corpus Import (deployed 2026-04-11)
- [Details](project_corpus_import.md) — 10,781 sentences from 166 children's books (72% lemma coverage). Sentence-only import (no new lemmas). On-demand translation via cron step A2. Also fixed preposition+pronoun function words.

## Spanish Pilot for Norwegian School (2026-04-15)
- [Details](project_spanish_pilot.md) — Standalone `spanish-pilot/` subproject; UX-validation prototype for Spanish-Norwegian word-level SRS. Separate SQLite/systemd/port 3100 on Hetzner. Norwegian UI, NO English allowed.

## Architecture Notes (not in CLAUDE.md)
- FSRS stability floor: "known" with stability < 1.0 -> "lapsed"
- Interaction logger: skipped when TESTING=1 (set in conftest.py)
- Frontend tests: `cd frontend && npx jest --watchman=false` (Jest + ts-jest, mocks for AsyncStorage/expo-constants/netinfo)
- Import dedup: all scripts use `resolve_existing_lemma()` for clitic-aware dedup (catches و-prefix, possessives, al-prefix)
- Hamza normalization: preserve in storage, normalize at lookup/comparison time only
- Sentence generation: rejected word feedback + collocate auto-introduction on failure
- **CRITICAL: SQLite naive datetime pitfall**: SQLite stores ALL datetimes as naive strings. Every datetime comparison in Python must either use naive datetimes (`datetime.utcnow()`) or convert DB values with `.replace(tzinfo=timezone.utc)` before comparing to aware datetimes. This affects: FSRS `reviewed_at` replay, `acquisition_next_due` comparison, FSRS due date comparison. Has caused production crashes 3 times.
