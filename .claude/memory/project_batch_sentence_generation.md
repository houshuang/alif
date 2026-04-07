---
name: Batch sentence generation (2026-04-06)
description: Multi-word batch generation reduces sentence generation from 30s/word to ~4s/word (15 words in 2 CLI calls)
type: project
---

Implemented multi-word batch sentence generation to replace per-word serial generation.

**Architecture**: `generate_sentences_for_words()` (llm.py) generates sentences for 15 words in one Sonnet CLI call. `batch_generate_material()` (material_generator.py) orchestrates the full pipeline: DB read once → generate once → validate deterministically → verify once (Haiku) → write. Total: 2 CLI calls for 15 words.

**Why:** Each `claude -p` subprocess has 2-5s startup overhead. Old path: 2 calls × 30s × N words. New path: 2 calls × ~30s total for up to 15 words.

**How to apply:** 
- OCR post-scan: `_schedule_material_generation()` uses batch when ≥3 words
- Cron: `step_backfill_sentences()` uses batch path
- Single-word `generate_material_for_word()` unchanged (still used as fallback)
- `BATCH_WORD_SIZE = 15` in material_generator.py

**Performance measured on server (5 words):**
- Generation: 38.7s (1 Sonnet call, 10 sentences)
- Full pipeline including verification: 57.7s
- = 11.5s/word vs previous 30s/word
- For 15 words: ~4s/word (estimated)

**Also fixed in same session (PR #29):**
- Model defaults gemini → claude_sonnet (pipeline was completely broken)
- Same-lemma correction kill (correct_mapping returns current_lemma_id instead of None)
- cli_only parameter prevents GPT-5.2 fallback for Arabic verification
- OCR tracks all acquiring lemma_ids (not just new ones)
