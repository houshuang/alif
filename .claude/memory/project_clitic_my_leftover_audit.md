---
name: 1sg possessive clitic leftover audit (35 lemmas)
description: Pre-2026-04-24 dirty lemmas where bare form keeps the ـِي 1sg possessive clitic — 35 in prod, three import sources, separate from active code path
type: project
originSessionId: e1c13bb6-518c-4982-8330-d1ab3675caab
---
35 prod lemmas where `lemma_ar_bare` ends in ي and `gloss_en` starts with "my " — the 1sg possessive clitic was not stripped at import. By source: 17 from `duolingo`, 17 from `textbook_scan`, 1 from `story_import` ("Prince of Physicians" story_id=20, 2026-03-21).

Examples: `جدي` "my grandfather" → should be `جد`. `بيتي` "my house" → `بيت`. `اسمي` "my name" → `اسم`. `أبي` "my father" → `أب`. `كتبي` "my books" → `كتاب`. Concrete trigger that surfaced this (2026-05-06): lemma 2652 `مَجالِي` "my field" shown as a New Word intro card — the canonical `مجال` doesn't exist yet, so book/corpus sentences containing `مجال` got mapped to this dirty lemma.

**Why:** Predates the clitic-aware dedup work. CLAUDE.md memory says "ALL 11 lemma-creation sites now use clitic-aware dedup ... patched in Phase 2 step 1 of the lemma-decomposition audit (2026-04-24)." The active code path is fixed. This is leftover data, not a recurring bug. The `run_quality_gates()` enrichment pass marks `gates_completed_at` but doesn't re-decompose the bare form, and the lemma-decomposition audit's compound focus didn't sweep clitic-attached possessives in the noun vocabulary.

**How to apply:** When the user reports a "my X" intro card or a sentence-mapping anomaly on a noun, suspect this audit. Don't hot-patch a single lemma — they're systemic. Cleanup needs (a) decide canonical for each, (b) check if the canonical lemma already exists, (c) merge via `canonical_lemma_id` (or create canonical + redirect). `SentenceWord.lemma_id` and `UserLemmaKnowledge` rows must follow the redirect — UserLemmaKnowledge especially is live FSRS data, so any merge script needs a dry-run + activity_log entry + DB backup. Branch name when picked up: `sh/clitic-my-leftover-audit`.

Audit query that surfaces them:
```python
db.query(Lemma).filter(
    Lemma.lemma_ar_bare.like("%ي"),
    Lemma.gloss_en.like("my %"),
).all()
```
