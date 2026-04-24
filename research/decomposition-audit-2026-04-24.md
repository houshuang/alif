# Lemma Decomposition Audit — Phase 1

**Date:** 2026-04-24
**Status:** Phase 1 (read-only) complete. Phase 2 (remediation) not started.
**Trigger:** User-found bug 2026-04-23 — `#2862 وَتَرَكَهُم "and left them"` shown as an atomic verb lemma instead of `و + تَرَكَ + هُم` decomposed to canonical `تَرَكَ`.

## TL;DR

- **2 import paths confirmed buggy** (no clitic-aware dedup): `quran_service.py:732-768` (primary) and `scripts/backfill_function_word_lemmas.py:111-122` (lower risk — curated input).
- **9 import paths confirmed good** (call `resolve_existing_lemma()` before creating).
- **161 compounds with canonical-in-DB** (5.5% of all lemmas), accounting for **886 reviews** — should be redirected to canonicals.
  - **144 HIGH confidence** (object-pronoun enclitic or prep prefix; 593 reviews) — safe migration tier.
  - 4 MEDIUM (definite article only; 141 reviews).
  - 13 LOW (wa-/fa- prefix only; 152 reviews) — needs spot-check, MLE-prone.
- **102 orphan compounds** (canonical missing from DB), accounting for **385 reviews** — Phase 2 must backfill canonicals before redirecting.
- The user's screenshot example **#2862 وَتَرَكَهُم is an orphan** — `تَرَكَ` is not in the DB. Phase 2 needs to create it first.
- The Hindawi corpus enrichment failure (99.5% inactive) is plausibly explained by orphan canonicals: the tokenizer can't match what isn't there.

## Methodology

CAMeL Tools `MLEDisambiguator` (not regex) classifies each of the 2,905 lemmas in `backend/data/alif.prod.db`. For each lemma:

