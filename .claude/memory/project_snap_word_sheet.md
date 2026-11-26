---
name: project-snap-word-sheet
description: "Snap word sheet = missed-word card + reasons (lookup_miss_v1), general glosses on Add, import gloss repair — LIVE backend 2026-11-26; OTA + parsing/translation-speed work open"
metadata:
  node_type: memory
  type: project
  originSessionId: 59f4453e-7296-48ef-b1cc-7bffe0a67a7f
  modified: 2026-11-26T08:19:27.886Z
---

Stian's Snap direction (2026-11-26): a tap on a studied word shows the review missed-word card
with its reason buttons; a new word shows only the sentence gloss + Add, and an Enrich button
(adds, enriches on the spot, turns into the card). Stored glosses must be general meanings:
"words with wrong glosses entering the general system is extremely problematic".

**Done (PRs #359–#361, backend deployed, migration dc3e4f5a6b78):** reasons write `lookup_misses`
and open a recovery miss for FSRS words only; `_gloss()` returns `gloss_en` (general) +
`gloss_here` (display) + idiom phrase; 24 of 280 imported lemmas repaired
(`scripts/fix_import_glosses_2026_11_26.py`, audit in `research/gloss-audit-2026-11-26/`,
backup `alif_pre_gloss_repair_20261126_080636.db`).

**Open, in his order:** (1) OTA publish of the frontend (blocked 2026-11-26 because the main
checkout had another session's uncommitted `.claude/memory` edits); (2) iterate page parsing on
stored snaps: pre-match every page word in the background after OCR (~140 model items/page;
resolver alone is not enough — sense check corrected 6/17 "clean" matches); (3) show the
translation first (OCR+translate+align is one ~14 s Gemini call; stream or split by paragraph,
client polls); (4) "Mixed" naming popover in Snap; wrong roots 4305/4307/4362/4367; resolver
picked عَيْن for عيّنت although عَيَّنَ exists (shadda should break the tie). Related:
[[project-word-cards]], [[project-missed-word-recovery]].
