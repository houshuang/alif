# Scripts Catalog

All scripts in `backend/scripts/`. Run from `backend/` directory.

## Import
- `import_duolingo.py` — Import Duolingo word list (196 words). Uses clitic-aware dedup via `resolve_existing_lemma()`.
- `import_wiktionary.py` — Import from Wiktionary. Uses clitic-aware dedup.
- `import_avp_a1.py` — Import AVP A1 word list. Uses clitic-aware dedup.
- `import_michel_thomas.py` — 5-phase audio course import: Soniox transcribe → extract Arabic segments → LLM classify Egyptian vs MSA → import words as "learning" + sentences → verify; --phase flag for resumability, --dry-run supported.

## Material Generation
- `pregenerate_material.py` — Pregenerate sentences and audio for words.
- `generate_audio.py` — Generate TTS audio for sentences.
- `generate_sentences.py` — Generate sentences for target words.
- `update_material.py` — Cron job (every 6h): Step 0 cap enforcement → Step A backfill → Step B audio → Step C pre-gen → Step D SAMER → Step E lemma enrichment → Step F leech reintroduction → Step G book ULK consistency. Pipeline capped at 300 active sentences, MIN_SENTENCES=2. Default `--model claude_sonnet` (free via Claude CLI). Falls back to Gemini if CLI unavailable.
- `generate_story_claude.py` — Local story generation via `claude -p` with vocabulary compliance validation and retry loop, free with Max plan.
- `generate_sentences_claude.py` — Validator-in-the-loop sentence generation via `claude -p` with Read/Bash tools — Claude reads vocab, generates, runs validator, self-corrects in one session; 10 words/batch, diversity-aware prompt prioritizing acquiring words as supporting vocabulary.
- `rotate_stale_sentences.py` — Identify and retire sentences with low vocabulary diversity — all scaffold words fully known, no cross-training value — then regenerate with diversity-aware prompts.

## Quality & Auditing
- `audit_sentences_claude.py` — Batch sentence quality audit via Claude Code — reviews grammar/translation/compliance with full vocabulary context, outputs retire/fix/ok report.
- `validate_sentence_cli.py` — CLI wrapper around validate_sentence() for use by Claude Code tool sessions.
- `review_existing_sentences.py` — Gemini Flash quality audit of all active sentences, --dry-run supported.
- `verify_sentences.py` — Gemini Flash batch verification of Arabic naturalness, parallel execution.

## Backfills
- `backfill_lemma_grammar.py` — Backfill grammar features for lemmas.
- `backfill_examples.py` — Backfill example sentences for lemmas.
- `backfill_forms.py` — Backfill inflection forms from CAMeL Tools.
- `backfill_forms_llm.py` — Backfill inflection forms using LLM.
- `backfill_frequency.py` — Backfill frequency ranks (CAMeL MSA corpus) + CEFR levels (Kelly Project).
- `backfill_roots.py` — Backfill root associations for lemmas.
- `backfill_root_meanings.py` — Backfill root core meanings.
- `backfill_story_words.py` — Resolve null lemma_ids in story words via morphology + LLM import.
- `backfill_story_proper_nouns.py` — Convert proper nouns to function words.
- `backfill_themes.py` — LLM thematic domain tagging for lemmas.
- `backfill_etymology.py` — LLM etymology data generation for lemmas.
- `backfill_memory_hooks.py` — LLM memory hooks for currently learning words. Flags: `--force` (re-generate existing hooks), `--box1-only` (only Leitner box-1 words), `--batch-size=N`, `--limit=N`, `--dry-run`.
- `backfill_diacritics.py` — LLM tashkīl (diacritization) for bare lemmas + auto-transliteration.
- `backfill_transliteration.py` — Deterministic ALA-LC transliteration from diacritized lemma_ar. No LLM.
- `backfill_samer.py` — SAMER readability L1-L5→CEFR mapping. TSV at backend/data/samer.tsv on server only.
- `backfill_function_word_lemmas.py` — Creates Lemma rows for FUNCTION_WORD_GLOSSES entries lacking DB entries.
- `backfill_wazn.py` — Backfill morphological pattern (wazn) for lemmas. Phase 1: extract from existing etymology_json.pattern (no LLM). Phase 2: LLM classify remaining root-bearing lemmas. `--phase=1|2|both`, `--batch-size=N`, `--limit=N`, `--dry-run`.
- `backfill_word_categories.py` — Classify existing lemmas as proper_name/onomatopoeia via StoryWord cross-ref + LLM batch, --dry-run.