1. Run MLE on the diacritized `lemma_ar`. Inspect `prc0`, `prc1`, `prc2`, `prc3`, `enc0` features.
2. If no clitic features → **canonical**.
3. If clitic features but `lex == bare_form` → **canonical** (MLE is stripping a feature like `Al_det` from `lemma_ar` that's already absent from the bare form — not a real duplication).
4. If clitic features and `lex` is a different word but the lemma's `pos` is `verb`/`noun` while the canonical's `pos` is `pron`/`particle` → **canonical with `mle_misanalysis` note** (the verb-misread-as-pronoun-compound pattern, e.g. CAMeL reading `فَهِمَ` as `ف+هم`).
5. Otherwise the lemma is a **compound**:
   - If clitic-stripped lex matches another DB lemma → `compound_with_canonical`.
   - If clitic-stripped lex is a real Arabic word but not in DB → `orphan_compound`.

### Confidence tiers for `compound_with_canonical`

The MLE has known noise on short content words (it sometimes prefers a clitic reading of a noun like `بَرْد` "cold" as `بـ + رَدّ`). Tiers reflect how reliable the signal is:

| Tier | Meaning | Examples | Phase 2 treatment |
|---|---|---|---|
| **HIGH** | `enc0` (object pronoun suffix) OR `prc1` (preposition prefix `bi_/li_/ka_`). Unambiguous — these morphemes only appear in compounds. | `وَتَرَكَهُم` (enc0=3mp_dobj), `بِجانِب` (prc1=bi_prep) | Spot-check ~10 random samples, then mass-migrate |
| **MEDIUM** | `prc0=Al_det` (definite article) only. Reliable when the article-stripped form ALSO exists as a separate DB lemma. | `اَلْيَوْمَ → يوم`, `اَلْآنَ → آن` | Per-row review (only 4 cases) |
| **LOW** | Only `wa_conj`/`fa_conj` prefix. Real for some forms (وَلَكِنْ) but high false-positive rate for short content words. | `وَلَكِنْ`, `وَنَأْكُلُ`, `وردي` (false: pink → wa+rdy?) | Manual classification |

Filter applied at all tiers: `lex == bare` (MLE noise) and POS-incompatible (verb→pron).

## Verification: known offenders from memory

| lemma_id | compound | classifier verdict | tier | resolved canonical | notes |
|---|---|---|---|---|---|
| #1638 | وَلَكِنْ | ✅ compound_with_canonical | LOW | لكن (#2035) | wa_conj only |
| #1469 | اَلْيَوْمَ | ✅ compound_with_canonical | MEDIUM | يَوْم (#240) | Al_det |
| #1468 | اَلْآنَ | ✅ compound_with_canonical | MEDIUM | آن (#943) | Al_det |
| #1806 | لَها | ✅ compound_with_canonical | HIGH | لِ (#452) | enc0=3fs_pron |
| #430 | تشَرَّفنا | ❌ canonical | — | — | **Different bug class — see below** |
| #1608 | أُعَرِّفُكُمْ | ✅ compound_with_canonical | HIGH | عَرَف (#389) | enc0 |
| #1732 | وَنَأْكُلُ | ✅ compound_with_canonical | LOW | أَكَل (#324) | wa_conj |
| **#2862** | **وَتَرَكَهُم** | ✅ orphan_compound | HIGH | (تَرَكَ not in DB) | enc0=3mp_dobj — **canonical missing** |
| #1692 | فِيهَا | ✅ compound_with_canonical | HIGH | فِي (#95) | enc0 |
| #2874 | خَلَقَكُم | ✅ compound_with_canonical | HIGH | خَلْق (#2898) | enc0 |

**Coverage: 9/10 known offenders caught** (90%). The one miss (#430 تشَرَّفنا) is a verb conjugation duplicate, not a clitic compound — MLE correctly says `lex=تَشَرَّف` with no clitics because نا in Form V perfect 1pl `تَشَرَّفْنا` is a true verbal inflection, not a clitic. This means the user's "compound lemma" problem actually overlaps a second bug class: **inflectional duplicates** (separate Lemma rows for different conjugations of the same verb). Out of scope for this audit but worth a follow-up.

## Per-import-path audit results

| File:Line | Dedup style | Calls `resolve_existing_lemma()`? | Verdict | Notes |
|---|---|---|---|---|
| `app/services/quran_service.py:732-768` | exact-string set membership | NO | **BUGGY** | Confirmed primary bug — `existing_bare_set` then `if bare_norm in existing_bare_set: continue`. Never tries clitic candidates. |
| `scripts/backfill_function_word_lemmas.py:111-122` | exact-string set membership | NO | **BUGGY (low risk)** | Curated input (function words). Bug direction is opposite — would create canonical when compound pre-exists, which is actually the right direction. Worth fixing for hygiene. |
| `app/services/story_service.py:305, 348, 508` | `resolve_existing_lemma()` | YES | GOOD | Reference pattern. |
| `app/services/ocr_service.py:529-531, 632, 1047` | `lookup_lemma()` (clitic-aware) | YES (via `lookup_lemma`) | GOOD | |
| `scripts/bookify_arabic.py:1234, 1252` | `resolve_existing_lemma()` + multi-stage | YES | GOOD | |
| `scripts/import_michel_thomas.py:439, 462` | `resolve_existing_lemma()` | YES | GOOD | |
| `scripts/import_duolingo.py:213, 247` | `resolve_existing_lemma()` | YES | GOOD | |
| `scripts/import_scaffold_lemmas.py:1234, 1252` | `resolve_existing_lemma()` | YES | GOOD | |
| `scripts/import_avp_a1.py:149, 166` | `resolve_existing_lemma()` | YES | GOOD | |
| `scripts/import_wiktionary.py:219` | curated source, post-import variant detection | N/A | OK | No pre-check needed for curated data. |
| `scripts/cleanup_lemma_mappings.py:110` | hardcoded curated list | N/A | OK | Manual fixes; no auto-creation risk. |

The CLAUDE.md claim "all import paths use `resolve_existing_lemma()`" is **wrong for `quran_service.py`** — the file is a *service* not a script, but it does the same import work and was missed.

Method: `grep -rn "db.add(Lemma\|Lemma(" backend/app backend/scripts | grep -v test_ | grep -v __pycache__` then read ~30 lines of context per site.

## Detailed findings

### Where the compounds came from (source distribution)

`compound_with_canonical` (161 total, 144 HIGH-tier):

| Source | Count | Notes |
|---|---|---|
| textbook_scan | 56 | Likely the OCR pipeline before `ocr_service.py` was hardened |
| duolingo | 42 | Pre-existing curated list — most are likely real (e.g. اليوم, الآن) |
| wiktionary | 32 | Curated list — same |
| book | 13 | book_import_service / bookify intro |
| avp_a1 | 9 | curated |
| quran | 4 | quran_service.py |
| flag_autocreate | 1 | material_generator |
| mapping_correction | 1 | sentence_validator |
| story_import | 1 | story_service |
| (null) | 2 | unknown |

`orphan_compound` (102):
- textbook_scan: 73
- wiktionary: 16
- quran: 16 (the user's "~70 orphan Quran compounds" memory note was an over-estimate at the lemma level — only 16 of 92 source=quran lemmas are MLE-confirmed orphans after filtering)
- book: 10
- duolingo: 5

### The Quran orphan story

User's screenshot showed `#2862 وَتَرَكَهُم`. Confirmed:
- Bucket: `orphan_compound` (canonical تَرَكَ not in DB)
- Tier: HIGH (enc0=3mp_dobj)
- Times seen: 2
- ULK source: quran (intentional since the user reads Quran)

Phase 2 sequence for this and similar Quran orphans:
1. Create canonical تَرَكَ as a real lemma (run quality gates).
2. Set `#2862.canonical_lemma_id = <new_taraka_id>` (preserves audit trail per existing variant pattern).
3. Migrate review history: 2 reviews / FSRS state from #2862 onto canonical.

## Connection to the Hindawi corpus enrichment failure

Same-session finding from 2026-04-23: 6,431/6,465 (99.5%) corpus sentences inactive. Top blocker surface forms: وَرَاقَبَ, عَلَيْهِ (17 sentences), إِلَيْهِ (14).

Plausible mechanism: enrichment's `map_tokens_to_lemmas` *does* call clitic-aware logic, but if canonicals like `رَاقَبَ` are missing (because the import pipeline either stored وَرَاقَبَ as a lemma OR never imported رَاقَبَ at all), the tokenizer has nothing to match.

This audit confirms there are 102 orphan compounds in the DB. The orphan list is a **specific worklist** for Phase 2 canonical backfill: each orphan implies one missing canonical that, once created, would unblock both (a) the orphan compound's redirection and (b) any corpus tokens that try to match that canonical.

I haven't validated end-to-end that fixing all 102 orphans recovers the 6,431 inactive sentences — that's a Phase 3 verify step. But the mechanism is consistent.

## Caveats / known noise

- **MLE has ~20-30% false positives in HIGH tier**. Spot examples: #285 برد "cold" (MLE: bi_prep + رد), #38 قطة "cat" (MLE: قطّ + 3ms_poss), #2158 عربة "cart" (MLE: عرب + 3ms_poss). Phase 2 should sample-validate before mass migrating. ~10 random spot-checks per source-cluster should be enough.
- The classifier doesn't catch **inflectional duplicates** (#430 تشَرَّفنا case). That's a separate audit if user wants — would require checking whether multiple Lemma rows share the same root + Form, which is a different query.
- The classifier uses MLE *top* analysis only — multi-lex ambiguity is collapsed. The original audit-script v1 explored multi-analysis but it created more noise than signal.
- LOW tier is genuinely noisy. The 13 entries should be classified by a human or by an LLM with linguistic context. They account for only 152 reviews so this is low priority.

## Recommended Phase 2 sequence

**Sequenced because halfway state = churn (per existing risk note in `project_lemma_decomposition_audit.md`).**

1. **Patch the buggy import path FIRST.** `quran_service.py:732-768` — replace exact-string dedup with `resolve_existing_lemma(bare, lemma_lookup)`. Add a pytest case importing a verse with a known compound and asserting the canonical is reused. Same fix for `backfill_function_word_lemmas.py` for hygiene. This stops the bleed.

2. **Backup DB.** `cp /opt/alif/backend/data/alif.db /opt/alif-backups/alif_pre_decomposition_$(date +%Y%m%d_%H%M%S).db`. Log to ActivityLog.

3. **Backfill orphan canonicals** (102 lemmas). For each `orphan_compound` HIGH-tier entry, create the canonical lex via the scaffold-import pattern. Use `claude -p` with the orphan list to enrich (gloss, pos, root) in batch. Run `run_quality_gates`.

4. **Migrate HIGH-tier compound_with_canonical** (144 lemmas, 593 reviews). Per row:
   - Spot-check ~10 random rows with LLM or human validation before bulk apply.
   - Set `compound.canonical_lemma_id = canonical_id`.
   - Merge ULK rows: sum `times_seen`/`times_correct`, `max(stability)`, `earliest(introduced_at)`, `latest(last_reviewed)`. The user *has* really seen these words — preserve mastery.
   - Treat compound row as variant — DON'T hard-delete (matches existing `canonical_lemma_id` pattern in `word_selector._resolve_to_canonical`).

5. **Apply ALL orphan→canonical redirects** once the canonicals exist (now that step 3 backfilled them, all "orphans" are reclassifiable as `compound_with_canonical`).

6. **Manual review** of 4 MEDIUM + 13 LOW. Small enough to handle in one sitting.

7. **Re-enrich Hindawi corpus.** Clear `mappings_verified_at` on the 6,431 inactive sentences and let the cron re-run. **Verify** the success rate climbs materially. If it doesn't, the corpus failure has a different root cause and we should investigate separately.

8. **Re-enrich gloss for ت.ر.ك** and any other affected roots (homograph conflation — separate bug from same screenshot).

9. **Spot-check the next Quran surah import.** All new compounds should auto-resolve to canonicals.

## Risks / discipline

- **Don't act piecemeal.** Step 1 (import patch) MUST land before step 4 (mass migration), or new compounds will appear during the migration.
- **`same_lemma` gate in `apply_corrections`**: post-migration, some legacy correction events may now be "same lemma" (canonical = ex-compound). That's correct behavior, NOT a regression to chase. See `feedback_dont_weaken_same_lemma_gate.md`.
- **FSRS state merge is irreversible.** Backup before step 4.
- **Don't extend the audit to inflectional duplicates without scoping it separately.** Different bug class, different fix.

## Artifacts

- Classification JSON: `research/decomposition-classification-2026-04-24.json` (~3 MB, all 2,905 lemmas with bucket assignment + ULK history)
- Audit script: `scripts/audit_lemma_decomposition.py` (standalone, re-runnable; uses `/usr/local/bin/python3` for CAMeL Tools)
- This report: `research/decomposition-audit-2026-04-24.md`

To reproduce:
```bash
cd /Users/stian/src/alif
/usr/local/bin/python3 scripts/audit_lemma_decomposition.py \
    --db backend/data/alif.prod.db \
    --out research/decomposition-classification-2026-04-24.json
```
