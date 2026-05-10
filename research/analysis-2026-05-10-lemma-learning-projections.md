# Lemma Learning Projection Analysis

Date: 2026-05-10
Data: fresh production SQLite snapshot pulled via `.backup` from `/opt/alif/backend/data/alif.db` into `backend/data/alif.prod.db`; prod logs mirrored into `backend/data/prod_logs/`.
Scope: 3,124 lemmas, 2,269 `user_lemma_knowledge` rows, 39,773 word reviews, 7,945 sentence reviews, 658 sessions, 92 review days.

## Executive Summary

The strongest predictors of better or worse lemma projection are not exotic
timing effects. They are:

1. **Morphological class**: verbs are consistently harder than nouns/adjectives.
   Verbs have 21.6% failure rate and 38.6% low-projection rate, versus nouns at
   10.9% / 16.1% and adjectives at 10.3% / 15.5%.
2. **Conjugation/form complexity**: lemmas with 11+ generated forms are almost
   all verbs and have 22.0% failure rate and 39.6% low-projection rate. Lemmas
   with 1-2 forms have 9.0% failure and 13.4% low-projection rate.
3. **Root-family support**: words with 0 currently learned root siblings have
   23.5% failure and 59.1% low-projection rate. This drops to about 10% failure
   and 16% low-projection once 3+ root-family members are in active memory.
4. **Role in sentence exposure**: target-heavy reviews are a warning signal.
   Lemmas shown as the explicit primary target 80-100% of the time have 25.8%
   failure and 52.1% low-projection rate. Lemmas mostly learned as collateral
   have much stronger projections. This is partly causal and partly scheduler
   confounding: hard words get more target slots.
5. **Authentic/book/corpus sentences are harder than generated LLM sentences**:
   book sentence comprehension is only 65.1% equivalent-understood score and
   median response is 116s, versus LLM at 81.8% and 27s. Corpus is 73.0% and
   48s. This argues for staged authentic-material entry, not raw replacement.
6. **Scaffold unknown count is a large sentence-level predictor**: reconstructed
   pre-review state shows understood rate drops from 70.8% with 0 unknown
   scaffolds to 56.7% with 1, 47.5% with 2, and 46.2% with 3+. The current
   `MAX_UNKNOWN_SCAFFOLD = 2` is probably too permissive for acquisition-heavy
   review sentences.
7. **The first acquisition pass is the bottleneck**: first reviews are 60.9%
   successful overall; first verb acquisition reviews are 42.9% successful,
   nouns 50.2%, adjectives 62.3%. By reviews 4-5 the system is mostly working.
8. **Confusions are mostly same-root or near-rasm neighbors** in the observed
   high-confusion set. Current logs only record `was_confused`, not the actual
   competing lemma, so pair-level confusion modeling is under-instrumented.

Current snapshot health is still good: among 1,965 reviewed canonical standard
lemmas with at least 3 reviews, 46.1% are high projection and 22.3% are low
projection. Most low projection mass is concentrated in verbs and recent
aggressive-acquisition/textbook/book cohorts.

## Outcome Definitions

I used multiple outcome views because "learn better" has no single sufficient
proxy:

- **Current projection**: FSRS stability and difficulty from
  `UserLemmaKnowledge.fsrs_card_json`.
- **Low projection**: lapsed/suspended, or stability `<3d` after 5+ reviews, or
  historical failure rate `>=25%`.
- **High projection**: `known`/`learning`, stability `>=30d`, failure rate `<=10%`.
- **Learning path**: days/reviews to graduation from acquisition, review order,
  spacing gaps, and acquisition-vs-FSRS status.
- **Confusion**: rating `2` plus `ReviewLog.was_confused`.

Because several fields are post-outcome artifacts, I treated them cautiously:
`memory_hooks_json` is often generated after failure, and target-heavy exposure
is partly caused by the scheduler focusing on hard lemmas.

## Current State

| State | Lemmas | Reviews | Mean Accuracy | Median Stability | Median Difficulty |
|---|---:|---:|---:|---:|---:|
| known | 1,824 | 38,234 | 89.8% | 74.4d | 2.09 |
| encountered | 201 | 10 | 68.8% | - | - |
| suspended | 86 | 451 | 40.7% | 0.21d | 6.41 |
| acquiring | 85 | 344 | 68.2% | - | - |
| learning | 44 | 274 | 69.7% | 2.31d | 2.12 |
| lapsed | 29 | 340 | 71.3% | 0.73d | 9.12 |

