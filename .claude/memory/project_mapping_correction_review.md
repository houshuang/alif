---
name: Mapping correction pipeline
description: Verify+correct pipeline for sentence word mappings — uses Claude Haiku CLI, deployed and operational since 2026-03-14
type: project
---

**Status: Operational.** Pipeline switched from Gemini to Claude Haiku CLI (free) as part of full CLI migration (2026-04-01).

Instead of discarding sentences with wrong lemmatizations, the pipeline asks Claude Haiku to suggest correct lemmas and fixes them inline. `verify_and_correct_mappings_llm()` in `sentence_validator.py`.

**Why:** Deterministic clitic stripping errors meant some words (وصف, طائر, ذهب) could never get sentences — every attempt produced the same wrong mapping and was discarded.

**How to apply:** Correction logs at `data/logs/mapping_corrections_*.jsonl` on server. Check via: `ssh alif "cat /opt/alif/backend/data/logs/mapping_corrections_$(date +%Y-%m-%d).jsonl"`
