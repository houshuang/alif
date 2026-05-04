# Pipeline Session Handoff — 2026-05-04

This is the state after a long session investigating the generation pipeline and shipping two observability PRs. Pick up here.

## TL;DR for the next session

1. **Read the related docs first** (in this order):
   - `research/learning-review-2026-05-03.md` — 21-day audit that started this thread (1,697 known words, 93% retention, 6 follow-up issues)
   - `research/generation-pipeline-investigation-2026-05-03.md` — the investigation, including the corrected diagnosis after auditing prior work
   - `research/experiment-log.md` top entries (2026-05-04 C1.5 and 2026-05-03 Phase A) — what shipped and why

2. **Run pipeline_stats on prod** to see what changed after one cron cycle:
   ```bash
   ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 scripts/pipeline_stats.py --days 2"
   ```
   This will now show multi-target Phase 1 numbers (deployed today ~14:30 UTC). Compare against the Phase A measurements below and decide whether C1 (empty-response retry) is justified.

3. **CRITICAL guardrails** (don't repeat session-1 mistakes):
   - DO NOT weaken `same_lemma` rejection in `apply_corrections` — load-bearing per CLAUDE.md memory + docstring + 4 commits
   - DO NOT add `lookup_lemma` to the validator's target-word check — PR #42 deliberately left it exact-match
   - DO NOT auto-create lemmas anywhere
   - Read CLAUDE.md Rule #14 ("Investigation Discipline") before proposing fixes — git log + IDEAS.md + scripts-catalog first

## What shipped this session

| PR | Title | Status |
|---|---|---|
| #56 | Phase A: observability for self-correct batch generation | merged db9b343, deployed |
| #57 | Phase C1.5: observability for multi-target sentence generation | merged 4091c78, deployed |

Plus documentation:
- CLAUDE.md Critical Rule #14 "Investigation Discipline in Iterated Areas"
- `.claude/memory/feedback_check_prior_work_first.md` — feedback memory
- Docstring on `validate_sentence` explaining why target check is exact-match-only
- "Periodic Pipeline Maintenance" section in `docs/scripts-catalog.md`
- This file

## Current state of the pipeline

### Generation backoff list

172 (peak) → 164 (today) — words excluded from generation for 7 days due to ≥3 consecutive failures. Drains naturally as backoff timestamps expire. Don't manually clear until C1+C2 land or we'll just rebuild it.

### Self-correct path (single-word + batch fallback) — 3-day measurements

```
date         sc_ret sc_acc sc_emp
2026-05-03        3     15      6   ← partial day, deploy mid-afternoon
2026-05-04       15     45     15   ← full day data
```

- **~50% empty-response rate.** 22 empties / 40 attempts over the window.
- Mean 1.93 sentences/target when it works — quality is fine, *reliability* is the issue.
- All but one empty is `group_size=1` (Phase 3 single-word fallback); one was `group_size=6`.

### Hard-core failing lemmas (3 empty / 0 accepted, all in 7-day backoff)

| ID | Surface | POS | Source | Gloss |
|---|---|---|---|---|
| #840 | مَاهَ | verb | book | "to produce water (of a well, etc.)" |
| #2650 | رِقَّة | noun | book | "delicacy" |
| #2651 | ثمينه | noun_prop | book | "Thameena" (proper name) |
| #582 | آلَة | noun | book | "instrument, utensil" |
| #2307 | آنِسَة | verb | textbook_scan | "Miss" |

Pattern: rare/abstract content. Not a POS issue (verified — only 1/12 is `noun_prop`).

### Sometimes-success lemmas (mixed empty/accepted)

- #1067 رحم: 1 empty / 3 accepted
- #2279 سور: 1 empty / 2 accepted
- #3138 منع: 2 empty / 2 accepted
- #2657, #1257, #2239, #3334, #855, #1547, #2615 — accepted only

### Step A2 corpus enrichment — still invisible

Not covered by either Phase A or Phase C1.5. The 22.4% kept rate (1,846/8,250 across 165 cron runs) is still measured only from `update_material.log`. Daily cron logs show 86% deactivation rate. **Verifier hallucinates mismatch on already-correct mappings; same_lemma gate fires; sentence permanently deactivated.** The Phase B probe (against prod snapshot) confirmed every top failure surface (`فَعَلَ`→#207, `لَا`→#163, `بَدَا`→#402) resolves cleanly via `lookup_lemma` and `correct_mapping`. There is no resolution gap.

### Multi-target Phase 1 — observability deployed today

After PR #57 deploy at ~14:30 UTC, the next cron at 15:30 should produce events. By the next session there should be ≥1 cron cycle of data.

### Other diagnostic surfaces

- 5 textbook_scan-corrupt lemmas show in `batch_validation_failed` issues daily: `إجاص` (#2379), `جوارب` (#2402), `ربطة` (#2570), `حاجب` (#2516), `ترفع` (#3173). These have `lemma_ar_bare` that's morphologically distinct from `lemma_ar` (e.g. #2516 ar="حَاجِب eyebrow" but bare="احتجاب concealment"). About 10–15 such lemmas total based on the spot-check.

## Plan for next session(s)

### Already considered + dropped

- **C0 — filter `noun_prop` from generation queue.** Probed: only 1/12 failure-list lemmas is `noun_prop`. Six active proper-name lemmas (Oslo, Lebanon, Norway, etc.) work fine — filter would starve them. Failure cluster is rare/abstract content not POS. Need more data before targeting.

### Open, in dependency order

**C1 — Empty-response retry in self-correct.** ~20 LOC in `sentence_self_correct.py:generate_sentences_self_correct_batch`. On `ClaudeCLIError("empty response: ...")`, retry once with the same prompt. If still empty and group has ≥4 targets, split in half and retry each. Don't retry on actual API errors. Expected lift: convert ~half of transient empties (the lemmas that sometimes succeed). Won't help the hard-core 5 lemmas.

How to verify: compare `sc_emp / (sc_ret + sc_emp)` ratio in `pipeline_stats` before/after.

**C2 — Soften Step A2 corpus enrichment policy.** Currently `update_material.py:enrich_corpus_sentences` deactivates sentences permanently on any `apply_corrections` failure. The verifier hallucination diagnosis means most failures are non-actionable.

Two options (both require `apply_corrections` to surface failure reasons, not just positions):

(a) If all failure positions are `same_lemma`, leave the sentence as-is and clear `mappings_verified_at` so it retries next cron. Verifier non-determinism resolves itself over multiple passes.

(b) Alternative: raise the same_lemma threshold — only deactivate if ≥2 distinct positions are flagged with same_lemma (one stray verifier disagreement on a long sentence is noise).

This requires adding a return value to `apply_corrections`. Done carefully so the gate behavior in *fresh generation* paths doesn't change — only the corpus-enrichment caller treats reasons differently.

Not in scope: the gate itself. The hard `same_lemma` rejection in `apply_corrections` stays untouched (CLAUDE.md memory).

**C3 — Repair ~10-15 textbook_scan corrupt bare forms.** Manual SQL or extension of `cleanup_dirty_lemmas_v2.py` with a category D ("ar and bare resolve to morphologically distinct lemmas"). The visible offenders are: #2379, #2402, #2570, #2516, #3173. Use `inspect_textbook_scan.py` to find the rest.

**D — Drain backoff list.** Only after C1+C2 land and one clean cron cycle. SQL:
```sql
UPDATE user_lemma_knowledge
SET generation_failed_count = 0, generation_backoff_until = NULL
WHERE generation_failed_count >= 3;
```

### Operational gap (separate decision)

`missing_lemma_candidates.py` and `import_scaffold_lemmas.py` were last run 2026-04-09. They aren't on the cron. Decision needed: add to cron (auto report), or keep manual + monthly reminder. My weak preference is manual + 14-day reminder via the `schedule` skill. User's call.

## Suggested prompt to start the next session

```
Pick up the alif generation-pipeline work. Read
research/pipeline-session-handoff-2026-05-04.md first — it has the state,
the guardrails, and the plan. Then run pipeline_stats on prod for the
last day or two and look at the multi-target Phase 1 numbers
(now visible after PR #57). Based on what the data shows, decide whether
C1 (empty-response retry) is the right next move and propose before
implementing.
```

## Quick reference

```bash
# Stats
ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 scripts/pipeline_stats.py --days 2"

# Backoff list size
ssh alif "grep 'Skipping [0-9]* words in generation backoff' /var/log/alif-update-material.log | tail -1"

# Cron summary recent runs
ssh alif "grep -E 'Generated [0-9]+ sentences for|Self-correct.*empty' /var/log/alif-update-material.log | tail -30"

# Missing-lemma candidates (operational, last run 2026-04-09)
ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 scripts/missing_lemma_candidates.py --days 30"
```
