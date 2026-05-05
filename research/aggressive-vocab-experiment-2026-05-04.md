# Aggressive Vocabulary Acquisition Experiment - 2026-05-04

## Question

Can Alif safely move from the recent ~15-25 new words/day pattern to a real
30 new words/day target while preserving existing vocabulary and moving the
curriculum toward general-reading frequency gaps?

The user goal is not to unlock one book. The goal is to reach real Arabic
reading as fast as possible, with enough high-frequency MSA coverage that
graded readers, children's books, news, Hindawi books, and Islamic/classical
material all become progressively less dictionary-bound.

## Production Snapshot

Snapshot time: 2026-05-04 evening UTC.

Current learner state:

| State | Count |
|---|---:|
| Known | 1,714 |
| Learning | 43 |
| Lapsed | 30 |
| Acquiring | 81 |
| Encountered | 296 |
| Suspended | 50 |

Recent activity:

| Window | Introductions | Sentence reviews | Word review logs | Accuracy |
|---|---:|---:|---:|---:|
| 1 day | 24 | 219 | 910 | 92.4% |
| 3 days | 44 | 390 | - | 93.1% |
| 31 days | 476 | 2,344 | - | 89.0% |

This is not an under-study problem. The learner is already doing enough
sentences to support 30/day if the queue is aimed correctly. The bottleneck is
that review debt and sentence coverage are not aligned with the user's current
goal.

## Frequency-Rank Diagnosis

Using the existing `lemmas.frequency_rank` as a proxy before the new
`frequency_core_entries` table is populated:

| Band | Known | Pipeline |
|---|---:|---:|
| <=500 | 148 / 196 | 156 / 196 |
| <=1,000 | 257 / 326 | 271 / 326 |
| <=2,000 | 391 / 494 | 413 / 494 |
| <=5,000 | 657 / 826 | 690 / 826 |

The confusing "top 1000" question came from denominator compression: only 326
current DB lemmas had proxy rank <=1,000. That makes `257/326` a coverage
metric over mapped rows, not an honest top-1,000 curriculum. The new
frequency-core table fixes this by keeping unmapped `lemma_id = NULL` rows, so
top 500 means the first 500 curriculum slots, including gaps.

## Due-Debt Diagnosis

At the snapshot, currently due cards split very unevenly:

| View | Main lane | Slow/artifact lane | Total |
|---|---:|---:|---:|
| Due now | 45 | 209 | 254 |
| Due + all acquiring | 48 | 228 | 276 |

Rank buckets for due + all acquiring:

| Bucket | Count |
|---|---:|
| 1-500 | 9 |
| 501-1,000 | 7 |
| 1,001-2,000 | 9 |
| 2,001-5,000 | 23 |
| 5,001-10,000 | 30 |
| 10,001-50,000 | 66 |
| >50,000 | 47 |
| NULL rank | 85 |

Most queue pressure is low/null-frequency artifact debt from book/OCR/scaffold
paths. That debt should not disappear, but it should not dominate the daily
definition of success.

## Sentence-Density Simulation

The simulation used production sentence mappings, active/inactive sentence
status, learner states, due timestamps, and the current validator constraints.
The target was 30 new words/day plus maintenance of active vocabulary.

Maintenance model for one full 30/day day:

| Component | Lemmas | Exposure units |
|---|---:|---:|
| FSRS due | 35 | 35 |
| Acquiring due | 38 | 38 |
| New-word acquisition exposures | 30 | 120 |
| Total | 75 distinct | 193 units |

Coverage results:

| Pool | Selected sentences | Units covered | Remaining units |
|---|---:|---:|---:|
| Active selector-only | 59 | 60 / 193 | 133 |
| Ideal pairs only | 97 | 193 / 193 | 0 |
| Ideal triples | 65 | 193 / 193 | 0 |
| Inactive salvage candidate pool | 107 | 150 / 193 | 43 |

Active sentences mostly cover one target each:

| Pool | Candidates | Due words covered | Density histogram |
|---|---:|---:|---|
| Active, all due | 514 | 182 / 237 | `{1:483, 2:30, 3:1}` |
| Inactive, all due | 6,250 | 231 / 237 | `{1:5561, 2:644, 3:44, 4:1}` |
| Active, main protect | 243 | 73 / 95 | `{1:232, 2:11}` |
| Inactive, main protect | 3,107 | 89 / 95 | `{1:2957, 2:142, 3:8}` |

