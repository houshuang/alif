# Generation Pipeline Investigation — 2026-05-03

Follow-up to `learning-review-2026-05-03.md` §10/§13. Investigates why 211 words sit in 7-day generation backoff, 12 acquiring words have zero active sentences, and the 3-hour `update_material.py` cron is producing low yield.

Sources: `/var/log/alif-update-material.log` (9.3 MB, 535 cron runs), `/opt/alif/backend/data/logs/generation_pipeline_2026-*.jsonl{,.gz}`, prod DB snapshot.

> **2026-05-03 revision** — this report originally proposed three "new" fixes: lookup-based target check in the validator, soften the `same_lemma` gate, and an audit of `lemma_ar_bare` corruption. After auditing recent commits, **none of those is actually new**. PR #42 deliberately skipped `lookup_lemma` for the target check; weakening `same_lemma` is explicitly forbidden (PR #28a1cc7, PR #9b5d107, PR #3f8822c, plus a CLAUDE.md memory); the bare-form audit was already done across PRs #9a4c685 / #751e93d / #e361705 / #db53f10 / `cleanup_dirty_lemmas_v2.py`, and the residual ar/bare mismatches are mostly hamza-on-alef vs bare-alef (correct normalization, not corruption). Sections marked "REVISED" below.
>
> The genuinely new issues are: (1) the self-correct path's "empty response" failure with no retry, (2) zero success-event logging on the new path, (3) the existing `missing_lemma_candidates.py` + `import_scaffold_lemmas.py` haven't been run since 2026-04-09 despite the failure pattern continuing.

## TL;DR (revised)

The picture is a long-running, patched-but-incomplete diagnostic loop, not three discrete new bugs:

