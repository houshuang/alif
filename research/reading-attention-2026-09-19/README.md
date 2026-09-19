# Reading attention v1 — implementation and rollout

This implements the September 19 investigation's first operational trial.
Released to production and the iOS preview channel on September 19, 2026.

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

## Production release — September 19, 2026

[PR #278](https://github.com/houshuang/alif/pull/278) was squash-merged as
`4ad8f688f1739bc6c61cfad45aa9eccb3f29f467`. Backend deployment ran from clean
tracked `main` at that exact revision, with a stopped-service SQLite online
backup at `/opt/alif-backups/alif-reading-attention-20260919-pre.db` before the
additive migration `c9e1a3b5d7f0`. The private backup contains 75,105 reviews and
3,413 knowledge rows. No raw learning database is committed here.

Live post-migration comparison to that backup confirmed:

- SQLite integrity OK; **zero preexisting memory rows changed** when comparing
  every old knowledge column, and **zero review rows changed or missing**.
- **1,476 missing ranks filled**, 224 remain NULL; no existing non-NULL rank changed.
- 3,410 maintained knowledge rows and the three explicitly reviewed parked rows.
- Exactly the three nominated QAC core links excluded; no full curriculum rebuild.

The live word endpoints return the expected dispositions for all three parked
candidates and a maintained control; `/api/stats` succeeds. The deployed scheduler
code selects 0.90 desired retention under the active maintenance policy. This
release also brings the previously merged Box-1/recovery adjustments online.
Dry-run and applied curation reports remain beside the private server backup.

Frontend release used the guarded `publish-ios-update.sh` wrapper from a clean,
owned `main` checkout at the same revision. The published iOS manifest passed the
private API URL check: preview channel, runtime `1.0.0`, update
`01a0bb29-aee2-7400-9a55-c6f7ce320c90`, [update group
6f1f8ecd-dc0a-4308-879e-3ae0996f32eb](https://expo.dev/accounts/houshuang/projects/alif/updates/6f1f8ecd-dc0a-4308-879e-3ae0996f32eb).
The phone normally downloads on one launch and applies on the next. On-device
receipt is not independently verified. Browser QA on a private production clone
confirmed that parking and restoring a word preserves its state, history and
times-seen count; the live review screen refreshes after an attention change.
The web service was restarted with its Metro cache cleared and responds HTTP 200
on the server. The public port-8081 URL timed out from the release machine;
external web reachability is unverified. The primary iOS app uses HTTPS directly.

Final checks after the full suite: affected attention/intake/QAC tests 92 passed,
stats/recovery tests 13 passed, frontend 23 suites/254 tests passed again, and
TypeScript passed. No reading improvement is inferred from deployment success.