Stability distribution among reviewed rows:

- `known`: 774 lemmas at 90d+, 543 at 30-90d, 312 at 7-30d.
- `learning/lapsed/suspended`: concentrated below 3d or missing FSRS card.
- Reviewed analysis subset: 1,965 canonical, non-proper-name lemmas with 3+
  reviews.

## Linguistic Predictors

### POS

| POS | Lemmas | Reviews | Accuracy | Failure | Confusion | Median Stability | Low Projection |
|---|---:|---:|---:|---:|---:|---:|---:|
| verb | 417 | 7,387 | 78.4% | 21.6% | 6.1% | 34.3d | 38.6% |
| noun | 1,135 | 21,113 | 89.1% | 10.9% | 3.5% | 77.0d | 16.1% |
| adj | 220 | 3,459 | 89.7% | 10.3% | 4.0% | 83.0d | 15.5% |
| adv | 17 | 241 | 90.7% | 9.3% | 0.0% | 106.3d | 11.8% |
| expr | 11 | 122 | 91.8% | 8.2% | 0.8% | 188.4d | 9.1% |

Verb-specific first-pass data is the clearest signal:

| POS | First Review Success | Second | Third | Reviews 4-5 |
|---|---:|---:|---:|---:|
| verb | 42.9% | 56.9% | 66.9% | ~82% |
| noun | 50.2% | 68.0% | 80.3% | ~89% |
| adj | 62.3% | 66.7% | 76.8% | ~90% |

Interpretation: the algorithm can consolidate verbs, but it introduces them too
optimistically for the first retrieval. The issue is encoding, not long-term
spacing.

### Verb Forms And Awzan

Worst large groups:

| Wazn / Feature | Lemmas | Failure | Low Projection | Notes |
|---|---:|---:|---:|---|
| `form_1` | 197 | 22.1% | 41.6% | weak/sound conflation, short ambiguous stems |
| `form_4` | 18 | 17.8% | 38.9% | causative/hamza forms |
| `form_8` | 14 | 21.3% | 35.7% | `افتعل`/assimilations |
| `form_3` | 11 | 21.3% | 27.3% | participatory/long-vowel pattern |
| `fa'iil` | 78 | 4.9% | 3.8% | very strong projection |
| `fi'la` | 15 | 5.8% | 0.0% | very strong projection |

Binary grammar flags show the same pattern:

| Feature | Low Projection If Present | Low Projection If Absent | Difference |
|---|---:|---:|---:|
| has present form | 39.1% | 17.4% | +21.7pp |
| has masdar | 39.2% | 17.4% | +21.9pp |
| has active participle | 39.1% | 17.4% | +21.7pp |
| has passive participle | 40.0% | 17.5% | +22.5pp |
| `grammar_features` contains `form_1` | 29.5% | 20.1% | +9.4pp |
| `form_4` | 32.9% | 21.9% | +10.9pp |
| feminine | 16.5% | 23.9% | -7.4pp |
| tanwin patterns | 12.8% | 23.2% | -10.4pp |

This is not saying tanwin teaches words. It likely marks older, well-formed,
fully enriched dictionary entries and simple nominal material. The actionable
signal is the opposite: richly conjugated verbs need a different intro path.

### Root-Family Effect

| Learned Root-Family Count | Lemmas | Accuracy | Failure | Confusion | Median Stability | Low Projection | Median Days To Graduate |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0 | 93 | 76.5% | 23.5% | 2.2% | 74.5d | 59.1% | 3.23 |
| 1 | 725 | 85.5% | 14.5% | 3.9% | 57.8d | 24.1% | 2.64 |
| 2 | 441 | 87.2% | 12.8% | 4.8% | 67.1d | 21.5% | 1.96 |
| 3-4 | 437 | 89.6% | 10.4% | 3.5% | 80.6d | 16.0% | 1.32 |
| 5+ | 269 | 89.8% | 10.2% | 4.8% | 78.2d | 16.4% | 0.99 |

Root siblings help, but not all the way: confusion rate can rise slightly with
root-family richness because same-root alternatives become available. The UI
should use root family as a support scaffold and as an interference warning.

### Form Count

