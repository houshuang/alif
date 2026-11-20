---
name: project_preread
description: "Pre-read (photograph upcoming book pages → automatic word prep) — live since 2026-11-16; state, how to inspect a batch, open follow-ups"
metadata:
  node_type: memory
  type: project
  originSessionId: 62e3c03c-4240-4525-9978-96e6a715e759
  modified: 2026-11-16T07:42:07.421Z
---

Pre-read is live (PRs #305–#309, 2026-11-15/16): Snap → "Pre-read pages" → burst camera (shared `components/MultiPageCamera.tsx`) → server OCRs, picks learn/shaky words + names (names practised as `practiced_name`), sense-checks matched words (widens glosses, re-routes homographs), then generates sentences. The user reads an Arabic book about Macron/Atatürk-era Turkey with it instead of ChatGPT, in 10–20 page batches.

**Why:** the user wants aggressive, automatic prep before reading real pages.

**How to apply:** inspect a batch on prod via `preread_sets.result_json` (stats incl. `reglossed`, `sense_rerouted`, `names_added`) and count reviewable sentences per added lemma. Restarting `alif-backend` kills in-flight sentence generation — re-run `routers.preread._prepare_material([], ids)` for words still at zero. Batch 1 leftover: تأثّر / نَضَجَ were not fixed by hand (the sense check catches them from batch 2 onward). Follow-up idea: run the same sense check on Snap and textbook-scan matches. Related: [[project_novel_sprint_2026]].
