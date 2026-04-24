---
name: lemma decomposition pipeline audit (Phase 1 + Phase 2 step 1 done; steps 2-8 open)
description: Import pipelines stored compound surface forms (proclitic + stem + enclitic) as lemmas instead of canonical stems. Phase 1 audit (2026-04-24) classified all 2,905 prod lemmas — 1,271 reviews on non-canonical compounds (4.5× the prior estimate). Phase 2 step 1 (2026-04-24) patched both buggy import paths to call resolve_existing_lemma(); bleed stopped. Steps 2-8 (DB backup → backfill 102 orphan canonicals → migrate 144 HIGH-tier compounds → re-enrich corpus → verify) still open.
type: project
originSessionId: aa23c8d5-8427-4e7e-b29f-5b12a26f8556
---
## Status
**🟡 PHASE 1 COMPLETE (2026-04-24). PHASE 2 STEP 1 COMPLETE (2026-04-24). PHASE 2 STEPS 2-8 OPEN.**

Phase 1 audit: `research/decomposition-audit-2026-04-24.md`. Classification JSON: `research/decomposition-classification-2026-04-24.json`. Classifier script: `scripts/audit_lemma_decomposition.py` (re-runnable; uses `/usr/local/bin/python3` for CAMeL Tools).

**Phase 2 Step 1 done (2026-04-24)** — branch `sh/decomposition-phase2-imports`:
- `app/services/quran_service.py:732` now calls `resolve_existing_lemma()` (3-layer dedup: direct lookup → clitic-strip → within-batch guard) before creating compound lemmas. Mirror of the `story_service.py:305,348,508` reference pattern.
- `scripts/backfill_function_word_lemmas.py` refactored: loop extracted into testable `backfill_function_words(db)`, same clitic-aware dedup added. Removed dead duplicate `if norm in existing` line.
- New tests in `backend/tests/test_lemma_dedup_imports.py` (6 cases) cover direct-match, clitic-strip, and create-when-new for both paths.
- 930 fast tests pass. No DB changes — this stops the bleed; existing 144 HIGH + 102 orphans stay where they are until Steps 3-4.