| Forms Count | Lemmas | Accuracy | Failure | Confusion | Median Stability | Low Projection | Verb Share |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1-2 | 866 | 91.0% | 9.0% | 2.9% | 107.3d | 13.4% | 0.2% |
| 3-5 | 626 | 87.2% | 12.8% | 4.4% | 58.7d | 20.8% | 1.8% |
| 6-10 | 12 | 66.1% | 33.9% | 11.9% | 36.4d | 58.3% | 58.3% |
| 11+ | 414 | 78.0% | 22.0% | 6.0% | 33.9d | 39.6% | 95.9% |

This is one of the cleanest static signals available before much review data.

## Timing And Spacing

### Review Order

| Review Order | Events | Success | Miss | Confusion | Acquisition Share |
|---|---:|---:|---:|---:|---:|
| 1 | 2,052 | 60.9% | 37.8% | 1.3% | 79.5% |
| 2 | 2,029 | 73.9% | 24.3% | 1.8% | 55.4% |
| 3 | 1,997 | 81.8% | 17.1% | 1.2% | 55.4% |
| 4-5 | 3,788 | 86.2% | 12.2% | 1.6% | 47.7% |
| 6-10 | 7,803 | 90.8% | 7.2% | 2.0% | 25.5% |
| 11+ | 22,104 | 97.3% | 1.7% | 1.1% | 5.0% |

The first two retrievals are where algorithm/UI leverage is highest.

### Gap Since Previous Review

| Gap | Events | Success | Miss | Confusion |
|---|---:|---:|---:|---:|
| same-minute | 4,944 | 96.7% | 2.8% | 0.5% |
| <1h | 4,860 | 95.7% | 3.8% | 0.6% |
| 1-4h | 3,374 | 95.6% | 3.9% | 0.5% |
| 4-12h | 4,225 | 94.2% | 4.8% | 0.9% |
| 12-24h | 4,569 | 94.4% | 4.5% | 1.1% |
| 1-3d | 7,916 | 92.2% | 6.3% | 1.5% |
| 3-7d | 4,272 | 89.4% | 8.1% | 2.4% |
| 7d+ | 3,561 | 81.7% | 14.5% | 3.7% |

Spacing is behaving sensibly. Long gaps expose weak items; they are not the root
cause. Do not shorten global retention blindly. Instead, treat long-gap failures
as a diagnostic for morphology/confusion support.

### Time Since Intro

| Time Since Intro | Events | Success | Miss | Confusion | Acquisition Share |
|---|---:|---:|---:|---:|---:|
| <10m | 1,910 | 53.6% | 43.8% | 2.7% | 53.6% |
| 10m-1h | 324 | 80.9% | 18.8% | 0.3% | 73.1% |
| 1-4h | 457 | 81.0% | 18.2% | 0.9% | 75.3% |
| 4-12h | 1,180 | 77.9% | 21.3% | 0.8% | 74.4% |
| 12-24h | 1,333 | 84.4% | 14.4% | 1.2% | 63.2% |
| 1-3d | 4,218 | 90.8% | 8.1% | 1.1% | 58.6% |
| 3-7d | 5,039 | 93.1% | 5.8% | 1.1% | 32.0% |
| 7d+ | 20,388 | 94.4% | 3.9% | 1.7% | 5.5% |

The `<10m` bucket is now intentionally harder after the fast-grad guard: it
contains real first retrievals instead of working-memory false positives. That
change looks conceptually right.

### Time Of Day / Week

Time-of-day effects are small. Best hours are around 14:00 and 20:00 Oslo
(92-93% success); lower hours are 07:00, 18:00, 22:00, and Sunday. Sunday is the
only visible day-level dip: 88.4% success vs 90.5-92.2% on other days.

I would not change scheduling based on clock time. At most, stats could flag
"Sunday sessions are tougher" if the pattern persists.

## Sentence And Context Effects

### Word Review Outcomes By Source And Role

| Sentence Source / Role | Reviews | Accuracy | Miss | Confusion | Median Response |
|---|---:|---:|---:|---:|---:|
| LLM collateral FSRS | 24,067 | 95.8% | 2.8% | 1.4% | - |
| LLM acquisition collateral | 4,571 | 82.3% | 16.3% | 1.4% | - |
| LLM primary FSRS | 3,386 | 86.1% | 12.2% | 1.8% | 25.6s |
| LLM primary acquisition | 3,931 | 74.8% | 24.0% | 1.2% | 27.8s |
| corpus primary FSRS | 44 | 68.2% | 25.0% | 6.8% | 45.6s |
| corpus primary acquisition | 14 | 57.1% | 28.6% | 14.3% | 57.6s |
| book primary FSRS | 21 | 85.7% | 9.5% | 4.8% | 93.7s |
| book primary acquisition | 27 | 44.4% | 55.6% | 0.0% | 102.1s |

