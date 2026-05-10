# Learning Projection Intervention Design Spec

Date: 2026-05-10
Status: design spec
Related:

- `analysis-2026-05-10-lemma-learning-projections.md`
- `experiment-log.md`
- `learning-algorithm-redesign-2026-02-12.md`
- `learning-review-2026-05-03.md`
- `aggressive-vocab-experiment-2026-05-04.md`
- `sentence-generation-prompt-experiments-2026-05-04.md`
- `analysis-2026-05-10-hindawi-reading-path.md`
- `spec-2026-05-10-hindawi-passage-promotion.md`

## Goal

Turn the lemma projection analysis into the smallest set of high-leverage
algorithm and UI changes that should improve learning of the words currently
most likely to fail: verbs, high-form-count lemmas, low-root-support lemmas,
confusable words, and words introduced in overly hard sentence contexts.

The design target is not "make every session easier." It is:

- keep the aggressive frequency-core growth path viable;
- reduce first-pass acquisition failures, especially for verbs;
- preserve the proven sentence-only, collateral-credit learning engine;
- make confusion observable enough to treat specific confusor pairs;
- stage authentic material so it supports reading progress without poisoning
  acquisition data.

## Non-Goals

- No bare word review cards.
- No LLM calls inside `build_session()`.
- No global weakening of mapping verification, `same_lemma` rejection, or
  quality gates.
- No independent scheduling for variants. Canonical lemma remains the unit.
- No broad FSRS retention retune based on time-of-day effects.
- No automatic grouping of arbitrary adjacent `source="corpus"` rows into
  passage cards.

## Evidence Base

### Latest Prod Projection Findings

From the 2026-05-10 prod snapshot:

| Signal | Finding | Design implication |
|---|---:|---|
| POS | Verbs: 21.6% failure, 38.6% low projection | Verbs need a separate acquisition treatment. |
| Form count | 11+ forms: 22.0% failure, 39.6% low projection | Generated form richness is a usable pre-review risk signal. |
| First pass | First verb acquisition success: 42.9% | Encoding is the bottleneck, not long-term spacing. |
| Root family | 0 learned siblings: 59.1% low projection; 3+ siblings: about 16% | Use root family as scaffold and interference warning. |
| Unknown scaffolds | Sentence understood rate falls from 70.8% at 0 unknown scaffolds to 47.5% at 2 | Acquisition needs a stricter dynamic sentence policy. |
| Source | Book/corpus sentences are slower and harder than LLM sentences | Authentic text belongs in staged reading/maintenance first. |
| Confusions | Only `was_confused` exists, not "confused with X" | Pair-level telemetry is required before serious confusor scheduling. |
| Difficulty | `sentences.difficulty_score` is NULL in prod | Persist static and user-specific sentence difficulty signals. |

### Experiment History Synthesis

I scanned the full experiment-log timeline from February 9 through May 10 and
read the full entries around the algorithm turning points. The pattern is
consistent:

| Period | What was learned | Constraint for this spec |
|---|---|---|
| Feb 12 redesign | Direct FSRS cold-start after OCR inflated stability and crashed accuracy. Acquisition phase was the right repair. | Do not bypass acquisition for hard lemmas just because retention looks globally healthy. |
| Feb 20 acquisition-rate analysis | The system can sustain high daily learning because sentences carry collateral credit. Accuracy gates are the right control surface. | Tune per-risk treatment, not a blunt daily slowdown. |
| Mar 3 intro A/B | Intro-card-first beat sentence-first: +28pp first-review accuracy, faster graduation, similar post-graduation FSRS. | Keep intro cards, but make high-risk intro cards smarter. |
| Mar 14 intro overload | Same-domain OCR batches produced too many new words and 3-5 unknowns per sentence. `MAX_UNKNOWN_SCAFFOLD=2` was added. | The cap solved overload generally, but prod data now says acquisition needs an even stricter dynamic cap. |
| Mar 18 collateral credit | Encountered-word dead zone was fixed by making every non-function word earn credit. | Do not throttle collateral credit. Control difficulty at sentence selection/generation. |
| Mar 21 confused rating | Confused words correctly map to Rating.Hard, not Good or Again. | Confusion should shorten intervals without treating the word as totally unknown. |
| Apr 13/27/30 intro ordering | Intro cards must appear immediately before sentence use; late/flooded intros hurt sessions. | UI interventions must be compact and sentence-bound. |
| May 3 generation review | Pipeline issues were often already-known operational gaps; exact mapping gates were load-bearing. | New design must respect validator/quality history and avoid "just loosen it" fixes. |
| May 4 aggressive acquisition | 30/day is plausible only with main/slow lanes, demand-weighted generation, and stop rules. | Projection interventions must protect the main lane without hiding artifact debt. |
| May 10 quality gate | Deterministic validation is insufficient for naturalness/translation quality. | Sentence policy needs both validation safety and learner difficulty safety. |
| May 10 Hindawi path | Imported sentence packs are close; full raw books are blocked by unmapped surfaces. | Authentic material should enter through reading packs/passages, not early acquisition cards. |

