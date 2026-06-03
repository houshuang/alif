# Memory

Index of durable memories — one line per file in this directory, grouped by intent. **Always-loaded project rules live in `CLAUDE.md`, not here.** Orientation at start of work: `docs/scheduling-system.md` (word lifecycle + all constants), `research/README.md` (research read-order), `research/experiment-log.md` (append-only lab notebook, newest-first), `IDEAS.md` (master idea list). North-star metric: genuinely-known words growing week over week.

## Working process & preferences (how to work)
- [PR self-review & merge without asking](feedback_pr_self_review_merge.md) — own the full PR lifecycle; self-review IS the gate; never pause for merge approval. Deploy is a separate decision.
- [Check prior work before pipeline fixes](feedback_check_prior_work_first.md) — for long-iterated areas: `git log -3mo` + grep IDEAS/scripts-catalog/experiment-log + `ls scripts/` BEFORE drafting. CLAUDE.md Rule #14.
- [Verify before recommending](feedback_verify_before_recommending.md) — check crontab before "you should run X"; check denominator symmetry before quoting a ratio.
- [Ask before changing design decisions](feedback_ask_before_changing.md) — don't alter intentional architecture (e.g. API-vs-CLI for chat speed) without asking first.
- [Prefer in-session work over `claude -p`](feedback_in_session_vs_cli_subprocess.md) — for transforms I can do directly (vocalize/translate/align/classify), write the output myself; don't delegate to a subprocess.
- [Don't trust bg-task exit 0 alone](feedback_bg_task_exit_code_misleading.md) — tail the output even on "passing" notifications.
- [Prefer focused sessions](feedback_focused_sessions.md) — multi-feature sessions repeatedly blow the context window; split 4+-part tasks.
- [DB query gotchas](feedback_db_queries.md) — read `docs/data-model.md` before ad-hoc scripts; key table/column name traps.
- [gh CLI / Go-binary TLS error in sandbox](feedback_gh_sandbox_tls.md) — `OSStatus -26276` = sandbox blocking trustd; retry with `dangerouslyDisableSandbox: true`.

## Deploy & ops (how to ship safely)
- [Always deploy from main](feedback_always_deploy_from_main.md) — verify server branch + HEAD commit + the actual effect, not just `systemctl is-active`.
- [Frontend deploy needs Metro cache cleared](feedback_expo_metro_cache_deploy.md) — a bare `restart alif-expo` serves a stale bundle; `rm -rf /tmp/metro-* …` first.
- [Don't scp files into the server working tree](feedback_no_scp_to_server_workdir.md) — untracked files block `git pull`, then restart runs stale code silently.
- [Local DBs are stale — fetch prod for analysis](feedback_polyglot_local_db_stale.md) — local alif.db/polyglot.db are dev copies; real review data lives on prod (`ssh alif`).
- [Backups & restore](reference_backups.md) — server cron 6h + local `scripts/backup.sh` + GFS retention; back up before any manual data change.
- [Deploy & LLM gotchas](reference_deployment_gotchas.md) — valid model IDs, alembic-on-fresh-DB, `.env` key names, TTS model.
- [Activity logging](reference_activity_logging.md) — batch scripts + manual Claude actions log to ActivityLog; event-type list + the manual CLI command.

## Alif — learning-engine rules & gotchas
- [Target & collateral words are equal](feedback_target_collateral_equal.md) — no distinction for credit, learning, or intro cards. Repeated user feedback.
- [System-wide caps belong at the chokepoint](feedback_intro_cap_chokepoint.md) — put caps inside `start_acquisition()`, not in one caller that others bypass.
- [Intro-card overload (fixed)](feedback_intro_card_overload.md) — interleave intros among sentences, dynamic cap; never front-load.
- [Never weaken the same_lemma gate](feedback_dont_weaken_same_lemma_gate.md) — intentional hardening (8+ commits); I keep trying to "fix" it. It is not broken.
- [Verification-cutoff bumps orphan pre-cutoff sentences](feedback_stale_verification_gate_orphans.md) — after bumping `MAPPING_VERIFICATION_MIN_AT`, run a manual `reverify_all_active_sentences` sweep.
- [Due-coverage deficit recurs](feedback_due_coverage_deficit_recurs.md) — known words lose their only sentence; refill `(due ∩ cohort) − reviewable` via `batch_generate_material`.
- [No book sentences for acquiring lemmas](feedback_no_book_sentences_for_acquiring.md) — textbook fallback is too hard for Box-1 words; skip rather than serve. (Alif + Polyglot.)
- [Lemma-deletion scripts must enumerate inbound FKs](feedback_lemma_deletion_fks.md) — `ReviewLog.lemma_id` NOT NULL + 6 nullable FKs; grep `ForeignKey("lemmas.lemma_id")` first.
- [Use json_schema= not json_mode for CLI](feedback_json_schema_cli.md) — old parser silently dropped Sonnet's answers and fell back to weak API Haiku.
- [Confusion-capture feature live](feedback_cluster_detection_limit.md) — `confusion_captures` table + picker (PR #167); analysis after ≥50 captures.

## Alif — Arabic NLP gotchas
- [CAMeL MLE feminine ة → 3ms_poss misread](feedback_camel_mle_fem_ta_marbuta_misread.md) — any LLM gate over CAMeL MLE output must warn about this; 22/33 false "valid" canonicals.
- [Quran dagger-alef (U+0670) strip-order bug](feedback_quran_dagger_alef_normalization.md) — normalize before stripping or خَٰلِدُونَ collapses to the name Khaldūn. PR #186.
- [Quran frequency track (islamic source)](project_quran_frequency_track.md) — `islamic_rank` from the committed QAC v0.4 file via `quran_frequency.py`; rebuild the core after deploy to populate it. NOT from QuranicVerseWord (dead end). Classical track = no-go. PR #195.

## Polyglot — rules & gotchas
- [Polyglot mirrors Alif's design & code](feedback_polyglot_mirror_alif.md) — read Alif's equivalent file FIRST; divergence needs a specific Greek/Latin reason.
- [Typography: EB Garamond, never italic](feedback_polyglot_typography_eb_garamond.md) — reference fonts by registered constant (EBGaramond_400Regular); wrong name = silent Georgia fallback.
- ["Network failed" on iOS = client session transition](feedback_polyglot_network_failed_prefetch.md) — not backend; grep `session_fetch` telemetry (PR #185) FIRST.
- [regloss/runon apply globs stale checkpoint shards](feedback_polyglot_regloss_stale_checkpoints.md) — old shards sort after + override fresh verdicts; move them aside before apply.
- [Latin LatinCy homograph mis-lemmatization](feedback_polyglot_latin_homograph_override.md) — fix at `la.py` `_LEMMA_OVERRIDES` keyed on LatinCy's own tag; "surface→>1 lemma" is discovery, not fix, signal.
- [Page-resplit gotchas](feedback_polyglot_resplit_gotchas.md) — null `page_id` on inactive sentences too; polyglot `log_activity` signature ≠ Alif's.
- [Validator: PageWord/SentenceWord = forms_json equivalent](feedback_polyglot_validator_forms_json_equivalent.md) — augment known forms with observed surface→lemma rows when LatinCy mis-lemmatizes.
- [review_log mixes scaffold-confirmations & real recall](reference_polyglot_review_event_classes.md) — filter `fsrs_log_json.scaffold_confirmation` before any retention/curve analysis.
- [Progress metrics = verified words, not activity](feedback_progress_metrics_verified_not_activity.md) — lead with verified word counts; exclude scaffold_confirmation from recall accuracy. (Cross-app principle.)
- [Codex CLI is free (like Claude Max)](feedback_codex_cli_free.md) — default to Codex for Polyglot LLM work; choose on speed/quality not cost. Alif stays Claude-CLI-primary.
- [Audit a "translation" complaint = check glosses too](feedback_audit_translation_check_glosses_too.md) — check BOTH `sentences.translation_en` AND `lemmas.gloss_en` (the lookup-card surface).

## Active / in-flight projects
- [Lemma decomposition audit](project_lemma_decomposition_audit.md) — 🟡 Phase 1 + Phase 2 steps 1–4c + 6 done; steps 7 (re-gloss ت.ر.ك #305) + 8 (Quran spot-check) OPEN.
- [Polyglot Latin live](project_polyglot_latin_live.md) — shipped 2026-05-25 (PR #140); LatinCy + LLPSI/Roma Aeterna seed + Eutropius reader.
- [Polyglot Latin picker exhaustion](project_polyglot_latin_picker_exhaustion.md) — diagnosed 2026-05-26; open levers (Coverage Reader / warm-on-intake / raise per-pass target). Revisit when more Latin lemmas due.
- [Bookify Arabic](project_bookify_arabic.md) — reading-aid PDF tool; Kalila dove chapter shipped; `introduce` subcommand imports top-N to Alif.
- [Spanish Pilot](project_spanish_pilot.md) — standalone Norwegian-Spanish UX prototype; separate SQLite/systemd/port 3100; NO English in UI.
- [Hindawi corpus import](project_corpus_import.md) — 10,781 sentences from 166 children's books; sentence-only; on-demand translation via cron step A2.

## Done / archived (low recurrence)
- [Box-1 starvation bug (fixed)](project_box1_starvation_bug.md) — NEVER_REVIEWED_BOOST (5.0x) in `sentence_selector.py`.
- [Dirty lemma cleanup (done)](project_dirty_lemma_cleanup.md) — LLM-powered cleanup in `import_quality.py`; 41 cleaned.
- [Batch sentence generation](project_batch_sentence_generation.md) — 15 words in 2 CLI calls (~4s/word); also fixed gemini defaults + same-lemma kill + GPT-5.2 fallback.
- [Clitic-leftover audit (done)](project_clitic_my_leftover_audit.md) — 95 lemmas, 88 cleaned; `cleanup_clitic_leftovers.py`.
- [Mapping correction pipeline](project_mapping_correction_review.md) — verify+correct via Claude Haiku CLI; operational.
- [Claude CLI migration lesson](project_llm_cli_monitoring.md) — committed-locally ≠ deployed; push-before-deploy.
- [Learner review 2026-04-05](project_learner_review_2026_04_05.md) — 1,279 FSRS words, 91.3% retention; pipeline deficit + textbook backlog-gate finding.
- [Voice cloning & TTS](project_voice_cloning.md) — PVC/IVC voice IDs, ظ/ض issue, PVC multi-step API.
- [Podcast system](project_podcast_system.md) — passive listening, 6 format variants, segment caching.
- [iOS EAS dev-build gotchas](project_ios_dev_build_gotchas.md) — ATS arbitrary-loads, icon source HTMLs, CgBI PNGs fine, Apple PLA blocks.

## Reference
- [User's Arabic learning goal](user_arabic_learning_goal.md) — classical-literature breadth (Quran, commentaries, medieval poetry), not just MSA. The product's north-star intention.
- [Arabic educational reference pages](reference_arabic_educational_pages.md) — index of 4 standalone HTML pages (function words, ligatures, Quranic marks, fonts) in research/.
- [History + PKM research / Petrarca](reference_history_pkm_research.md) — 12 historians' PKM tools; Petrarca integration plan.
- [Architecture notes](reference_architecture_notes.md) — 🔴 incl. the CRITICAL SQLite naive-datetime pitfall (3 prod crashes); FSRS stability floor; import dedup; hamza.
