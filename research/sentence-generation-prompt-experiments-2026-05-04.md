# Sentence Generation Prompt Experiments - 2026-05-04

## Goal

Increase useful target coverage per sentence without weakening the hard
deterministic validator. The target is not longer sentences for their own sake;
it is more main-lane due words per valid, natural, comprehensible sentence.

## Baseline

Production multi-target generation on 2026-05-04:

| Metric | Value |
|---|---:|
| Multi-target groups | 54 |
| Sentences returned | 212 |
| Accepted by validator | 80 |
| Acceptance rate | 37.7% |
| Distinct target lemmas requested | 116 |
| Targets with >=1 accepted sentence | 88 |
| Target coverage | 75.9% |
| Groups with zero accepted sentences | 9 / 54 |
| Self-correct empty-response failures | 51.5% in recent failure logs |

Baseline failure modes:

- groups were sometimes semantically random,
- validator accepted one-target "multi-target" sentences,
- backed-off/OCR words could crowd out easier useful targets,
- active sentence pool had low due density,
- inactive verified sentences contained many due-dense candidates but were not
  being considered before new LLM calls.

## Tournament

| Candidate | Expected validator pass | Expected targets/sentence | Verdict |
|---|---:|---:|---|
| Demand-weighted multi-target set-cover v2 | 55-70% | 2.0-2.8 | Ship first |
| Self-correct primary + one optional due collateral | 75-90% | 1.4-2.2 | Later, if target density remains low |
| Validator-first grammar lattice | 90-98% | 1.5-3.0 | Too much complexity for this PR |
| Authentic corpus lift-and-repair | 45-70% raw, 60-80% repaired | 1.5-3.5 | Needs separate corpus QA |
| Micro-scene / two-sentence bundle | 65-80% per line | 3.0-5.0 per bundle | Too much UX blast radius now |

## Shipped Prompt/Algorithm Variant

The PR ships the conservative winner:

1. Build multi-target groups by demand, not input order.
2. Prefer root-diverse and written-form-diverse pairs.
3. Require semantic compatibility before making triples.
4. Allow at most one backoff/collateral word per group.
5. Change multi-target validation default to require at least two target hits.
6. Keep single-target validators capable of accepting one target.
7. Tell the LLM to use exact written forms and avoid forcing unrelated targets.

This preserves the current generate-then-validate invariant and avoids adding
LLM calls to session build.

## Prompt Delta

The multi-target prompt now emphasizes:

- exact written target forms,
- at least two target words per sentence,
- everyday contexts,
- no unrelated forced combinations,
- no content words outside the known+target lists,
- full diacritics.

This is deliberately smaller than the radical prompt ideas. The system has a
long history of regressions when prompt changes try to solve validation,
naturalness, and vocabulary diversity at once.

## Inactive-Sentence Salvage

Before new generation, the material updater now searches inactive sentences for
already-verified candidates that:

- are inactive,
- have `mappings_verified_at`,
- cover at least two target lemmas,
- contain no non-function content outside active known/acquiring/learning/lapsed
  words,
- pass the existing Haiku quality review before reactivation.

This targets the strongest simulation signal: inactive verified sentences had
far more two-target and three-target candidates than the active pool.

## Rejected Radical Variants

These were intentionally not shipped in this PR:

- LLM session-build generation: violates the no-LLM-critical-path invariant.
- Bare word drilling: violates the "sentences always" invariant.
- Lowering box-1/box-2 exposure targets further: not needed for the 30/day
  experiment and too likely to confound results.
- Letting validator accept one target in multi-target mode: this hides failure
  and inflates generation metrics.
- Auto-creating missing lemmas during generation repair: known historical source
  of corrupt vocabulary.
- Making long stories the default review unit: previous simulations favored
  target-specific sentences; the new vocabulary level justifies re-testing
  later, but not inside this PR.

## Metrics To Compare After 48 Hours

Generation:

- accepted multi-target sentences / returned sentences,
- distinct target lemmas covered,
- zero-accepted group rate,
- mean accepted targets per sentence,
- quality-gate rejection rate for salvaged inactive sentences.

Learning:

- main-lane useful units/sentence,
- acquiring words without active sentences,
- new words introduced/day,
- daily accuracy,
- main-lane carryover due debt.

## Open Follow-Ups

- Re-run the long-sentence vs short-story simulation after 48h of new data.
- Add an offline eval harness for prompt variants using production target groups.
- Consider a two-line micro-scene unit only if the UI can keep each line
  independently reviewable and validated.
