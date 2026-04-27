---
name: lemma decomposition pipeline audit (Phase 1 + Phase 2 steps 1-4c + 6 done; 7-8 open)
description: Import pipelines stored compound surface forms as lemmas instead of canonical stems. Phases shipped 2026-04-24 to 2026-04-27. Steps 4a-prime+link, 4b, 4c-A, 4c-B, 6 all on prod. 180 lemmas tagged with `decomposition_note.mle_misanalysis=true`, 28 compound→canonical links created, 3,056 corpus sentences requeued for re-verification. Remaining: Step 7 (re-gloss ت.ر.ك root #305 — separate bug), Step 8 (Quran spot-check verification milestone).
type: project
originSessionId: aa23c8d5-8427-4e7e-b29f-5b12a26f8556
---
## Status
**🟡 PHASE 1 + PHASE 2 STEPS 1-4c + 6 COMPLETE. PHASE 2 STEPS 7-8 OPEN.**

**Step 4c + Step 6 result (2026-04-27)**:
- Re-gated all 161 `compound_with_canonical` entries (HIGH=144, MEDIUM=4, LOW=13) using two-pass asymmetric verification (Sonnet primary + Sonnet re-check on flagged verdicts only).
- Verdict distribution: 67 `confirmed_valid_link` (42%), 76 `bogus_mle_error` (47%), 15 `wrong_canonical_real_compound` (9%), 3 `uncertain` (2%).
- **91 lemmas tagged** via `apply_step4c_tags.py` (76 bogus + 15 wrong_canonical). Prod total now 180 tagged (89 from 4b + 91 from 4c).
- **17 compounds linked** to existing canonicals via `apply_step4c_link_survivors.py`. High-impact merges: اَلْيَوْمَ→يَوْم (161 reviews merged), وَلَكِنْ→لكن (111), 3 orphans (لِي, لَها, لَكّ) → preposition لـِ, اَلْآنَ→آنٌ (44).
- **Step 6**: cleared `mappings_verified_at` on 3,056 inactive corpus sentences (touched-only filter — saves ~700 wasteful LLM calls vs. clearing all 3,725). Cron Step A2 will re-verify on next run.
- 3 `uncertain` entries (جارة→جار, وَذَكِيَّة→ذَكِيّ, وَلَطيفة→لَطِيف) all in fem→masc-canonical edge case. Pass 1 fired ة-misread heuristic, pass 2 reasoned past it. Conservative default: no action. Meanwhile 50 fem→masc cases where both passes agreed got `confirmed_valid_link` — the de-facto policy is "link OK".
- **47% bogus rate** vs. 67% on Step 4a-prime (created canonicals): pre-existing canonical compounds have lower MLE-noise than freshly-created ones, as predicted.
- New scripts (all in `backend/scripts/`): `regate_compound_decompositions.py`, `apply_step4c_tags.py`, `apply_step4c_link_survivors.py`, `reenrich_corpus_post_step4c.py`. Verdict snapshot frozen at `backend/data/decomposition_step4c_progress.json` (also on prod).
- Two-pass asymmetric verification design: cost of false-`bogus` is asymmetrically higher than false-`valid`, so pass 2 only re-checks non-`confirmed_valid_link` verdicts. Saves ~50% of verification budget without sacrificing precision. The disagreement→`uncertain` mechanism is the safety net for policy edge cases.
- Activity log entries 1507 (4c-A tags), 1508 (4c-B links), 1509 (Step 6 requeue).

**Step 4b result (2026-04-24 PM, PR #51)**:
- Added `lemmas.decomposition_note` (nullable JSON) column via Alembic `aa7h8i9j0k12`. Initial migration chained off `z6g7h8i9j012`, but `b4e1f07a2c18` had landed on main in the meantime — two heads. One-line re-parent fixed it, direct to main. **Always check `alembic heads` on the server before deploying a new migration.**
- Wrote `backend/scripts/tag_mle_misanalysis_orphans.py`. Reads two frozen JSON artifacts: regate (22 `bogus_mle_error`) + backfill progress (67 `mle_error`). Stamps `{mle_misanalysis, reason, source_artifact, tagged_at, phase: "step4b"}`. Dry-run default, `--apply` to commit. Refuses to overwrite an existing note. One ActivityLog row per run.
- **89 orphans tagged on prod** (22 + 67). ActivityLog entry 1506. Second-run idempotency verified locally before shipping.
- Query: `SELECT ... WHERE json_extract(decomposition_note, '$.mle_misanalysis') = 1`.

**Step 4a-prime result (2026-04-24 PM, PR #49)**:
- Spot-check of Step 3's 33 "created" canonicals found systematic CAMeL failure the original gate missed: feminine ة (tā marbūṭa) routinely misread as 3ms possessive pronoun ـه. Pattern tell: `lemma_ar_bare` ends ة/ه AND `clitic_signals == {"enc0": "3ms_poss"}`. 21/33 matched exactly; manual spot-check showed majority bogus.
- Stricter re-gate with explicit failure-mode warning + worked examples: **22 bogus_mle_error DELETED, 11 confirmed_valid retained**.
- Deleted IDs: #3140-3147, #3153-3166. Survivors: #3139 سُرْعَة, #3148 تَرَك, #3149 قَدَّس, #3150 أَذْكَى, #3151 أَحْمَد, #3152 فَضْلَة, #3167 رَعْد, #3168 إِصْبَع, #3169 لَعَلَّ, #3170 إِذ, #3171 نَبَّأ.
- Zero downstream refs verified pre-delete (per-lemma double-check). No orphan roots freed.
- Artifacts: `research/decomposition-regate-2026-04-24.json` (frozen verdict snapshot), `backend/scripts/regate_step3_created_canonicals.py`, `backend/scripts/apply_step4a_regate_deletions.py`.
- 2 `already_canonical` entries (#1734, #1735) pointed at now-deleted bogus canonicals (#3144/#3145) via bare-key collision — they remain correctly linked in prod to their actual canonicals (#1739, #1740) and need no Step 4a-link action.
- See also: `feedback_camel_mle_fem_ta_marbuta_misread.md` for the failure pattern.

**Step 3 result (2026-04-24 PM, PR #47)**:
- Ran `backend/scripts/backfill_decomposition_orphan_canonicals.py` on prod (186s, 11 LLM batches).
- DB backed up pre-run: `/opt/alif-backups/alif_pre_decomposition_20260424_131904.db`.
- 33 new canonical lemmas inserted (#3139-#3171, `source=backfill_decomposition_audit`, quality gates stamped). Examples: سُرْعَة #3139, رَعْد #3167, إِصْبَع #3168, لَعَلَّ #3169, إِذ #3170, نَبَّأ #3171.
- 67 of 102 orphans flagged `mle_error` by the LLM verdict gate — CAMeL had hallucinated clitic splits on single-indivisible lemmas (كِرَاء "rent", بَادَ "to perish", فَأْر "mouse", عِبْرِيّ "Hebrew", كُحْل "kohl", سِيلُوفُون "xylophone", قُبَّعة "cap" — the last because ة is a feminine marker, not ـه 3ms enclitic).
- 2 skipped as `already_canonical` (drift between audit and run).
- Per-orphan outcomes in `research/decomposition-backfill-progress-2026-04-24.json` — Step 4 should read this to know which 67 orphan compounds to tag `mle_misanalysis` and which 2 to link directly.

**Key Step 3 design decisions to carry forward**:
- Narrow `lemma_ar_bare`-only lookup (not `resolve_existing_lemma`) for re-dedup at insert — the broader lookup matches generated verb conjugations and produces false positives.
- `run_quality_gates(enrich=False)` keeps the script fast; forms/etymology/transliteration decoupled and not load-bearing for Step 4.
- LLM verdict gate (`valid` / `mle_error`) catches CAMeL hallucinations, avoiding 67 bogus Lemma inserts that Step 4 would otherwise need to unwind.

**67% MLE-noise rate surprise**: orphan bucket has way more false positives than the HIGH-tier bucket. Makes sense: orphans by definition are canonicals not in the DB, and real canonicals tend to already exist. For the 144 HIGH-tier `compound_with_canonical` compounds, expect ~100-115 real migrations and ~30-45 mle_misanalysis tags — lower than the raw count suggests.

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