**Phase 1 quantified findings** (vs prior memory estimates):
- 144 HIGH-tier compounds (enc0/prep clitic; 593 reviews) — safe migration tier after spot-check
- 4 MEDIUM (Al_det only; 141 reviews)
- 13 LOW (wa/fa only; 152 reviews) — high false-positive rate, manual review
- 102 orphan compounds (canonical missing from DB; 385 reviews) — Phase 2 must backfill canonicals first
- **Bug was 4.5× under-estimated** (1,271 reviews on compounds vs. memory's "~280 on top 4")

**User's screenshot example #2862 وَتَرَكَهُم confirmed orphan** — canonical تَرَكَ not in DB. Step 3 must create it before redirecting.

**Buggy import paths confirmed and now patched (Step 1)**:
- `app/services/quran_service.py:732` — was exact-string set membership only; now `resolve_existing_lemma()` first
- `scripts/backfill_function_word_lemmas.py` — same pattern fixed; low blast radius (curated input)
- All 11 of 11 lemma-creation sites now use clitic-aware dedup (or use a curated source that doesn't need it)

Full action plan at top of `IDEAS.md` under "🟡 Lemma Decomposition Pipeline." Phase 2 sequence detailed in audit report. Experiment-log 2026-04-24 entries reference it.

## TL;DR of the bug
Compound surface forms like وَتَرَكَهُم (و-proclitic + تَرَكَ stem + هم-enclitic) are being stored as *single* Lemma rows instead of decomposed to the canonical stem. The user has been reviewing these as atomic units for months, so review history is attached to the wrong level.

## Concrete offenders with review impact (verified on prod 2026-04-23)
- #1638 وَلَكِنْ (but, و+لكن) — 104 reviews, known, source=auto_intro
- #1469 اَلْيَوْمَ (today, ال+يَوْم) — 99 reviews, known, source=textbook_scan
- #1468 اَلْآنَ (now, ال+آن) — 43 reviews, known, source=book
- #1806 لَها (to her, ل+ها) — 35 reviews, known, source=book
- #430 تشَرَّفنا (تَشَرَّفَ+نا) — 12 reviews, known
- #1608 أُعَرِّفُكُمْ (عَرَّفَ+كُم) — 7 reviews, acquiring
- #1732 وَنَأْكُلُ (و+نَأْكُل) — 5 reviews, known
- **#2862 وَتَرَكَهُم (و+تَرَكَ+هم) — 2 reviews, learning, source=quran** ← user's screenshot
- #1692 فِيهَا (في+ها) — 0 reviews, encountered
- #2874 خَلَقَكُم (خَلَقَ+كُم) — 0 reviews, encountered

**Top 4 = ~280 reviews credited to compounds instead of canonicals.**

Plus ~70 of 92 source='quran' lemmas look compound (waiting to be promoted).

## Root cause
`backend/app/services/quran_service.py:732-773` — exact-string dedup, no clitic awareness:
```python
existing_bare_set = {normalize_alef(l.lemma_ar_bare) for l in all_lemmas}
if bare_norm in existing_bare_set: continue
lemma = Lemma(lemma_ar=surface, lemma_ar_bare=bare, source="quran", ...)
```
Should call `resolve_existing_lemma()` from `sentence_validator.py:1624` (the clitic-aware dedup that `story_service.py:305,348,508` already uses).

CLAUDE.md claim "all scripts use `resolve_existing_lemma()` for clitic-aware dedup" is **wrong** for Quran path. Per-path audit needed to find other offenders (textbook_scan produced #1469 and #1608 so it's likely also affected).

Decomposition infrastructure already exists and should be called:
- `sentence_validator.py:337` — `_strip_clitics(bare_form) -> list[str]`
- `sentence_validator.py:1624` — `resolve_existing_lemma(bare, lookup)`
- `confusion_service.py:74` — `decompose_surface(surface, lemma_bare, forms)`

## Connection to Hindawi corpus enrichment failure
Same-session finding: 6 431/6 465 (99.5%) corpus sentences inactive. Dominant blocker surface forms: وَرَاقَبَ, عَلَيْهِ (17), إِلَيْهِ (14). These are identical to the decomposition-failure pattern — the enrichment tokenizer *does* try clitic-stripping, but if the canonical stem (رَاقَبَ) isn't in the DB as a lemma (because the import pipeline stored وَرَاقَبَ as a lemma instead, or never imported the canonical at all), the tokenizer has nothing to match to. **Fixing the import pipeline should drastically improve enrichment success rate.**

## Secondary bug (same screenshot)
Root ت.ر.ك glossed as *"related to Turkic peoples, Turkey, leaving, and abandoning things"* — LLM enrichment conflated ت.ر.ك (to leave) and تُرْك (Turk). Fix separately after decomposition settles (re-enrich affected roots).

## Remediation plan (1-2 sessions — see IDEAS.md for full version)
1. **Audit with CAMeL Tools morphology** (not regex — my regex had false positives like فَهِمَ "to understand" matching "%هم"). Classify 2 905 lemmas. Write JSON to `research/`.
2. **Per-import-path audit** — all 7 import scripts + quran_service/story_service/book_import_service/lemma_quality/material_generator flag_autocreate path/sentence_validator mapping_correction path.
3. **Fix imports** — patch Quran path + outliers to call `resolve_existing_lemma()`.
4. **Backfill missing canonicals** — create تَرَكَ, رَاقَبَ, etc. where orphan compounds have no canonical yet.
5. **Migrate compound review history** to canonicals via `canonical_lemma_id` redirect. Merge ULK carefully: sum times_seen/times_correct, max(stability), earliest(introduced_at), latest(last_reviewed). User has *really* seen these words 104 times, just spelt compound — preserve that mastery.
6. **Re-run Hindawi enrichment** on the 6 431 inactive sentences (clear `mappings_verified_at`). Expect success rate to climb.
7. **Fix gloss conflation** for ت.ر.ك and similar affected roots.

## Risks
- **Don't act piecemeal** — halfway state (compounds redirected but imports still creating more) = churn. Backup DB before Phase 2.
- **Watch `same_lemma` gate in apply_corrections** (see feedback_dont_weaken_same_lemma_gate.md). After compound→canonical redirect, some existing corrections may now be "same lemma" — that's correct post-fix behavior, not a bug to chase.
- **FSRS state merge** — when compound has stability 90d and canonical has stability 30d and both have reviews, merged card should be stability ≈ 90d + minor adjustment. Treat the compound's history as the learner's real encounter with the underlying word.

## How I'd verify the fix
- Import a fresh Quran surah after patch → confirm all words resolve to canonical stems or get properly decomposed.
- Corpus enrichment success rate climbs from ~2% to >50% on next cron run.
- No more compound lemmas appearing in sessions (spot-check via `select_next_words` top-30).
- Reading mode on a Quran verse shows canonical root with correct gloss (not "Turkic peoples" conflation).
