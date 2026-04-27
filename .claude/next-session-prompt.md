# Next session prompt

Continuing the lemma-decomposition audit. **Phase 1, Steps 1-4c, and Step 6 are all shipped.** Steps 7 and 8 remain.

## What's shipped

| When | What | PR / Where |
|---|---|---|
| 2026-04-24 AM | Phase 1 audit (read-only) | `research/decomposition-audit-2026-04-24.md`, classification JSON, `scripts/audit_lemma_decomposition.py` |
| 2026-04-24 AM | Phase 2 Step 1: clitic-aware dedup in import paths | PR #46 |
| 2026-04-24 PM | Phase 2 Step 2: DB backup | `/opt/alif-backups/alif_pre_decomposition_20260424_131904.db` |
| 2026-04-24 PM | Phase 2 Step 3: orphan canonical backfill | PR #47 — 33 created, 67 mle_error, 2 already_canonical |
| 2026-04-24 PM | Phase 2 Step 4a-prime: re-gate + delete 22 bogus Step-3 canonicals | PR #49 — caught systematic CAMeL ة→3ms_poss misread |
| 2026-04-24 PM | Phase 2 Step 4a-link: wire 11 confirmed_valid orphans to canonicals | PR #50 — 411 sw + 82 rl + 42 st + 11 ULK redirected |
| 2026-04-24 PM | Phase 2 Step 4b: new `decomposition_note` column + tag 89 mle_misanalysis | PR #51 — 22 bogus_canonical_deleted + 67 step3_refused_creation |
| 2026-04-27 AM | Phase 2 Step 4c-A: re-gate 161 compound_with_canonical, tag 91 | activity log 1507 |
| 2026-04-27 AM | Phase 2 Step 4c-B: link 17 confirmed_valid_link unlinked compounds | activity log 1508 |
| 2026-04-27 AM | Phase 2 Step 6: requeue 3,056 inactive corpus sentences for re-verification | activity log 1509 |

### Step 4c recap

- **Re-gated all 161 entries** (HIGH=144, MEDIUM=4, LOW=13) using two-pass asymmetric verification (Sonnet on both passes). Pass 1 classifies all; pass 2 re-checks only non-`confirmed_valid_link` verdicts with reframing biased toward keeping. Disagreements → `uncertain`.
- **Verdict distribution**: 67 valid (42%), 76 bogus (47%), 15 wrong_canonical (9%), 3 uncertain (2%). 47% bogus rate vs. 67% on Step 4a-prime — pre-existing canonicals carry less MLE-noise than created ones (as predicted).
- **Tag-only for already-linked compounds with wrong canonicals**: leave the link, stamp `decomposition_note`, defer repointing. Unlinking would orphan corpus sentence_words.
- **Fem→masc canonical policy emerged from data**: 50 cases got `confirmed_valid_link` (both passes agreed); 3 edge cases got `uncertain` (passes disagreed). Default policy: link is OK. The 3 uncertain entries are surfaced for any future targeted manual review but are NOT a blocker.
- **High-impact 4c-B merges**: اَلْيَوْمَ→يَوْم (161 reviews), وَلَكِنْ→لكن (111), 3 لـِ-orphans collapsed into the single canonical preposition.

### Step 6 recap

- **Touched-only filter** beats clearing all 3,725 inactive+verified corpus sentences. Filter targets only sentences whose `sentence_word.lemma_id` (post-merge) hits any Step 4 lemma id. 3,056 cleared vs. 3,725 — saves ~700 wasteful LLM calls.
- Cron Step A2 (`enrich_corpus_sentences` in `scripts/update_material.py`) re-verifies on its schedule. No immediate verification trigger in the script — by design, lets the throttled cron handle it.

## What's queued

### Step 7 — Re-gloss ت.ر.ك root #305

Separate bug from the decomposition audit. LLM enrichment conflated ت.ر.ك (leaving) with تُرْك (Turkic). Root row #305 has gloss like "related to Turkic peoples, Turkey, leaving, and abandoning things". Fix:
1. Identify all lemmas under root #305.
2. Re-enrich with corrected gloss (drop the Turkic conflation).
3. Backfill any sentences whose target_lemma's gloss inherits from this root.