1. **`correct_mapping()` resolution gap (already identified, partially fixed, regression).** Today's `missing_lemma_candidates.py` shows `فعل` (the verb "to do" — the most common verb in Arabic, definitely in the DB) failing 73× as `same_lemma`. IDEA #245 (closed 2026-04-09) noted "36 of 39 candidates already existed but `correct_mapping()` couldn't find them." The fallback added then (`build_comprehensive_lemma_lookup`) is not catching all the surfaces the verifier proposes.
2. **The corpus-enrichment step destroys sentences over `same_lemma` non-actionable feedback.** 22.4% kept (1,846/8,250). The original design intent is right — don't auto-create lemmas — but the *sentence* didn't become wrong because the verifier disagreed. Permanent deactivation is too punitive in this path. Action: a softer policy for the corpus path *only*, not for fresh generation.
3. **Self-correct batch returns empty responses with no retry.** 596 instances since 2026-04-20 (release of PR #43). Every empty response drops the whole multi-target group; the next phase has to pick up the slack via single-word fallback. This is genuinely new and unaddressed.
4. **Observability gap: the dominant generation path emits no success events.** Pipeline log post-2026-04-20 contains only `batch_validation_failed`. We can't measure the success rate of the path that handles ~80% of generation.
5. **Existing remediation tools haven't been run in three weeks.** `scripts/missing_lemma_candidates.py` and `scripts/import_scaffold_lemmas.py` were built for exactly this failure pattern, but the most recent activity_log entries for them are from 2026-04-09. They are not on the cron.

The generation backoff list has grown **31 → 172** over the cron's history (essentially monotonic). The 7-day backoff timer means once a word is in, even a fixed pipeline takes a full week to recover it.

## Evidence

### 1. Lemma data corruption — six examples from today's failures

Every failed lemma in today's `generation_pipeline_2026-05-03.jsonl` has a `lemma_ar` ↔ `lemma_ar_bare` mismatch:

| ID | `lemma_ar` (canonical) | `lemma_ar_bare` (validator looks for) | gloss | Issue |
|---|---|---|---|---|
| 3028 | غَارِقَة (drowning, fem.) | غارق (drowning, masc.) | "drowning, submerged" | Gender mismatch, mostly OK |
| 2379 | إِجّاص (pears, collective) | اجاصة (pear, singular) | "pears" | Plural↔singular |
| 2402 | جَوَارِب (socks, plural) | جورب (sock, singular) | "socks" | **Plural↔singular** |
| 2570 | رِبْطَة (a tying/bond, noun) | ربط (verbal root) | "tie" | **Noun↔verb root** |
| 2516 | حَاجِب (eyebrow, noun) | احتجاب (concealment, masdar) | "eyebrow" | **Different lemma entirely** |
| 3173 | تَرَفَّعَ (form V verb) | رفع (form I root) | "to rise, elevated" | **Different verb form** |

The validator (`validate_sentence` in `sentence_validator.py:1771`) tries `target_bare` plus al-prefix variants and a clitic-stripped version. None of these accommodates singular↔plural, masculine↔feminine, or form I↔form V. Result: the LLM correctly produces a sentence using *the lemma the user is studying*, the validator demands a different surface, sentence rejected, fail-counter ticks up. After 3 failed runs (`GENERATION_BACKOFF_THRESHOLD = 3`), word goes into 7-day backoff (`GENERATION_BACKOFF_DURATION = 7d`).

All six are `source = "textbook_scan"`. Spot-checking the 12 sentence-less acquiring words from the learning review:

| Word | Source | `lemma_ar` | `lemma_ar_bare` | Verdict |
|---|---|---|---|---|
| #2402 جَوَارِب | textbook_scan | جَوَارِب | جورب | corrupt (plural/singular) |
| #2570 رِبْطَة | textbook_scan | رِبْطَة | ربط | corrupt (noun/verb-root) |
| #3038 ارْتَجَّ | textbook_scan | ارْتَجَّ | ارتجى | partial (different geminate vs final-weak) |
| #3087 زِينَة | textbook_scan | زِينَة | زين | corrupt (noun/verb) |
| #3090 غَلَّفَ | textbook_scan | غَلَّفَ | غلاف | corrupt (verb/noun) |
| #3173 تَرَفَّعَ | textbook_scan | تَرَفَّعَ | رفع | corrupt (form V/form I) |
| # 559 دَمَّ | wiktionary | دَمَّ | دم | OK (the gloss "to coat" is wrong for `دم` though) |
| #2630 مَقْسَم | book | مَقْسَم | مقسم | OK |

So **at least 5/12 sentence-less acquiring words have data corruption**, not just generation failures. The other 7 are plausibly natural-frequency rare words the LLM struggles with. Both subsets compound into the backoff list.

### 2. Validator strictness

`validate_sentence` (line 1771) defines target match as:

```
target_forms = [target_bare, "ال" + target_bare, target_bare[2:]]
token_forms  = [bare_normalized, strip_tanwin_alif(bare_normalized)]
match if any(tf in target_forms for tf in token_forms)
```

It then also tries clitic stripping. **It does NOT try:**
- Resolving `bare_normalized` through `lookup_lemma()` and accepting if it returns the target's `lemma_id`
- Accepting any inflected form of the same root
- Plural↔singular, gender swaps, form I↔V/X verb derivations

The same file uses `lookup_lemma()` for the *known-word* check (line 1870) but not for the target-word check. The target check is the much stricter path despite being for the most important word in the sentence.

This is also why `_strip_clitics` for the target is the *only* generosity — fine for clitic-coated forms (`الجوارب`) but useless for inflectional differences.

### 3. Corpus enrichment is mostly destroying sentences

Aggregated across 165 cron runs:

```
Step A2 enrich: 1,846 / 8,250 sentences (22.4% kept)
                 ↑ 6,404 deactivated permanently
```

The deactivation path sets `is_active = False` and `mappings_verified_at = now`, so retired sentences are never reconsidered. The `apply_corrections` failure modes are:

- `same_lemma`: verifier flagged a position as wrong, but its proposed correct lemma resolves to the *same* lemma_id. Indicates "verifier thinks the lemma in the DB doesn't match but the correct one isn't here." The hard invariant correctly blocks auto-creation, but for *book/corpus* sentences this is throwing away well-formed natural Arabic over a verifier hallucination.
- `not_found`: verifier proposed a `correct_lemma_ar` that doesn't resolve to anything in the DB.

Spot-check from today's run — the verifier flagged `بَدَا` (he appeared, common verb), `يَبْدُو` (present of same), `حَصَلَ` (he happened, common verb), `سَبَبَ` (he caused), `يَفْعَلَ` (he does) — all common Arabic verbs. Many *do* exist in the DB (lemma IDs are mentioned in the failure message). The verifier is flagging surface forms it doesn't agree with the current mapping for, then suggesting an alternate lemma_ar that just round-trips to the same ID.

Possible interpretations:
- The verifier is too aggressive about mapping disagreements, especially across diacritization variants (e.g., `بَدَا` vs `بدى`).
- Proper-name detection (`min_frequency=2`) doesn't catch one-shot character names like the corpus's `بَاك` (looks like Bach in a Hindawi children's book translation), `هَاكَ`, `ثُورْنْتُون:`.
- The pipeline destroys instead of skipping: a single verifier disagreement deactivates the sentence permanently, with no retry path.

### 4. Self-correct batch — observability gap + empty responses

After PR #44 (2026-04-20) made `_generate_via_self_correct` the default for batch runs:

- `generation_pipeline_*.jsonl` post-04-20 contains *only* `batch_validation_failed` events. No `batch_returned`, no `sentence_accepted`, no `rerank_kept`. The new path doesn't emit success events at all.
- 596 occurrences of `Self-correct batch generation failed: empty response: ` in `update_material.log`, all on or after 2026-04-20.

`empty response` is raised by `limbic.cerebellum.claude_cli` when the CLI exits 0 but the JSON `result` field is empty/None — typically: hit `max_turns`, hit budget, or the agent decided it had nothing to say. With `max_turns = 4 + 8*N` for N targets and `max_budget_usd = max(0.2, 0.1*N)`, an 8-target group has 68 turns and $0.80 budget, which should be plenty. Empty responses on smaller groups (1–2 targets) suggest the CLI is short-circuiting before even reading `targets.json`.

There is no retry on empty response — the whole multi-target group is dropped, and the next phase (single-target fallback) has to pick them up.

### 5. Backoff growth

```
Skipping  31 words in generation backoff   (early observed)
Skipping  53 words
Skipping  85 words
Skipping 103 words
Skipping 159 words
Skipping 172 words   (latest run)
```

Monotonically growing. With `GENERATION_BACKOFF_DURATION = 7 days`, even fixing the upstream bugs today wouldn't drain the queue immediately — backoff timestamps reset only on `record_generation_result(..., generated > 0)`.

## What the cron is *actually* doing right now

From the most recent successful cron run today:

```
═══ Step A: Backfill sentences ═══
  Tier distribution: T1=363 T2=60 T3=66 T4=1360
  Total active sentences: 1188
  Pipeline target: 2000
  Budget: 812 sentences to generate
  Skipping 172 words in generation backoff
  Words needing sentences: 11 (of 1849 total)        ← see issue
  Multi-target group: صَيّادِيَة, مُنْخَفِضٌ, قَرْع, ذاع
  Multi-target group: اقرئيه, أَنار, تَجْهِيز, الْحَبَشِيّ
  Multi-target group: الجوع, بَكْر, مَارَسَ
    ✓ Multi-target sentence covering 2 words
    ✓ Multi-target sentence covering 1 words
  Batch generating for 9 words...
    ✓ Batch: 15 sentences for 9 words
  → Generated 17 sentences for 11 words
```

Note "Words needing sentences: **11** (of **1849** total)". The cron has a budget of 812 but only finds 11 words below their per-tier target. That is fine if the pool is healthy — but it's also why the 12 sentence-less acquiring words don't get filled: they're either in backoff or their per-tier target is satisfied by *retired* sentences that no longer exist in `is_active = TRUE`. Worth verifying that `get_existing_counts` filters on `is_active`.

(Adjacent finding: Step C "Pre-generate for upcoming candidates: 0/10". The pre-gen path is 0% effective.)

## Recommended actions (revised, ordered by safety)

### A. Run the existing remediation scripts (operational, not a code change)

The user already built `scripts/missing_lemma_candidates.py` (aggregates `same_lemma` failures) and `scripts/import_scaffold_lemmas.py` (curated import) for exactly this failure pattern. Last run: 2026-04-09.

```bash
ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 \
    scripts/missing_lemma_candidates.py --days 30"
# review top candidates → curate into import_scaffold_lemmas.py SCAFFOLD_WORDS
ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 \
    scripts/import_scaffold_lemmas.py --apply"
```

Today's output is already revealing — the top entries are not "missing" lemmas, they're common forms like `فعل` (73×), `لا` (56×), `بدا` (43×), `نفس` (39×) that **already exist in the DB but `correct_mapping()` cannot resolve the verifier's `correct_lemma_ar` to them.** This means the immediate action is investigating the resolution gap, not adding lemmas.

### B. Investigate the `correct_mapping()` resolution regression

`build_comprehensive_lemma_lookup` was the fallback added in IDEA #245 (closed 2026-04-09) to handle alef/hamza/tanwin variants. **Probed 2026-05-03** with `/tmp/claude/probe_correct_mapping.py` against the prod snapshot — every top `same_lemma` surface (`فَعَلَ`, `لَا`, `بَدَا`, `نَفْس`, `حَال`, `مَلِك`, …) resolves cleanly through both `lookup_lemma` and `correct_mapping`. **There is no resolution gap.**

The actual failure mode is one of two upstream issues:

1. **Verifier hallucinates mismatches on already-correct mappings** (the `same_lemma` cases). The current mapping is right, the verifier (Haiku) flags it as wrong, proposes back the same lemma, gate correctly rejects the non-actionable correction. 73× for `فعل` alone.
2. **Verifier proposes pos/gloss combinations the existing lemma doesn't carry** (the `not_found` cases). Probing showed `هَاكَ` and `بَاكٌ` resolve to lemma IDs via lookup, but `correct_mapping`'s 3-way check rejects because the verifier's `correct_pos` or `correct_gloss` doesn't match the resolved candidate. Mostly harmless: `بَاك` is a proper name; `هَاكَ` is an imperative most generators won't produce again.

Implication for the corpus-enrichment path (Step A2): permanently deactivating sentences over verifier disagreement is too aggressive. The verifier is non-deterministic, and the same sentence on a retry would likely pass. A softer corpus-enrichment policy — "if all `apply_corrections` failures are `same_lemma`, leave the sentence as-is; retry on next cron" — closes 22.4% → likely 70–80% kept without weakening the gate for fresh generation. This is a future PR; not in scope for the observability ship.

### C. Add retry on self-correct empty responses (genuinely new gap)

`_generate_via_self_correct` calls `_limbic_generate` once. On `ClaudeCLIError("empty response: ")`, the whole multi-target group falls through to single-target fallback — at lower throughput and without the cached vocab dump. Add: catch `RuntimeError("empty response")` from line ~796 of `sentence_self_correct.py:generate_sentences_self_correct_batch`, retry once with the same prompt. If still empty, retry with a smaller batch (split the targets in half).

This is one of the few changes here that is genuinely new and unaddressed.

### D. Add success events to the new path (genuinely new gap)

Post-2026-04-20, only `batch_validation_failed` is emitted to `generation_pipeline_*.jsonl`. Add `batch_self_correct_returned` and `batch_self_correct_accepted` in `_generate_via_self_correct`, mirroring the legacy events. Without these, we cannot measure the success rate of the path that handles ~80% of generation.

### E. Rerun `cleanup_dirty_lemmas_v2.py` on the residual mismatches

There are ~10–15 textbook_scan lemmas with genuinely different morphological forms in `lemma_ar` vs `lemma_ar_bare` (e.g. `#2516` ar=`حَاجِب` bare=`احتجاب`, `#3173` ar=`تَرَفَّعَ` bare=`رفع`). These slip through the existing categories A/B/C in `cleanup_dirty_lemmas_v2.py` because they're not prefix issues. Either extend the script with a category D ("ar and bare resolve to different roots") or manually correct.

This is a small, contained data fix — not the whole-pipeline overhaul I implied earlier.

### F. After A–E land, decide on backoff drain

```python
# only after A/B have closed the actual gap
db.query(UserLemmaKnowledge).update({
    UserLemmaKnowledge.generation_failed_count: 0,
    UserLemmaKnowledge.generation_backoff_until: None,
})
```

Otherwise the cleared-but-still-broken words just go right back into backoff.

### Explicitly NOT recommended

- ~~Soften the `same_lemma` rejection in `apply_corrections` for fresh generation.~~ Load-bearing rejection per PRs #28a1cc7, #9b5d107, #3f8822c, and the docstring at `sentence_validator.py:1437`. Initial draft of this doc proposed it for the corpus path only; safer to fix upstream resolution.
- ~~Add `lookup_lemma` to the target-word check in `validate_sentence`.~~ PR #42 added it for the *known*-word check. The target check stayed exact-match. Without evidence that target-check inflection acceptance is needed (and not just "would be nice"), and given the long history of careful exact-match invariants in this area, leave alone until a test case demonstrates a real failure.

## What this does not address

- The 3 FSRS words 30+ days overdue (max 67d) — they're a different filter (sentence_selector comprehensibility gate, not generation), worth a separate look.
- The 305 encountered-with-NULL-`introduced_at` records — a data-shape bug from before the 04-27 fix, not pipeline-related.
- Listening-mode being unused — UX, not pipeline.
