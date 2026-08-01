# Embedded short stories v2 — aggressive 3-day experiment

**Date:** 2026-08-01

## Decision

Replace the maintenance-passage generator's narrow mood prompt and repeated
oldest-target shortlist with a coverage-aware, story-shape-rotating generator.
Keep the separate Story UI untouched. Increase background supply and reserve up
to two qualifying story cards in a normal sentence-reading session so immediate
quality and learning signals accumulate within days.

## Production baseline

Snapshot: `/opt/alif/backend/data/alif.db`, SQLite online backup copied at
2026-08-01 22:01 UTC, SHA-256
`3f54d5e469f7e78dabb9ed4b6904e8324efb52537b2035b533f434017371e045`.

- 192 maintenance passages existed from 2026-05-10 through 2026-07-30.
- 478 FSRS-due content lemmas had stability at least seven days; 123 were verbs.
- The 192 passages used only 136 unique targets. The ten most-reused targets
  occupied 26.5% of 268 target slots; pepper and scissors appeared in ten
  passages each, rat in nine.
- 41.1% of passages contained “old,” 33.3% centered on a generic man or boy,
  18.8% ended with “now,” and 16.7% ended with “but.”
- Only 32.5% of target slots repeated the target, and only 22.4% showed two or
  more normalized target surfaces. The recent rat cluster made the subjective
  repetition particularly visible.

Prior passage evidence is favorable: the 2026-06-03 efficacy re-run found
idle-filtered reading speed comparable to individual sentences and equal or
better stability changes in four of five pre-stability bands. The intervention
therefore targets creative collapse, target allocation, and volume—not the
basic passage-card UI.

## Intervention

1. **Wide, ranked pool.** Offer the agent up to 96 due candidates, ranked first
   by passage-coverage debt, then story suitability, capped overdue pressure,
   and frequency. The agent must compare several coherent 2–4 word clusters.
2. **Twenty-four rotating story shapes.** Select among the least recently used
   compositional shapes: mystery, bargain, role reversal, failed plan, message,
   parallel lives, chain reaction, useful-fact arc, and others. Shapes prescribe
   an arc, not characters or a fill-in plot.
3. **Negative creative context.** Give the writer the previous 24 titles,
   openings, endings, targets, and shapes. Deterministically reject stock
   empty-house/remaining-memory endings and near-remakes.
4. **Planned repetition.** Every generated story must use 2–4 selected due
   targets and repeat at least one. Morphology shapes require one verb lemma in
   contrasting person/number/tense surfaces; resolved token mappings, not model
   claims, enforce the contrast.
5. **Identity-safe storage.** Recompute target coverage after the surface-aware
   mapper. A target that disappears during mapping cannot remain in metadata.
6. **Aggressive delivery.** Generate at most three stories per warm-cache run,
   up to 12 per rolling 12 hours when due supply warrants it. Reserve at most
   two passage cards in reading sessions of at least 12 sentence slots. Passage
   cards remain restricted to FSRS maintenance states and still need three due
   lemmas, so fragile acquisition reviews remain individual sentences.

## Measurement

Tag every new story `experiment_version="clustered_short_stories_v2"`; pass its
story ID, shape, target plan metrics, and morphology flag through `card_shown`.
Run:

```bash
cd /opt/alif/backend
.venv/bin/python scripts/analyze_short_story_experiment.py --days 3
```

The 1–3 day decision surface is:

- stories and distinct shapes created;
- unique-target coverage and top-ten target concentration;
- share of target slots repeated and shown in varied surfaces;
- cards/unique stories actually shown;
- idle-filtered milliseconds per Arabic word;
- whole-card “understood” rate and selected-target rating success.

This experiment cannot establish delayed retention in three days. The later
endpoint remains stability change and next-review success, stratified by prior
stability and compared with legacy passages and individual sentences.

## Guardrails and rollback

- Generation remains outside session build and behind the existing rolling cap.
- Every sentence still passes deterministic vocabulary/mapping validation,
  sentence quality review, passage-level editing, and exact identity checks.
- If generation repeatedly fails, the warm task logs and skips it; ordinary
  sentence review remains available.
- Rollback is code-only: restore one passage per warm run, an eight-passage
  ceiling, one reserved passage card, and the legacy generator. Stored v2
  stories are valid ordinary maintenance passages and need not be deleted; they
  can be archived independently if their quality is poor.
