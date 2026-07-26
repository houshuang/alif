# Segmented FSRS calibration: strict learning success vs FSRS recall

## Decision

Do not retune FSRS weights or desired retention from the current mixed history.

The earlier “~16-point FSRS overconfidence” diagnosis used Alif's strict
learning-success threshold (`rating >= 3`) against FSRS retrievability. That is
a valid retention/product outcome, but it is not the scheduler's mathematical
calibration target. FSRS treats Hard (`rating = 2`) as recalled; its binary
target is `rating >= 2`.

After reporting both definitions, a real calibration gap remains:

- strict learning success: predicted 89.92%, observed 78.14%, gap **-11.77pp**;
- FSRS recall: predicted 89.92%, observed 82.88%, gap **-7.04pp**.

The scheduler is still overconfident, but less than the strict-success analysis
implied. The gap is highly concentrated in legacy/untracked-origin and
relearning cards. Clean post-acquisition cards are much closer.

## Reproducible method

`backend/scripts/analyze_fsrs_calibration_segments.py`:

- opens the pinned snapshot with SQLite `mode=ro&immutable=1`;
- verifies its SHA/cutoff against the WP0/WP1 baseline;
- applies the same valid-lemma filter as WP0;
- keeps only due, sentence-reading, non-acquisition reviews with a complete
  stored pre-card;
- computes retrievability at the actual review timestamp;
- reports Wilson 95% intervals and Brier scores under both outcome definitions;
- segments by state, lateness, origin, first post-graduation review,
  stability, probability band, credit metadata, month, and policy-date proxy.

Pinned window:

- `2026-03-27T00:00:00Z` to
  `2026-07-25T17:30:44.409771Z`;
- database SHA-256:
  `3b8ba1d566185ebe908139e24851f4f02214dbd6d6334a3638b6da5f30ef0069`;
- final reproduced analysis library: production's FSRS 6.3.1 (an initial local
  6.3.0 run produced identical metrics and parameter hash);
- parameter SHA-256:
  `a00444e09ca114a3ce9704158c2abb90200f9aa76e4892ef87fe7d4c79b85f56`;
- output:
  `baselines/fsrs-calibration-segmented-2026-03-27-to-07-25/`.

Two fresh runs were byte-identical and the database remained byte-identical.
An automated fixture checks deterministic/read-only behavior.

## Overall result

Eligible reviews: 8,317.

| Target | Predicted | Observed | Observed 95% CI | Gap | Brier |
|---|---:|---:|---:|---:|---:|
| strict Alif success (`rating >= 3`) | 89.92% | 78.14% | 77.24–79.02% | -11.77pp | 0.1755 |
| FSRS recall (`rating >= 2`) | 89.92% | 82.88% | 82.05–83.67% | -7.04pp | 0.1384 |

Primary and collateral reviews are equally valid. Credit type is retained only
as a diagnostic slice.

## The gap is not just lateness

| Lateness | N | Predicted | FSRS recall | FSRS gap | Strict success |
|---|---:|---:|---:|---:|---:|
| 0–1d | 3,104 | 96.04% | 90.46% | -5.58pp | 86.57% |
| 1–3d | 1,970 | 90.38% | 84.52% | -5.86pp | 79.24% |
| 3–7d | 1,618 | 85.64% | 78.80% | -6.84pp | 73.98% |
| 7–14d | 988 | 83.08% | 72.87% | -10.21pp | 66.60% |
| ≥14d | 637 | 80.10% | 66.72% | -13.38pp | 62.17% |

Lateness makes the mismatch worse, but reviews within one day of due still
miss predicted FSRS recall by 5.58 points.

## Origin is the largest split

| Origin | N | Predicted | FSRS recall | FSRS gap | Strict success |
|---|---:|---:|---:|---:|---:|
| post-acquisition | 7,001 | 90.11% | 85.97% | **-4.14pp** | 81.43% |
| legacy/untracked | 1,316 | 88.87% | 66.41% | **-22.46pp** | 60.64% |

The post-acquisition definition uses current `graduated_at`; it is not a
perfect historical episode label. Even so, this split is too large to justify
fitting one parameter set across both populations. Legacy cards include old
state/data regimes whose stability was plausibly inflated before the
acquisition pipeline became the normal entry path.

## Relearning is the weakest scheduler state

| Stored pre-card state | N | Predicted | FSRS recall | FSRS gap | Strict success |
|---|---:|---:|---:|---:|---:|
| Learning (1) | 998 | 90.80% | 80.46% | -10.34pp | 76.75% |
| Review (2) | 5,703 | 89.81% | 85.46% | -4.35pp | 80.52% |
| Relearning (3) | 1,616 | 89.74% | 75.25% | **-14.49pp** | 70.61% |

This supports giving older lapsed words special scrutiny. It does not by itself
identify the correct new parameters: state composition, legacy origin, and
lateness overlap.

## Why desired-retention changes are not the first fix

`desired_retention` controls the interval the scheduler chooses; it does not
change how retrievability is computed from an existing card. Lowering it would
schedule cards later and is directionally wrong for maximizing retention while
observed recall already trails prediction. Raising it might increase workload
substantially without repairing inflated legacy state.

The clean next comparison is:

1. post-acquisition Review-state cards;
2. legacy/relearning recovery cards;
3. on-time versus overdue delivery;
4. explicit scheduler-version/config boundaries.

Only then should a replay compare higher desired retention, state repair, or
relearning-specific treatment under the same learner-minute budget.

## Reproducibility fixes implemented in the workspace

No scheduling behavior was intentionally changed.

- `backend/pyproject.toml` now pins production's `fsrs==6.3.1` instead of
  allowing any future 6.x release. The initial local audit used 6.3.0; the
  production read-only check confirmed 6.3.1 with the identical parameter hash.
- New FSRS review rows stamp library version, scheduler-policy version,
  desired retention, and parameter hash.
- `optimize_fsrs.py` now reads installed-library defaults instead of comparing
  optimized weights with a stale hard-coded vector.
- Its post-lapse diagnostic now uses the minimum of FSRS 6.3's long- and
  short-term forgetting branches.
- `replay_fsrs.py` is explicitly labeled as a frozen April historical
  reproduction, not current calibration.

These changes are workspace-only and not deployed.

## Risks and open validation

- Historical rows do not identify the FSRS package or parameter vector that
  created each card. Date epochs are only proxies.
- `fsrs>=6.0.0` plus `pip install -e .` allowed fresh environments to select a
  different compatible release; the server's installed version at each past
  review is not recoverable from the database.
- Rating 2 is simultaneously an FSRS recall and an Alif strict-learning
  failure. Both views must remain visible; neither should be silently renamed.
- Calibration is conditional on delivery. Selector/recovery policy changes the
  reviewed population.
- No parameters should be optimized on early, quiz, listening, acquisition,
  or invalid/inert rows.

## Rollback

The analysis is read-only. The version pin can be reverted to the old range,
and the telemetry fields can be removed without schema or data migration.
Existing review rows remain valid JSON with additive keys.
