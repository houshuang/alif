# Claude Max / Limbic Usage Optimization - 2026-05-11

## Why

Claude Max usage hit 19% of the weekly allowance much earlier than expected. The visible recent-call rows were small, but each Claude Code session also paid session startup/context overhead. Limbic logs and Claude debug files confirmed that high session count, not just prompt size, was the main waste pattern.

## Baseline

Window: since 2026-05-10 06:00 Europe/Oslo.

| Metric | Value |
|---|---:|
| Claude Code sessions | 3,168 |
| Limbic CLI rows | 3,329 |
| Visible tokens | 2.47M |
| Cached input tokens | 28.95M |
| Subscription-value estimate | $148.59 |

Largest purpose buckets:

| Purpose | Rows/sessions | Subscription-value estimate |
|---|---:|---:|
| `mapping_verification` | 560 | $50.50 |
| `sentence_self_correct_batch` | 280 | $25.98 |
| `enrichment_forms` | 1,213 | $14.42 |
| `batch_verification` | 143 | $13.03 |
| `sentence_gen_batch_words` | 74 | $10.04 |
| `corpus_enrichment` | 500 | $5.55 |

The top fixable issue was one-Claude-session-per-item behavior in forms enrichment and corpus enrichment/verification.

## Limbic Patch Shipped

Deployed to `/opt/limbic` on the server and restarted `alif-backend`.

Changes:
- Claude CLI calls now run headless with unused MCP/plugin/slash-command loading stripped where Claude CLI flags allow it.
- Per-call metadata now records prompt/system/schema char counts and hashes, tools, allowed tools, budget, max turns, work dir, timeout, return code, debug file, and debug file size.
- Failed/timeout/unparseable Claude CLI runs now log zero-cost failure rows instead of disappearing.
- Limbic dashboard/reporting now splits API vs CLI, shows visible/cached/total tokens, and groups CLI usage by purpose.

Verification:
- Local Limbic tests: `19 passed, 1 skipped`.
- Server compile passed.
- Server smoke call produced a clean debug log with no Claude.ai MCP auth failures, no marketplace refresh failure, no loaded plugins, and `Loaded 0 unique skills`.
- `alif-backend` restarted cleanly.

## Alif Patch Shipped

Deployed to `/opt/alif` on the server and restarted `alif-backend`.

Changed files:
- `backend/app/services/lemma_enrichment.py`
- `backend/app/services/grammar_tagger.py`
- `backend/scripts/update_material.py`
- `backend/tests/test_lemma_enrichment.py`
- `backend/tests/test_grammar_tagger.py`
- `backend/tests/test_update_material_batching.py`

### Lemma Forms

Before: `enrich_lemmas_batch()` called `_generate_forms(lemma)` once per lemma.

After:
- `_generate_forms_batch()` sends up to `FORMS_BATCH_SIZE=10` lemmas per Claude Haiku call.
- Output is constrained with `json_schema=`.
- Results are still cleaned through `FORMS_VALID_KEYS`.
- Missing partial items get one batch retry.
- A legacy single-word fallback only runs if the whole batch raises.

Expected effect: `enrichment_forms` drops from roughly one session per lemma to one session per 10 lemmas, while preserving the same stored `forms_json` contract.

### Lemma Grammar Tags

Before: `enrich_lemmas_batch()` called `tag_lemma_grammar()` once per lemma needing `grammar_features_json`.

After:
- `tag_lemmas_grammar_batch()` sends multiple lemmas per Claude Haiku call.
- `enrich_lemmas_batch()` uses `GRAMMAR_BATCH_SIZE=20`.
- Outputs are still cleaned against `VALID_GRAMMAR_FEATURE_KEYS`.
- A legacy single-word fallback only runs if the whole batch raises.

Expected effect: `grammar_tag` session count drops for enrichment catch-up runs without changing the stored `grammar_features_json` shape.

### Corpus Enrichment + Verification

Before: `update_material.py` Step A2 processed each corpus/book sentence independently:
1. one `corpus_enrichment` call for diacritics + translation;
2. one `mapping_verification` call for word-lemma verification.

After:
- `_generate_corpus_enrichment_batch()` diacritizes/translates up to `ALIF_CORPUS_ENRICH_BATCH_SIZE=10` sentences per Claude Haiku call.
- The existing `batch_verify_sentences()` verifier now checks corpus mappings in chunks of `ALIF_CORPUS_VERIFY_BATCH_SIZE=10`.
- Existing failure semantics are preserved: unverified failed batches release the claim for retry; unfixable corrections deactivate the sentence; unmapped content words still prevent activation.