The practical bottleneck is not daily volume. It is useful target coverage per
sentence. Moving from 1.0 to even 1.5 main-lane useful units/sentence materially
changes the workload.

## Experiment Hypotheses

H1. A 30/day target is feasible at >=90% recent accuracy if the daily goal
requires main-lane maintenance but only samples low/null-rank artifact debt.

H2. Frequency-core priority will improve general-reading progress faster than
book-specific priority alone because the top-N curriculum closes the largest
cross-domain gaps first.

H3. Due-targeting efficiency can improve within 48 hours by combining:

- demand-weighted multi-target grouping,
- validator-enforced 2+ target multi-target sentences,
- inactive due-dense salvage,
- oldest-overdue-first selection,
- freshness/diversity relaxation only for >=2 main-lane due words.

H4. The aggressive target is unsafe if accuracy falls below 90%, acquiring words
without sentences grow, or slow-lane debt begins increasing faster than it is
sampled.

## Implementation Decision

Ship one gated experiment, not a rewrite:

1. Keep box-1 and box-2 exposure targets unchanged.
2. Raise automatic intro target to 30/day through session-slot reservation and a
   high-accuracy backlog cap.
3. Define daily 100% as:
   - 30 new words introduced,
   - main-lane maintenance cleared,
   - today's slow-lane budget completed.
4. Make frequency core the primary new-word curriculum.
5. Preserve low/null-frequency artifact debt in a visible slow lane capped to
   10% of session budget.

## Frequency-Core Source Decision

The first shipped builder fuses source-level lemma ranks with these weights:

| Source | Weight | Reason |
|---|---:|---|
| CAMeL MSA | 1000 | Broad modern MSA scale, open list |
| Buckwalter/Parkinson | 900 | Learner-oriented reference dictionary |
| arTenTen export | 800 | Very large modern web corpus |
| KELLY | 600 | CEFR/learner-facing signal |
| Hindawi/books | 400 | Book-domain reading relevance |
| News | 300 | Current public prose |
| Islamic/classical | 150 | Important target domain, but genre-skewed |

Each source is collapsed to its best lemma-level rank before fusion. Unmapped
rows are retained.

## Rollout Criteria

Run for 48 hours after deployment.

Pass:

- >=30 introductions/day when recent accuracy stays >=90%.
- No growth in sentence-less acquiring words.
- Main-lane due count returns near zero by end of day.
- Slow-lane debt remains visible but does not dominate sessions.
- Useful main-lane units/sentence improves materially from the ~1.0 baseline.
- User subjective load remains high but not chaotic.

Stop or roll back:

- 2-day rolling accuracy <88%.
- Sentence-less acquiring count increases by >10.
- Acquiring backlog exceeds 140 with no downward trend.
- Main-lane due debt carries over by >40 words for two consecutive days.
- User reports review quality feels random or source labels become misleading.

## Sources

- CAMeL Arabic Frequency Lists: https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists
- Sketch Engine arTenTen: https://www.sketchengine.eu/artenten-arabic-corpus/
- KELLY Project, University of Leeds: https://ahc.leeds.ac.uk/languages-research-innovation/dir-record/research-projects/1007/kelly
- Buckwalter & Parkinson, Routledge Frequency Dictionary: https://www.routledge.com/A-Frequency-Dictionary-of-Arabic-Core-Vocabulary-for-Learners/Buckwalter-Parkinson/p/book/9780415444347
- Arabic E-Book Corpus / Hindawi corpus: https://researchdata.se/en/catalogue/dataset/2024-145
- Hindawi books: https://www.hindawi.org/books/
- OpenITI Corpus: https://openiti.org/projects/OpenITI%20Corpus.html
- Nation 2006: https://openaccess.wgtn.ac.nz/articles/journal_contribution/How_large_a_vocabulary_is_needed_for_reading_and_listening_/12552221
- Laufer & Ravenhorst-Kalovski 2010: https://files.eric.ed.gov/fulltext/EJ887873.pdf
- Uchihara, Webb & Yanagisawa 2019: https://doi.org/10.1111/lang.12343
