---
name: Hindawi Corpus Import
description: Two-phase corpus import from Hindawi children's books — rule-based import inactive, LLM-enrich on-demand, dialogue-aware splitter
type: project
originSessionId: d7506f03-62c6-4a35-ac1c-edb1789459d3
---
Hindawi E-Book Corpus (CC-BY-4.0, HuggingFace) first imported 2026-04-11. Reimported 2026-04-17 after splitter fix (PR #38).

**Two-phase architecture**:
1. **Import** (`scripts/import_hindawi.py`): sentences stored `is_active=False`, `mappings_verified_at=NULL`. **Rule-based only — no LLM calls.** Requires every content word to map to an existing lemma. Parquet at `/tmp/hindawi.parquet` on server (~350MB).
2. **Enrichment** (cron step A2 in `update_material.py`): LLM diacritizes + translates + re-maps + verifies/corrects mappings → activates on success. Max 50/run. Before the 2026-04-17 splitter fix, success rate was ~40% (failures = missing lemmas + splitter-damaged text).

**Splitter** (`_split_on_terminators`): character-walk, tracks `«/»` depth, suppresses internal-terminator splits while inside an unclosed quote. Newlines always split and reset depth. Closers `»")]'` after a terminator absorb at split point.

**2026-04-17 reimport state**:
- 10,748 old-splitter sentences deleted (10,743 inactive + 5 broken-active; 33 clean-active preserved)
- 6,432 new inactive sentences imported (from 165 of 167 children books, 1,813 distinct lemmas covered)
- Quality in new batch: 1.6% orphan guillemet, 5.6% no-terminal-punct (was 26% / 98% before)
- Total corpus now: 6,465 sentences (33 active + 6,432 awaiting enrichment)

**Why:** authentic sentence diversity, not just LLM-generated drill sentences. Classical Arabic literature is core to user's long-term goal.

**How to apply:**
- Deploy splitter changes before any reimport (the server runs `/opt/alif/backend/scripts/import_hindawi.py`).
- Backup DB first: `cp /opt/alif/backend/data/alif.db /opt/alif-backups/alif_pre_*_$(date +%Y%m%d_%H%M%S).db`
- When deleting corpus sentences, also clear `sentence_words`, `sentence_review_log`, `sentence_grammar_features`, `content_flags` rows; NULL `review_log.sentence_id` (preserves lemma review history).
- Run `--analyze` before `--import` to sanity-check accept/reject counts.
- Log completion via `scripts/log_activity.py manual_action`.
