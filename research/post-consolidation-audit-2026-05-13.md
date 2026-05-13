# Post-Consolidation Audit — 2026-05-13

Quick check on prod state after the 2026-05-12 cost-consolidation push, to verify
the bounded-cron / opt-in-enrichment / audio-off defaults aren't silently
degrading data quality. Three checks: env flags, lemma gloss coverage, stranded
sentences.

Script: `/tmp/claude/audit_post_consolidation.py` (read-only, sandboxed).

## 1. Service environment

`systemctl show alif-backend -p Environment` reports **none** of the
cost-consolidation flags are explicitly set:

| Env var | Set? | Code default | Effective state |
|---|---|---|---|
| `ALIF_USE_LEGACY_BATCH` | unset | `"1"` | Legacy batch ON ✓ |
| `ALIF_AUDIO_ENABLED` | unset | `False` | Audio gen OFF ✓ |
| `ALIF_RUN_CRON_LEMMA_ENRICHMENT` | unset | `False` | Lemma enrichment OFF in cron |
| `ALIF_RUN_CRON_CORPUS_ENRICHMENT` | unset | `False` | Corpus enrichment OFF in cron |
| `ALIF_RUN_CRON_PREGENERATION` | unset | `False` | Pre-generation OFF in cron |

Note: the earlier exploration assumed `ALIF_ENRICH_LEMMAS` and
`ALIF_PREGEN_ENABLED` — the real names are `ALIF_RUN_CRON_*` (see
`backend/scripts/update_material.py:97-105`). Behavior matches intent.

## 2. Lemma gloss coverage

The hard invariant: no lemma may have an empty `gloss_en` if it can be reached
from an active sentence (CLAUDE.md "no words without English gloss — EVER").

- **3129 lemmas total**, max id `3384`
- **14 empty-gloss lemmas**, all `source="story_import"`, all
  `word_category="junk"`, all gated on 2026-04-01 / 2026-04-15 (one batch).
- **0 empty-gloss lemmas in the most recent 200 ids** — enrichment-opt-in is
  *not currently biting* because there's no fresh import path running through
  the cron.
- **0 empty-gloss lemmas reachable from any active sentence** — the 14 junk
  rows are quarantined by `word_category="junk"` filters.

**Verdict:** safe today. Latent risk: if a new book/corpus import lands while
the cron flags stay off, those lemmas will skip enrichment and may bypass the
invariant at generation time. Track 1 follow-up: make sure book imports run
through the synchronous enrichment path (not the cron-gated one).

## 3. Stranded sentences (active but not reviewable)

`reviewable_sentence_clauses()` requires `mappings_verified_at >= 2026-04-16`
AND `!= '2000-01-01'` AND no NULL `lemma_id`s.

- **1744 active sentences** total
- **1560 currently reviewable** (89.4 %)
- **184 stranded** (10.6 %), all due to stale `mappings_verified_at` —
  zero with NULL verification, zero with the corpus sentinel
  - 153 LLM-generated
  - 29 book
  - 2 corpus
- **3 active sentences carry a NULL `lemma_id`** — storage gate passed,
  reviewability blocked. Healer in `update_material.py` step 0b should remap
  these next pass.

**Verdict:** the stale-verification cohort is the operationally important one.
These were generated/imported under the old pipeline (pre-2026-04-16) and have
never been re-verified. The cron's healing only handles NULL lemma_ids — it
does **not** trigger re-verification of sentences whose verification timestamp
is stale. They will sit gated forever unless Track 2 explicitly sweeps them.

## Summary

| Finding | Severity | Action |
|---|---|---|
| Audio gen OFF | Intentional cost cut | Confirm with user that listening-mode degradation on new content is acceptable |
| Enrichment OFF in cron | Latent | Verify book-import path uses synchronous enrichment, not cron flag |
| 14 junk lemmas with empty gloss | Inert | None — not user-visible |
| 184 stale-verification sentences | **Operational** | Track 2 sweep — these are exactly the kind of sentences the recurring mapping audit should re-verify |
| 3 active NULL-lemma sentences | Self-healing | Wait one cron cycle, recheck |

The cost consolidation looks well-executed. The one *non-cost* operational
issue surfaced is the 184-sentence backlog — which Track 2 (recurring mapping
audit) is already designed to drain.
