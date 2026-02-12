# Scripts Catalog

All scripts in `backend/scripts/`. Run from `backend/` directory.

## Import
- `import_duolingo.py` — Import Duolingo word list (196 words). Uses clitic-aware dedup via `resolve_existing_lemma()`.
- `import_wiktionary.py` — Import from Wiktionary. Uses clitic-aware dedup.
- `import_avp_a1.py` — Import AVP A1 word list. Uses clitic-aware dedup.

## Material Generation
- `pregenerate_material.py` — Pregenerate sentences and audio for words.
- `generate_audio.py` — Generate TTS audio for sentences.
- `generate_sentences.py` — Generate sentences for target words.
- `update_material.py` — Cron job: backfill sentences + audio by FSRS due-date priority. Pipeline capped at 200 active sentences, MIN_SENTENCES=2.

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
- `backfill_diacritics.py` — LLM tashkīl (diacritization) for bare lemmas + auto-transliteration.
- `backfill_transliteration.py` — Deterministic ALA-LC transliteration from diacritized lemma_ar. No LLM.
- `backfill_samer.py` — SAMER readability L1-L5→CEFR mapping.

## Cleanup & Maintenance
- `cleanup_bad_roots.py` — LLM-assisted bad root classification and cleanup (POS fixes, variant linking).
- `reset_ocr_cards.py` — Reset inflated OCR-imported FSRS cards to "encountered". Supports --dry-run.
- `retire_sentences.py` — Remove low-quality/overused sentences.
- `verify_sentences.py` — GPT-5.2 batch verification of Arabic naturalness, parallel execution.
- `normalize_and_dedup.py` — 3-pass production cleanup: LLM-confirmed variant detection + al-prefix dedup + forms_json enrichment.
- `cleanup_lemma_variants.py` — DB-aware CAMeL Tools disambiguation for variants.
- `cleanup_glosses.py` — Clean up gloss text.
- `cleanup_lemma_text.py` — Clean up lemma text fields.
- `merge_al_lemmas.py` — Merge al-prefixed duplicate lemmas.
- `merge_lemma_variants.py` — Merge identified variant lemmas.
- `identify_leeches.py` — Find high-review low-accuracy words, optional --suspend.

## Analysis & Testing
- `db_analysis.py` — Database analysis and statistics.
- `analyze_word_distribution.py` — Word distribution analysis.
- `benchmark_llm.py` — Test 3 models across 5 tasks (105 ground truth cases).
- `test_llm_variants.py` — Benchmark LLM variant detection against ground truth.
- `tts_comparison.py` — Compare TTS voices/settings.
- `simulate_usage.py` — Simulate raw FSRS usage patterns (no DB, pure library).
- `simulate_sessions.py` — End-to-end multi-day simulation using real services against a DB copy. Profiles: beginner/strong/casual/intensive. Uses freezegun for time control. Output: console table + optional CSV.

## Utilities
- `log_activity.py` — CLI tool for manual ActivityLog entries.
