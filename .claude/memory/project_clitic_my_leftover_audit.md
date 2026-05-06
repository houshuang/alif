---
name: Clitic-leftover audit (95 lemmas, 88 cleaned)
description: Pre-2026-04-24 dirty lemmas where bare form kept an unstripped Arabic clitic. Closed via cleanup_clitic_leftovers.py.
type: project
originSessionId: e1c13bb6-518c-4982-8330-d1ab3675caab
---
Started as the 35-lemma 1sg possessive ("my X") cohort, broadened on 2026-05-06 to all Arabic proclitics + enclitics. Two-signal audit (bare-form clitic shape + matching English gloss prefix) found 95 lemmas total in prod. Cleaned 88 of them via `backend/scripts/cleanup_clitic_leftovers.py` — three idempotent phases (A: stale-wiring repoint, B: link to existing canonicals, C: create new canonicals + link). 7 false positives were ل-initial verbs glossed "to V" (English infinitive, not "for X" proclitic) — left alone.

**Why:** All hits predate the 2026-04-24 clitic-aware dedup work. The 2026-04-27 lemma-decomposition audit (Phase 2 step 4c) tagged 91 of these and linked 17 to canonicals, but a residual cohort survived because the prior pass didn't always reassign downstream sentence_words / review_log / target_lemma_id refs at link time, and 13 had no canonical at all. The active import path is now correct.

**How to apply:** When the user reports a "my X" / "and X" / "with X" intro card or a sentence-mapping anomaly that smells like a clitic-attached compound was treated as a real lemma, the audit + cleanup is already done as of 2026-05-06. New leftovers shouldn't appear on this scale because every lemma-creation site uses `resolve_existing_lemma()`. If a new instance does surface, treat it as a one-off data fix using the same `merge_orphan_into_canonical` primitive from `apply_step4c_link_survivors.py:68–121`. **Don't** add ـي to `ENCLITICS` in `sentence_validator.py` — it would over-strip defective verbs (قاضي), relational adjectives (عربي), and dual obliques. The gloss-driven audit's two-signal design is the safer way to surface ـي leftovers.

The 6 newly-created canonicals (lemma_id 3349–3354) are ميثاق "covenant", طغيان "transgression", تجارة "trade", اتقى "to fear", افسد "to corrupt", مجال "field". All went through `run_quality_gates(enrich=True)` synchronously so they have proper roots, transliteration, etymology, and forms_json populated by Claude Haiku.