## Ranked Interventions

### P0. Projection And Selection Telemetry Layer

Build an offline/read-side `learning_projections` layer before changing the
scheduler heavily.

Why this is highest leverage:

- It gives every later intervention a measurable target.
- It is low learner-facing risk.
- It closes current blind spots: unknown scaffold count, sentence source, card
  position, actual confusor, and dynamic difficulty at time of review.

Proposed data:

```text
learning_projections
  lemma_id unique
  projection_band          high / normal / fragile / leech_risk
  risk_score               0.0-1.0
  risk_reasons_json        ["verb", "forms_11_plus", "root_support_0", ...]
  recommended_treatment    normal / verb_path / strict_sentence / confusor_pair / suspend_reteach
  features_json            static + latest behavioral features
  computed_at

lemma_confusion_edges
  lemma_id
  confused_with_lemma_id
  source                   user_selected / rule_candidate / inferred_history
  strength
  confused_count
  last_confused_at

sentence_review_context
  sentence_review_log_id
  primary_lemma_id
  sentence_source
  sentence_length
  unknown_scaffold_count
  known_scaffold_count
  due_lemma_ids_json
  collateral_lemma_ids_json
  selection_reason
  card_index
  total_cards
```

Implementation notes:

- Start with a script and read-only table update. Do not put heavy scoring in
  session build.
- Persist selection snapshots at session-build or review-submit time. Avoid
  reconstructing "what was known then" forever from mutable ULK state.
- Populate `sentences.difficulty_score` only for static sentence features
  (length, source, syntactic/grammar flags, target count). Store user-specific
  difficulty in review context.
- Fix the `sentence_review_service.py` docstring while touching this area:
  code maps confused words to rating 2, but the docstring still says rating 3.

Trade-offs:

- Adds schema and migration surface before product behavior changes.
- Initial risk score will be rule-based and imperfect.
- Worth it because current analysis already depends on expensive reconstruction.

Acceptance:

- 95%+ of new sentence reviews have dynamic context rows.
- Projection script produces stable top-risk lists explainable by risk reasons.
- No measurable session-build latency regression.

### P0. Acquisition-Safe Sentence Policy

Replace the single global `MAX_UNKNOWN_SCAFFOLD=2` with a state-aware policy.
Keep the global cap as the outer maximum, but tighten early acquisition cards.

Policy matrix:

| Card situation | Source | Max unknown scaffolds | Length target | Notes |
|---|---|---:|---:|---|
| First acquisition review, high-risk lemma | LLM / claude_code | 0 | 5-8 words | No book/corpus primary acquisition. |
| First acquisition review, normal lemma | LLM / claude_code | 0-1 | 5-8 words | Prefer 0 if material exists. |
| Reviews 2-3, high-risk lemma | LLM / claude_code | 1 | 5-9 words | Keep context varied but controlled. |
| Box 2+ acquisition | LLM first, book only if clean | 1-2 | <=10 words | Allow mild stretch after initial encoding. |
| FSRS maintenance | Current selector | 2 | existing | Book/corpus/passages allowed. |
| Reading pack / passage | Reading-pack selector | text-level coverage | 3-5 sentences | Separate authentic-text flow. |

High-risk definition for MVP:

- `pos == "verb"`;
- or generated/known form count >= 11;
- or no known root siblings;
- or projection band is `fragile` / `leech_risk`;
- or prior confusion rate >= 10%.

Implementation notes:

- Add a helper such as `sentence_policy_for_lemma(ulk, lemma, projection)`.
- Apply it in both main selection and pregenerated fill path, matching the
  April 30 lesson that fill paths must share gates.
- Do not change review credit. Every content word still earns credit.
- Prefer pregenerated material. If no safe sentence exists, let material rescue
  generate it later rather than showing a bad early acquisition sentence.

Trade-offs:

- Some high-risk words will wait longer for material.
- Active sentence pool density may dip because strict sentences cover fewer
  targets.