Book/corpus primary acquisition is too hard in the current data. The sample is
small, but it is directionally consistent with sentence-level comprehension.

### Sentence Comprehension

| Source | Sentence Reviews | Understood | Partial | Equivalent Score | Median Response | Median Length |
|---|---:|---:|---:|---:|---:|---:|
| book | 53 | 30.2% | 69.8% | 65.1% | 115.7s | 9 |
| corpus | 74 | 45.9% | 54.1% | 73.0% | 47.6s | 8 |
| LLM | 7,777 | 63.7% | 36.2% | 81.8% | 27.0s | 6 |
| claude_code | 29 | 75.9% | 24.1% | 87.9% | 21.0s | 4 |

Length matters, but less than source/authenticity:

| Length | Reviews | Understood | Equivalent Score | Median Response |
|---|---:|---:|---:|---:|
| <=4 | 1,471 | 66.3% | 83.1% | 16.1s |
| 5-6 | 3,331 | 62.8% | 81.4% | 25.5s |
| 7-8 | 1,853 | 64.7% | 82.3% | 31.3s |
| 9-10 | 1,062 | 61.0% | 80.4% | 41.8s |
| 11+ | 225 | 52.9% | 76.4% | 57.8s |

`sentences.difficulty_score` is entirely NULL in prod. The code reasons about
sentence difficulty using computed stability/scaffold metrics at selection
time, but that is not persisted. This is a major analytics gap.

### Scaffold Unknown Count

A subagent reconstructed scaffold unknown count from each sentence review's
per-word pre-review state:

| Unknown Scaffolds | Sentence Reviews | Understood |
|---:|---:|---:|
| 0 | 4,568 | 70.8% |
| 1 | 2,168 | 56.7% |
| 2 | 671 | 47.5% |
| 3+ | 344 | 46.2% |

One unknown scaffold is already a major difficulty jump. Two unknown scaffolds
may still be useful for stretch/authentic-reading cards, but they are too hard
for ordinary acquisition review. This is stronger evidence than raw sentence
length.

### Repeated Sentence Exposure

Repeated exposure helps on average:

| Sentence Exposure Ordinal | Understood |
|---:|---:|
| 1st | 60.9% |
| 2nd | 75.5% |
| 3rd | 86.1% |
| 4+ | 87.5% |

But repeated partials should create a sentence-level intervention. Example:
sentence `43504`, `اِشْتَغَلَ الطَّالِبُ عَلَى التُّوبِ الْجَدِيدِ فِي الصَّبَاحِ الْبَاكِرِ.`,
was partial 12 times. That is not a spacing problem; it is a bad sentence/lemma
mapping/context problem for the learner.

Examples of bad sentence choices by residual correctness:

- `3825`: `قَالَ الأُسْتَاذُ إِنَّهَا لَحْظَةٌ جَمِيلَةٌ فِي نِهَايَةِ هٰذِهِ الرِّسَالَةِ.`
  had 35.7% word correctness.
- `40225`: `تُقَاوِمُ الدُّوَلُ الْإِرْهَابَ بِقُوَّةٍ.` had 25.0% correctness;
  the political/abstract register is too high for ordinary review.
- `7141`: `تَمَكَّنَ النَّاسُ مِنْ تَصْوِيرِ تِمْثَالِ الحُرِّيَّةِ.` had cultural
  and named-object load beyond the target lemma.

Helpful sentence shapes were short, concrete, and imageable. Example:
`يُوجَدُ عَظْمٌ حَادٌّ فِي حَنْجَرَةِ البَطَّةِ.` produced strong outcomes even
for a generally confused lemma.

### Target Share

| Target Share | Lemmas | Accuracy | Failure | Confusion | Median Stability | Low Projection |
|---|---:|---:|---:|---:|---:|---:|
| 0% | 297 | 95.1% | 4.9% | 3.1% | 137.4d | 11.1% |
| 0-20% | 621 | 91.8% | 8.2% | 3.1% | 104.0d | 11.8% |
| 20-50% | 660 | 84.1% | 15.9% | 4.9% | 57.7d | 25.5% |
| 50-80% | 293 | 79.0% | 21.0% | 4.1% | 33.7d | 39.6% |
| 80-100% | 94 | 74.2% | 25.8% | 6.5% | 22.8d | 52.1% |

The interpretation is not "collateral is magic." It is:

