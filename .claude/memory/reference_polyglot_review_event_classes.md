---
name: reference_polyglot_review_event_classes
description: Polyglot review_log mixes reading scaffold-confirmations with real recall tests — separate them before any analysis
metadata: 
  node_type: memory
  type: reference
  originSessionId: e2c67502-7394-4fd4-bcf7-0c9a1538afd2
---

Polyglot is **reading-as-mapping first**, NOT sentence-SRS first like Arabic. So `review_log` mixes three event classes that must be separated before any forgetting-curve / retention / FSRS analysis:

1. **Scaffold confirmations** — `fsrs_log_json` has `scaffold_confirmation: true`. Logged when advancing a reading page over untapped assumed-known words (`apply_page_review` → `record_scaffold_confirmation`). NOT a recall test (no active retrieval; just "didn't flag it"). These dominate the row count (e.g. 1864 of 2118 Latin rows on 2026-05-29).
2. **Acquisition recall tests** — `is_acquisition=1`. Real graded recall on flagged-unknown words.
3. **FSRS-card recall tests** — `is_acquisition=0` AND not scaffold. Real retrieval on carded words.

Word classes (stats discriminators, `polyglot/app/routers/stats.py:548-659`): `knowledge_origin ∈ {pre_known, cognate_known}` = batch-imported assumed scaffold; `fsrs_card_json IS NOT NULL` = genuine target; `confirmed_at` = reading-confirmed; `first_failed_at` on an assumed-known row = familiarity-illusion victim.

**How to apply:** Filter OUT scaffold confirmations before fitting curves or counting "reviews." When the user says they "did lots of reviews," most are reading confirmations — real engagement but not retrieval tests. 2026-05-29 findings (see `research/analysis-2026-05-29-polyglot-real-review-data.md`): ~92% of marked-known holds but ~8% illusion tax; flagged-unknown words are HARD (~40% Latin / ~48% Greek Again-rate); data argues AGAINST lengthening boxes. Related: [[feedback_polyglot_local_db_stale]], [[feedback_polyglot_mirror_alif]].