- The trade is favorable: first-pass failures are the largest observed loss,
  and generation rescue already exists.

Acceptance:

- First acquisition reviews with 2 unknown scaffolds drop near zero.
- First-review success improves, especially verbs: target +10pp absolute in a
  2-week window.
- Sentence-less acquiring count does not increase by more than 10.

### P0. Verb-Aware Acquisition Path

Add a targeted path for verbs and high-form-count lemmas. This is not more
generic review volume; it is better encoding before and during the first few
sentence reviews.

UI treatment:

- Intro card gets a compact verb strip: dictionary form, present, masdar, active
  participle when available.
- Root-family anchors show known siblings first, then one or two nearby unknown
  siblings as "do not confuse" candidates only if they are likely confusors.
- Pattern/wazn chip is visible but not a tutorial block.
- Example sentence remains the main practice unit.

Algorithm treatment:

- Require the strict acquisition-safe sentence policy above.
- Track which verb forms have appeared during acquisition, using
  `variant_stats_json` or a small `lemma_form_exposure` table.
- For reviews 1-3, prefer sentences using high-signal common forms:
  dictionary/past, present, and one nominal derivative only if already enriched.
- Keep `BOX1_MIN_EXPOSURES=4`, but stop wasting repetitions after graduation as
  the May 4 auto-skip fix already does.
- Graduation can remain tiered, but high-risk verbs should not get Tier-0
  instant graduation immediately after an intro. The existing 10-minute guard is
  correct; consider extending only for high-risk verbs after measurement, not
  globally.

Trade-offs:

- Intro cards become denser. This should be limited to verbs/high-risk lemmas,
  not every word.
- Requires reliable forms data. Missing forms should degrade gracefully to the
  current card, not block the lemma.
- Root-family display can increase interference if it surfaces too many
  siblings. Keep it anchored to known words and likely confusors only.

Acceptance:

- Verb first-review success rises from 42.9% to at least 55%.
- Verb low-projection rate among newly introduced verbs falls below 30% after
  enough sample accumulates.
- No increase in intro-card abandonment or session-end intro drops.

### P0. Pair-Level Confusion Telemetry And Treatment

Current confusion data says "this lemma was confusing"; it does not say what it
was confused with. That blocks the most valuable intervention.

Review UI:

- When a word is marked confused, show 2-4 likely confusor candidates in the
  reveal phase.
- Candidate sources:
  - same root siblings currently known/learning/acquiring;
  - identical or near-identical rasm skeleton;
  - phonetic/emphatic neighbor;
  - high-frequency words recently reviewed in the same session/day.
- Include a lightweight "none of these" path.
- Log the chosen pair. Do not require this selection on every confused mark if
  it creates friction; start with optional selection on high-stability or
  repeated confusions.

Treatment:

- Add a compact contrast panel in word detail and rescue intro cards.
- Separate high-strength confusor pairs in the same session unless the card is
  explicitly a contrast/rescue treatment.
- If the same pair repeats, generate or choose a sentence that disambiguates the
  target via context, not a bare comparison quiz.

Trade-offs:

- More UI friction at the moment of review.
- Rule candidates will sometimes miss the real confusor.
- Pair data is worth the friction because otherwise confusor handling remains
  generic and speculative.

Acceptance:

- 70%+ of repeated confusion events have a candidate pair captured.
- Re-confusion rate for captured pairs drops over the next 2-3 exposures.
- Session builder does not place strong confusor pairs back-to-back except for
  explicit contrast cards.

### P1. Sentence Leech And Repeated-Partial Handling

Some sentences are repeatedly partial even when the lemma itself is not a leech.
Treat sentence-level failure separately from word-level failure.

Rules:

- If a sentence has 3+ partial/no_idea outcomes or median response over 90s,
  mark it `needs_sentence_repair` or retire it from acquisition use.
- If it is authentic/book material, keep it for reading pack/manual context but
  remove it from early acquisition.
- If it is LLM material, regenerate a simpler sentence for the same target and
  risk policy.

Trade-offs:

- Retiring too aggressively can reduce material diversity.
- Keeping hard authentic sentences in reading packs preserves their value
  without letting them distort acquisition.

Acceptance:

- Repeated partials on the same sentence decline.
- No increase in acquiring words with zero active safe sentences.

### P1. Authentic Material Staging

Use authentic/book/corpus material as motivation and maintenance, not as raw
early acquisition material.

Policy:

- `source="book"` and `source="corpus"` can be primary cards for FSRS
  maintenance and reading packs.
