---
name: Read data-model.md before ad-hoc DB queries
description: Always read docs/data-model.md (not just models.py) before writing ad-hoc analysis scripts — it has the exact column names in a quick-reference format
type: feedback
---

Read `docs/data-model.md` before writing ad-hoc DB analysis scripts, not just `models.py`. The summary doc is faster to scan for column names.

**Why:** On 2026-04-05, a learner review script failed 3 times because it guessed column names: `surah_number`/`verse_number` (actual: `surah`/`ayah`), `chapter`/`verse` (same), `flags` table (actual: `content_flags`). Each failure wasted a round-trip to the server.

**How to apply:** For any script that queries the DB directly (especially ad-hoc analysis), read `docs/data-model.md` first to get table names and column names. Key gotchas:
- Quran: table `quranic_verses`, columns `surah`/`ayah` (not chapter/verse/surah_number)
- Flags: table `content_flags` (not `flags` or `word_mapping_flags`)
- ULK: table `user_lemma_knowledge` (not `ulk`)
- Reviews: table `review_log` (not `reviews`)