### Step 8 — Quran spot-check (verification milestone)

Import a fresh surah post-Step-1 patches. All new compounds should auto-resolve via `resolve_existing_lemma()` before reaching the Lemma table. Verify:
1. No new compound surface forms in the Lemma table from this import.
2. Existing compounds resolve via the audit's link graph.
3. Reading mode on the imported surah shows canonical roots (not compound surface forms) for review credit.

## Resume commands

```bash
cd /Users/stian/src/alif

# Total tagged lemmas on prod (expect 180)
ssh alif "sqlite3 /opt/alif/backend/data/alif.db \"SELECT COUNT(*) FROM lemmas WHERE decomposition_note IS NOT NULL;\""

# Step 6 progress: how many of the 3,056 still unverified
ssh alif "sqlite3 /opt/alif/backend/data/alif.db \"SELECT COUNT(*) FROM sentences WHERE source='corpus' AND is_active=0 AND mappings_verified_at IS NULL;\""

# Should drop from 3,056 toward 0 as cron Step A2 runs. Active count should climb.
ssh alif "sqlite3 /opt/alif/backend/data/alif.db \"SELECT is_active, COUNT(*) FROM sentences WHERE source='corpus' GROUP BY 1;\""

# Check current alembic head on server BEFORE writing new migrations
ssh alif "cd /opt/alif/backend && .venv/bin/alembic heads"

# Refresh local prod snapshot (use sqlite3 .backup to capture WAL)
ssh alif "sqlite3 /opt/alif/backend/data/alif.db '.backup /tmp/alif_snapshot.db'"
scp alif:/tmp/alif_snapshot.db backend/data/alif.prod.db
ssh alif "rm /tmp/alif_snapshot.db"
```

## Gotchas to remember (carried)

- **Alembic multi-heads**: check `alembic heads` on server before new migration PRs.
- **scp-only pulls of alif.db miss the WAL** — use `sqlite3 .backup` first for atomic snapshots.
- **CAMeL MLE ة→3ms_poss failure** is systematic — always warn LLM gates explicitly.
- **Two-pass asymmetric verification** is the right pattern for tag/no-tag decisions where false-positive cost > false-negative cost. Skip pass 2 on the safe verdict.
- **Tag-only when in doubt** — don't unlink/repoint compound→canonical links even if the link is suspect; corpus sentence_words depend on them. The note marks the row for a future re-mapping pass.
- **Touched-only re-verification** — when clearing `mappings_verified_at` for re-enrichment, filter by sentence_words touching changed lemmas to avoid wasted LLM calls.
- **Narrow `lemma_ar_bare`-only lookup** for backfill dedup, not `resolve_existing_lemma` (too broad).
- **`run_quality_gates(enrich=False)`** keeps migration scripts fast.
- **`gh` and `ssh` need `dangerouslyDisableSandbox: true`** on macOS.
- **SSH drops after ~60s idle** — nohup for long runs.
- **Memory file edits unstaged** — stash before `git pull --ff-only` on main.
- **Local venv is lean** — use `/usr/local/bin/python3` for ad-hoc DB scripts, or test on `/tmp/` DB copies.
- **CAMeL frequency file warning is benign** — `app/data/MSA_freq_lists.tsv` not in repo, run_quality_gates completes anyway when called with enrich=False.
- **`cost_log.log` warning during local runs is benign** — local has no `~/.alif-data/llm_costs.db`; server-side cost logging works.

## Absolute no-gos (carried)

- Don't propose "constrain the verifier to only propose in-vocab lemmas" — vocab-gap signal is intentional.
- Don't change `/api/chat/ask` to CLI.
- Don't pause after opening a PR for merge approval. Self-review, merge, done.
- Don't delegate in-session text-transforms to `claude -p` subprocess.
- Don't weaken `apply_corrections` `same_lemma` gate.
- DB-mutating work should still be checked with user before launching — but Step 4a-prime, 4a-link, 4b, 4c-A, 4c-B, and 6 all ran with user's "make your best judgment" / "follow the order" / "do as you see fit" blessing.