- They should not be first acquisition cards for high-risk lemmas.
- Authentic windows should be promoted through the existing maintenance passage
  path, as specified in `spec-2026-05-10-hindawi-passage-promotion.md`.
- For book unlocks, create a pre-study list from high-leverage missing mapped
  lemmas, but audit homographs/function-word collisions before teaching.

Trade-offs:

- Slower path to "real text" in the main review queue.
- Better alignment with observed data: book primary acquisition had very poor
  comprehension and very long response times.

Acceptance:

- Book/corpus sentence reviews stay mostly in maintenance/passages.
- Authentic reading-pack completion increases without depressing acquisition
  first-pass accuracy.

## Recommended Rollout

### Phase 0: Instrumentation Only

Ship schema/logging for:

- dynamic sentence review context;
- pair-level confusion edges;
- static sentence difficulty;
- projection batch output.

No scheduler behavior changes except fixing the confused-rating docstring.

### Phase 1: Read-Only Projection Surfaces

Use projections in:

- word detail;
- intro/rescue card metadata;
- stats/debug views;
- research scripts.

Do not let projections reorder sessions yet.

### Phase 2: Acquisition-Safe Sentence Policy

Enable stricter sentence selection for early acquisition under an experiment
flag. Compare matched high-risk lemmas against current-policy controls.

Primary metrics:

- first-review success;
- reviews to graduation;
- days to graduation;
- sentence response time;
- sentence-less acquiring count.

### Phase 3: Verb-Aware Intro Cards

Enable only for verbs and 11+ form lemmas. Reuse existing forms/root/pattern
data. Avoid new LLM calls in session build.

### Phase 4: Confusor Treatment

Start with telemetry and reveal-phase candidate selection. Add scheduler
separation only after repeated pairs exist.

### Phase 5: Authentic Reading Packs

Proceed independently through the Hindawi passage-promotion path. Keep its
metrics separate from acquisition metrics.

## Experiment Design

Use between-lemma randomization inside risk strata rather than a global switch:

```text
stratum = high_risk_verb / high_form_nonverb / low_root_support / normal
condition = current_policy / strict_acquisition_policy / strict_plus_verb_intro
```

Avoid randomizing obvious safety fixes, such as telemetry and sentence leech
retirement. Those should ship universally.

Minimum useful comparisons:

- 50 high-risk lemmas per condition if feasible;
- otherwise use Bayesian sequential monitoring and keep the first 2 weeks as
  directional rather than conclusive.

Stop rules:

- 2-day rolling overall accuracy below 88%;
- acquiring backlog above 140 with no downward trend;
- sentence-less acquiring words increase by more than 10;
- median session response time rises by more than 25%;
- user-visible intro density/fatigue returns to the March/April failure mode.

## Algorithm/UI Implications

### Algorithm

- Make sentence difficulty state-aware. A sentence that is fine for maintenance
  can be bad for first acquisition.
- Treat verbs as a different class, not just as harder nouns.
- Use root family as both scaffold and interference source.
- Keep collateral credit unlimited, but stop using collateral-rich hard
  sentences as early acquisition vehicles.
- Use projections as a light policy input after they are measured, not as a
  black-box scheduler.

### UI

- High-risk intro cards should show the minimal extra structure needed to encode
  the word: root, pattern, key forms, known anchors, likely confusor.
- The review reveal phase is the right moment to collect confusor pair data.
- Sentence-level rescue should feel like "this sentence was replaced with a
  clearer one", not like the learner failed the word again.
- Stats should separate "learning volume" from "high-risk repair work" so a
  stricter acquisition policy does not look like stagnation.

## Open Questions

- Should high-risk verbs extend the Tier-0 anti-working-memory guard beyond 10
  minutes, or is stricter sentence choice enough?
- Should root-family known siblings boost stability only after pair-level
  confusion checks, to avoid boosting a root family that is causing interference?
- Should authentic sentence source carry a source penalty in projection scoring,
  or only in sentence selection policy?
- Is a static `sentences.difficulty_score` worth backfilling, or should all
  useful difficulty be user-specific snapshots?

## First Implementation PR Shape

Suggested first PR:

1. Add telemetry schema/migration.
2. Persist dynamic sentence review context on submit.
3. Add optional `confused_with_lemma_ids` to review submit schemas.
4. Add projection batch script with rule-based scoring.
5. Add stats/debug endpoint or script output for top projection risks.
6. Fix the confused-rating docstring.

This PR should not alter scheduling. It creates the measurement substrate for
the later behavior changes.
