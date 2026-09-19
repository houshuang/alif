# Reading attention v1 — implementation and rollout

This implements the September 19 investigation's first operational trial.
Production rollout results will be appended after release verification.

## What changes

- Word detail offers **Maintain now**, **Reading support**, and **Park for later**.
  Choices preserve FSRS/acquisition state and history. Support/parked vocabulary
  remains available in reading and lookup but creates no automatic review debt.
- Imports stage new vocabulary. Explicit enrollment retains the recovery-aware
  0–2/day limit, including book reading and external adds. A recently selected
  reading word gets intake priority for 14 days; old textbook provenance does not.
- Canonical eligibility governs intake, debt, cohort, leech retries, generated
  targets/scaffold, stored review sentences and stale review submissions.
  Exact-token outcomes remain recorded without scheduling parked words.
- Scaffold sampling no longer favors vocabulary just because it has few existing
  generated sentences or high lapse risk. No raw frequency-cutoff ban is enabled.
- Bookifier/Dragoman join the ordinary artifact frequency lanes. The QAC mapper
  uses citation identity and compatible POS, leaving uncertainty unresolved.
- Reviews link directly to supported reading. Use a few reviews followed by one
  short passage within the same visit; completing the card queue is optional.
  The already prepared [Momo restart passages](../reading-refresh-2026-09-15/reading-next.md)
  remain the next small reading unit; no new whole-book enrollment is needed.

## Bounded data intervention

[initial-curation.json](initial-curation.json) contains exact preimages for three
identity-QA candidates to park (3988, 4006, 4484), and three invalid fused-core
links to exclude (816→751, 842→1224, 1375→1267). It does not relabel every rare
word or rewrite a guessed correct meaning. The excluded links remain in the core
inventory for audit; a later reviewed rebuild can recompute ranks from the fixed
mapper. A full rebuilding/reordering of the curriculum is outside this release.

`backend/scripts/apply_attention_curation.py` validates the complete manifest
before changes and defaults to dry-run; `--apply` logs the explicit changes.
The existing NULL-only rank backfill remains a separate operation.

## Verification before release

Pinned base: `1610625748a0dcaee268e7a4567a786075e2b63b` (production before release:
`f901a1b447351ed1a0e2cdc82a48e9291f32cb77`). Fresh online production snapshot:
75,105 reviews, 3,413 knowledge rows; SQLite integrity OK. Private database and
browser-test copies stay outside Git.

Rehearsed migration, NULL-rank backfill and exact curation on a private copy:
1,476 of 1,700 NULL ranks filled; **zero existing non-NULL ranks changed**.
Comparing every preexisting knowledge column with the backup found **zero
changes**; all 75,105 review rows retained. Only attention fields changed on the
three nominated candidates. No active learning rows were broadly parked.

The shadow artifact-lane correction moved 18 currently due words from main to
slow lane (797→779 main, 93→111 slow at rehearsal time). This is not an estimate
of time saved. At the same clock, strict content FSRS debt moved 747→729; new
intake remained blocked by Box-1/recovery, so no promise of immediate new words.

Three prefetch builds made no memory changes. Local timings were 2.13s cold and
1.26/1.30s warm; the unchanged baseline on the same snapshot was 2.01s cold and
1.74/1.18s warm. Existing selector work remains above the nominal <1s goal; no
new provider calls occur in session building. The all-path gate audit is in
[the scheduler documentation](../../docs/scheduling-system.md#reading-attention-v1--2026-09-19).

Full backend suite: **2,100 passed**, 9 slow tests excluded; provider guard blocked
40 attempted calls at the provider runners. Frontend: **23 suites, 254 tests
passed**, TypeScript passed. Subsequent intake-expiry/manifest-preimage and QAC
checks: **92 passed** across the affected files.

## What this does not yet establish

No causal reading-effort improvement, novel-readiness threshold or retention
benefit has been measured. Existing generated sentences are not bulk-retired;
new scaffold sampling changes future supply. The current core still combines
modern/classical evidence. A corrected independent fiction corpus and numerical
rare-scaffold gate are separate evidence-dependent work, not prerequisites for
starting supported reading.

During the two-week trial, compare fresh passages with similar support: voluntary
completion, help per 100 words, reported effort and return to reading. Keep a
small protected vocabulary sample for 7/14/30-day recognition. Report scheduled
reviews separately from exposure-only evidence, and distinguish first readings
from rereading. Do not expand parking just to make the due counter smaller.