Expected effect: the high-volume corpus path drops from about two Claude sessions per sentence to about two sessions per 10 sentences.

### Multi-Target Generated Sentence Validation

Before: cron Step A and warm-cache multi-target generation validated each accepted generated sentence with separate `mapping_disambiguation` and `mapping_verification` Claude Code calls.

After:
- `validate_multi_target_sentences_batch()` maps all candidates deterministically, then verifies/disambiguates them with `batch_verify_sentences()`.
- Chunks are bounded by `ALIF_MULTI_TARGET_VERIFY_BATCH_SIZE=10` so this stays horizontal without making one oversized verifier prompt.
- The DB write phase remains separate through `write_multi_target_sentence()`, so no write lock is held during LLM calls.

Expected effect: generated multi-target sentences move from per-sentence verification/disambiguation calls into `batch_verification` chunks.

## Verification

Local:

```bash
backend/.review-venv/bin/python -m pytest \
  backend/tests/test_grammar_tagger.py \
  backend/tests/test_lemma_enrichment.py \
  backend/tests/test_update_material_batching.py \
  backend/tests/test_material_generator_fallback.py -q
```

Result after the grammar batch follow-up: `16 passed`.

Server:
- Production backups created with suffix `20260511105825`.
- Compile passed:

```bash
/opt/alif/backend/.venv/bin/python -m compileall \
  backend/app/services/lemma_enrichment.py \
  backend/scripts/update_material.py
```

- Import smoke passed.
- `alif-backend` restarted and is active.
- `GET /openapi.json` returned `200`.

## Operational Expectations

The next scheduled material cron run after deployment is 2026-05-11 11:30 Europe/Oslo.

Success criteria:
- `enrichment_forms` row count per enriched lemma should fall by about 10x.
- `grammar_tag` row count per enriched lemma should fall on enrichment catch-up runs.
- `corpus_enrichment` row count per processed sentence should fall by about 10x.
- `mapping_verification` should fall for Step A2 corpus processing and move into `batch_verification`.
- `mapping_verification` and `mapping_disambiguation` should also fall for Step A multi-target generation and move into bounded `batch_verification` chunks.
- Quality should not regress: activated corpus sentences must still be diacritized, translated, mapped, and verified before becoming active.

Do not tune `sentence_self_correct_batch` yet. It is already batched and quality-sensitive. Any change to batch size, turn budget, timeout, prompt, or model should be benchmarked against naturalness and acceptance rate before production rollout.

Benchmark harness:

```bash
python3 backend/scripts/benchmark_claude_code.py \
  --tasks self_correct_batch \
  --batch-sizes 5,10,15 \
  --count 30
```

This compares production self-correct batch sizes on the same target set, using deterministic vocabulary validation plus Haiku quality review. A production change is acceptable only if a larger or smaller batch size preserves quality approval rate and target coverage while reducing calls/turns materially.

2026-05-11 follow-up: production `generation_pipeline` logs showed the current `BATCH_WORD_SIZE=15` path often timed out, then fell back to legacy generation. Recent examples included 11-word and 15-word self-correct chunks timing out at 180s before the weaker legacy path produced many validation failures. The default production cap was lowered to `ALIF_BATCH_WORD_SIZE=8`, and the self-correct timeout curve is now 120s for one target, 180s for 2-4 targets, and 300s for 5+ targets. This is a conservative move away from the failing 11-15 range, not a model-quality change.

## Rollback

Alif production backups:
- `/opt/alif/backend/app/services/lemma_enrichment.py.bak-20260511105825`
- `/opt/alif/backend/scripts/update_material.py.bak-20260511105825`

Rollback commands:

```bash
cp /opt/alif/backend/app/services/lemma_enrichment.py.bak-20260511105825 \
  /opt/alif/backend/app/services/lemma_enrichment.py
cp /opt/alif/backend/scripts/update_material.py.bak-20260511105825 \
  /opt/alif/backend/scripts/update_material.py
systemctl restart alif-backend
```

Limbic backups:
- `/opt/limbic/limbic/cerebellum/claude_cli.py.bak-20260511084328`
- `/opt/limbic/limbic/cerebellum/cost_log.py.bak-20260511084328`