- Good scaffolds naturally become high-stability through collateral exposure.
- Hard new words need primary slots, so target share is also a marker of
  difficulty.
- The scheduler should watch for lemmas that remain target-heavy after several
  reviews and then change treatment, not simply give them more of the same.

## Confusions And Confusors

Observed confusion is sparse: about 595/39,773 review events. It is still
high-signal when present.

Examples from high-confusion lemmas:

| Lemma | Issue Pattern | Nearby Confusors |
|---|---|---|
| `ظُهُورٌ` appearance | same-root + visual collision | `ظَهَرَ`, `مَظْهَرٌ`, `زُهُور`, `طُيُور` |
| `اِسْتِقْبَالٌ` reception | same-root nominal/verb/future family | `اِسْتَقْبَلَ`, `المُسْتَقْبَل`, `اِسْتِعْمَالٌ` |
| `عَظْمٌ` bone | short rasm-dense noun | `العَظِيم`, `مُعْظَم`, `عَلِمَ`, `عَام` |
| `صَلَّى` to pray | weak-final verb + function-word visual | `صَلاة`, `عَلى`, `إِلَى` |
| `إِغْلاق` closing | masdar vs verb, similar nominal patterns | `أَغْلَق`, `إِطْلَاق` |
| `حَسَبَ` calculate | same root semantic cluster | `حِساب`, `مُحاسِب`, `حَاسُوب` |
| `سَعِد` be happy | same-root adjective/verb/help collision | `سَعيد`, `ساعَد`, `سُعُودِيّ` |

The pattern is mostly:

- same root, different pattern/POS;
- very short bare forms with similar rasm;
- masdar vs finite verb;
- weak-final verbs and high-frequency particles (`صَلَّى` vs `عَلى`/`إِلَى`);
- visually similar textbook vocabulary introduced in close time windows.

Instrumentation gap: `was_confused` does not store "confused with X". The
current confusion service can propose candidates after the fact, but that is not
the same as observed user confusion. Add pair-level logging.

## Concrete Hard Lemma Clusters

### Verb Encoding Failures

Examples with high failure / low projection:

- `لَوْح` "to wave" - 14.3% accuracy, suspended.
- `يَضرِبَ` "strikes, sets" - 16.7% accuracy, suspended.
- `طَفَا` "to float" - 16.7% accuracy, suspended, confusion 33%.
- `اسْتَلَقَى` "to lie down" - 16.7% accuracy, suspended.
- `مَنَّى` "to arouse desire" - 20.0% accuracy, suspended.
- `أَوِد` "to want" - 20.0% accuracy, suspended.
- `تَابَعَ` "to continue" - 25.0% accuracy, suspended.
- `راقَب` "to monitor" - 25.0% accuracy, suspended.

Common traits: verb, many forms, often low-frequency, often ambiguous root
family, often a generated form/gloss mismatch risk (`noun` POS assigned to a
verb-looking item appears in several rows).

### High-Projection Concrete Nouns/Adjectives

Examples:

- `دَجاج` chicken, `كَلْب` dog, `شَقَّة` apartment, `باب` door.
- `سُؤال`, `طَرِيق`, `مَحَلّ`, `مَسْجِد`, `حَيَوَان`.
- `سَعيد`, `أَزْرَق`, `واسِع`, `غَريب`.

These tend to be concrete, imageable, short-to-medium, often nominal, often with
stable sentence contexts and many collateral exposures.

## Multivariate Checks

A simple linear model for `log(stability)` using static, lexical, sentence-role,
and source features had adjusted R² ≈ 0.41 over 1,845 FSRS rows. Strongest
positive terms:

- days since introduction;
- collateral share;
- learned root-family count;
- longer bare form, mildly;
- tanwin/feminine nominal markers, mildly.

Strongest negative terms:

- high acquisition/target share;
- verb/form features, especially forms 3/8 and form-heavy rows;
- missing POS/enrichment source groups;
- memory hooks, but this is reverse causality because hooks are often generated
  after failure.

A logistic model for low projection had pseudo-R² ≈ 0.31. Reliable directional
signals:

- acquisition share increases low-projection risk;
- collateral share decreases risk;
- story/book/wiktionary/textbook sources increase risk relative to the easiest
  baseline after controlling for other fields;
- more learned root siblings decrease risk;
- more recent introductions increase risk;
- etymology/enrichment presence appears protective, but the missing group is
  tiny and likely data-quality confounded.

## Data And Instrumentation Gaps