## Cleanup & Maintenance
- `cleanup_bad_roots.py` — LLM-assisted bad root classification and cleanup (POS fixes, variant linking).
- `cleanup_review_pool.py` — Reset under-learned→acquiring, suspend variant ULKs with stat merge, suspend junk, retire bad sentences, run variant detection on uncovered words.
- `reset_ocr_cards.py` — Reset inflated OCR-imported FSRS cards to "encountered". Supports --dry-run.
- `reset_to_learning_baseline.py` — Reset words without genuine learning signal to encountered, preserves review history.
- `retire_sentences.py` — Remove low-quality/overused sentences.
- `normalize_and_dedup.py` — 3-pass production cleanup: LLM-confirmed variant detection + al-prefix dedup + forms_json enrichment.
- `cleanup_lemma_variants.py` — DB-aware CAMeL Tools disambiguation for variants.
- `cleanup_glosses.py` — Clean up gloss text.
- `cleanup_lemma_text.py` — Clean up lemma text fields.
- `merge_al_lemmas.py` — Merge al-prefixed duplicate lemmas.
- `merge_lemma_variants.py` — Merge identified variant lemmas.
- `identify_leeches.py` — Find high-review low-accuracy words, optional --suspend.
- `fix_null_lemma_ids.py` — Re-maps NULL lemma_id sentence_words using comprehensive lookup, retires unfixable sentences.
- `fix_book_glosses.py` — Fix conjugated glosses on book/story-imported lemmas + run full enrichment, --dry-run --limit.
- `cleanup_lemma_mappings.py` — Batch cleanup of lemma data quality issues: wrong glosses, missing particles, conjugated-form lemmas, possessive-form lemmas, al-prefix lemmas, batch re-map via CAMeL + LLM.

## Analysis & Testing
- `db_analysis.py` — Database analysis and statistics.
- `analyze_word_distribution.py` — Word distribution analysis.
- `analyze_progress.py` — Comprehensive learning progress report: knowledge states, acquisition pipeline, graduations, sessions, comprehension, struggling words. Supports `--days N`.
- `audit_llm_usage.py` — Audit LLM API costs/volume from call logs. Parses llm_calls_*.jsonl, infers task types, estimates costs by model. Supports `--log-dir`, `--days N`.
- `benchmark_claude_code.py` — Benchmark Claude Code CLI (Sonnet/Haiku) vs Gemini Flash. Tests: sentence gen, quality gate, forms, memory hooks. Supports `--tasks`, `--models`, `--count`. Includes batched multi-word mode (`sonnet_batch`, `haiku_batch`). Must run with network access.
- `benchmark_llm.py` — Test 3 models across 5 tasks (105 ground truth cases).
- `benchmark_stories.py` — Model × strategy benchmarking for story generation (--models gemini,opus,sonnet --strategies A,B,C,D).
- `test_llm_variants.py` — Benchmark LLM variant detection against ground truth.
- `test_book_import_e2e.py` — Download Archive.org children's book + run full import pipeline, --download-only/--images-dir/--max-pages.
- `tts_comparison.py` — Compare TTS voices/settings.
- `simulate_usage.py` — Simulate raw FSRS usage patterns (no DB, pure library).
- `simulate_sessions.py` — End-to-end multi-day simulation using real services against a DB copy. Profiles: beginner/strong/casual/intensive/calibrated. Uses freezegun for time control. Output: console table + optional CSV.
- `learning_analysis.py` — Comprehensive production learning metrics: vocabulary states, graduation rates, retention, FSRS stability, session patterns, frequency coverage, tashkeel readiness. Raw sqlite3, outputs JSON to stdout + console summary to stderr.

## Utilities
- `log_activity.py` — CLI tool for manual ActivityLog entries.
