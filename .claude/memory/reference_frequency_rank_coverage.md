---
name: reference-frequency-rank-coverage
description: "In Alif a NULL frequency rank does not mean rare: a path bug left 1,700 lemmas unranked (2026-03-31 to 2026-09-18). Check rank coverage before any rarity analysis or rarity-based rule."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 054feb30-8f9a-4980-9f88-5606a8917b8b
  modified: 2026-09-18T12:03:41.209Z
---

`lemma_quality._CAMEL_CACHE` pointed at `backend/app/data` from 2026-03-31
until PR #276 (2026-09-18), so no lemma created through `run_quality_gates()`
or `/api/discover` got a CAMeL rank. Common words (أَصْبَحَ, رَأَى, شَدِيد)
therefore looked rare, and the 2026-09-18 spec's first rarity numbers counted
them as rare. The code is fixed, but production stays wrong until
`scripts/backfill_missing_frequency_ranks.py` is run (dry run: 1,476 of 1,700
lemmas, 801 active).

**How to apply:** before treating "unranked" or "rank > N" as rare, count how
many lemmas have `frequency_rank IS NULL` and no `FrequencyCoreEntry` link. If
lemmas with high ids are all unranked, the backfill has not run. Rarity-based
generation rules (spec B1/B2) must wait for it. Analysis script and numbers:
`research/analysis-2026-09-18-sentence-rarity.md`. Related:
[[feedback_understand_system_before_concluding]].