1. `sentences.difficulty_score` is completely NULL in prod.
2. `sentence_selector` computes rich `selection_info`, but it is not persisted
   with `card_shown` or `review_log`.
3. `ReviewLog.was_confused` lacks pair-level confusor identity.
4. `response_ms` is useful only after filtering; outliers include pauses,
   backgrounding, and offline queue effects.
5. `memory_hooks_json` and `etymology_json` are not clean causal predictors
   because they correlate with enrichment timing and failure-triggered generation.
6. Some rows still show POS/gloss oddities that matter for learning, e.g. verb
   meanings stored as `noun` POS. These rows often appear in hard clusters.
7. Card-position logging only exists from 2026-05-06 onward. Current joined
   card position sample is 522 sentence cards, useful but not enough for a
   stable fatigue model.
8. There is no sentence-level leech/regeneration path for repeated partials.
   Lemma leech handling exists, but a sentence can fail repeatedly even when the
   lemma is not the only issue.

## Algorithm And UI Implications

### 1. Add A Cached `learning_projections` Layer

Do not put this computation inside `build_session()`. Add an offline/read-side
analytics layer that computes per-canonical-lemma:

- projection band: easy / normal / fragile / leech-risk;
- expected remaining acquisition burden;
- risk reasons: verb form complexity, no root-family support, high target share,
  same-root confusors, visual confusors, authentic-sentence overload;
- recommended treatment: contrast card, simpler sentence, extra same-day
  retrieval, delayed authentic context, root-family bridge.

Use it first for explanations and UI targeting. Only later let it lightly
modify `sentence_selector` priority.

### 2. Verb-Aware Acquisition

For verbs and 11+ form lemmas:

- intro card should foreground one finite form + one stable gloss, not the whole
  paradigm;
- first sentence should be very short and semantically concrete;
- require an extra first-day retrieval before graduating from box 1;
- prefer same-root bridge only after one successful retrieval, not before;
- show masdar/participle contrasts only after the base verb is stable.

This targets the 42.9% first-review verb success problem.

### 3. Root-Family Support With Interference Control

Root family helps once at least 2-3 siblings are known, but it can also confuse.
Use root family in two modes:

- **Support mode**: "You know `سَعيد`; `سَعِد` is the verb."
- **Contrast mode**: when same-root confusors exist, explicitly distinguish POS
  and pattern, e.g. `إِغْلاق` (closing noun) vs `أَغْلَق` (he closed).

Do not introduce many same-root siblings in one session.

### 4. Sentence Selection

For fragile lemmas:

- generated sentence first, authentic sentence later;
- avoid book/corpus primary acquisition until at least box 2 or one successful
  retrieval;
- prefer 0 unknown scaffolds; allow 1 carefully; reserve 2+ for explicit stretch
  or authentic-reading cards;
- cap sentence length at 5-7 words for first verb retrievals;
- if a lemma remains target-heavy after 5+ reviews, switch to contrast/memory
  support rather than continuing plain target repetitions.

Authentic material should be a progression step, not the default first teaching
context.

### 5. Confusion UI

Current confusion help is directionally right. Improve it by storing and ranking
actual observed confusors:

- log candidate selected or "looked like X" when the user marks confused;
- record surface form, sentence position, and proposed confusor lemma IDs;
- show only one contrast in wrap-up, not a list of possible explanations;
- prioritize same-root/POS contrasts over generic visual distance when both
  exist.

### 6. Telemetry To Add

Persist on `card_shown` or `review_log`:

- `selection_info.reason` and score components;
- primary due lemma IDs and collateral lemma IDs;
- unknown scaffold count at selection time;
- sentence length and source;
- card index and total cards for all sessions;
- intro-card delay before first retrieval;
- pair-level confusion candidate.

These are cheap, no-LLM fields and would make the next analysis much more
causal.

## Recommended Next Steps

1. Add pair-level confusion telemetry and persist selector metadata.
2. Build offline `learning_projections` export over prod snapshot; do not affect
   scheduling yet.
3. Add UI display in word detail / intro / wrap-up:
   "why this word is hard/easy" with one recommended contrast.
4. Add a verb-aware first-day treatment experiment for form-heavy verbs.
5. Re-run after 2 weeks with the aggressive 30/day setting to separate
   transient new-cohort effects from stable learner patterns.
6. Add a sentence-level leech rule: repeated partials/no-idea for the same
   sentence should trigger simplify/regenerate/explain rather than more repeats.
