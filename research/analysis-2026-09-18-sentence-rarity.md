# Rare words in review sentences: a missing-rank bug, a sampler that doubles rarity, and a revised strategy

**Date:** 2026-09-18 · **Data:** production backup `alif_20260918_091026.db` (snapshot 07:08 UTC), read-only copy ·
**Code:** [`sentence-rarity-2026-09-18/analyze_rarity.py`](sentence-rarity-2026-09-18/analyze_rarity.py) ·
**Results:** [`results.json`](sentence-rarity-2026-09-18/results.json) (ranks as stored),
[`results-repaired-ranks.json`](sentence-rarity-2026-09-18/results-repaired-ranks.json) (ranks the fixed code assigns) ·
**Follows:** [spec 2026-09-18, Workstream B](spec-2026-09-18-test-isolation-sentence-quality.md)

The spec found that forced and word-salad review sentences carry about twice as many rare words
(effective rank above 5,000, or unranked) as natural ones. This analysis asks where those rare
words come from before changing generation. "Rare non-target" below means content words, classified by
the resolved lemma, other than the sentence's target words.

## 1. Since 31 March, no newly created word has received a frequency rank

`lemma_quality._CAMEL_CACHE` resolved to `backend/app/data/MSA_freq_lists.tsv`; the file lives in
`backend/data/`, where `build_frequency_core.py` and `backfill_frequency.py` read it. The path has
been wrong since the centralized quality gate landed (`fb5efbc`, 2026-03-31). `_load_rank_map()`
logged a warning and returned an empty map, so `assign_frequency_rank()` never assigned anything.
That affected every lemma created through `run_quality_gates()` and every `/api/discover` import.
In the backup, **no lemma with id above 2912 has a `frequency_rank`**; 1,700 lemmas have none.

Consequences:

- **Generation and the spec's rarity metric.** Lemmas without a frequency-core link were counted as
  rare. Before the repair, common words led the rare scaffold: شَدِيد "severe", أَصْبَحَ "to become",
  بَقِيَ "to remain", رَأَى "to see", وَالِد "father".
- **Session priority.** `frequency_priority_weight()` gives an unranked due word 0.70, against
  1.50–2.20 for ranks up to 5,000. A due أَصْبَحَ competed as if it were obscure. فَرَغَ, one of the
  starved Box-1 words, is unranked. The other two, رَاوَدَ and صَرَعَ, have core ranks, so this is
  at most one contributor to the starvation, not its explanation.
- **Leech reintroduction.** The reintroduction delay orders by effective rank, and unranked sorts last.
- **The discover API used by Bookifier.** With an empty map, `selection="distinctive"` scored every
  candidate as `count × log10(100010)`, which is just "most frequent in this text".
  `common_first` ordered by count, and `freq_rank` was always null. Very common words were offered as
  distinctive and imported, then stored without a rank.

**Fix (this PR).** Point `_CAMEL_CACHE` at `backend/data`, and make the loader keep only the head of
the list. The full file has 10.5M forms; loading it took 13.3 s and about 2 GB of memory, which a
corrected path would have pinned inside the web process on first use. The file is sorted by
descending count, so the loader stops below a count of 50 and keeps ranks up to 100,000. Every
consumer already treats ranks beyond that like "not in the list": both get priority weight 0.70, and
discover's OOV rank is 100,000. The capped loader takes 0.8 s and 263 MB transiently. For the 4,352
lemma forms in the database it reproduces the full loader's ranks within a few places: dropping
low-count alef variants shifts a few forms, e.g. واسع 3,616 → 3,615.

**Backfill (not run).** `scripts/backfill_missing_frequency_ranks.py --dry-run` on the backup
assigns ranks to 1,476 of the 1,700 lemmas, 801 of them in active study. The other 224 lie past rank
100,000 and stay unranked. It fills only NULL ranks through the same `assign_frequency_rank()` and
never changes an existing one.

## 2. How rare is the vocabulary, once the ranks are right?

Among the 2,802 active content lemmas (known, learning, lapsed or acquiring), **288 (10.3%) are rare.**
Before the repair the figure was 10.9%: the repair mostly promoted common words out of "rare", but
the pool was never dominated by rare words. The rare ones come from:

| Source (ULK) | Rare lemmas |
|---|---:|
| textbook_scan | 128 |
| bookifier | 104 |
| book | 26 |
| dragoman | 14 |
| other | 16 |

Bookifier added 104 of them in July. Since July they account for 1,988 of the 2,644 rare-scaffold
appearances in generated sentences. They are story vocabulary, not slang:

| Appearances as scaffold since July | Word | CAMeL band |
|---:|---|---|
| 67 | صَبِيّ boy | >20k |
| 37 | جُنْد troops | >20k |
| 36 | وَالِد father | 5–20k |
| 29 | رَضِيع infant | >20k |
| 29 | أَرِيكَة sofa | >20k |
| 26 | حَلَّاق barber | >20k |
| 25 | مِنْضَدَة table | >20k |

CAMeL ranks come from a news-heavy MSA corpus and count surface forms. `assign_frequency_rank`
tries the ال-form only when the bare form is missing entirely, so nouns that usually appear with ال
(صَبِيّ) are probably under-ranked. For a learner reading fiction, "rare" is partly a property of
the corpus, not of the word.

## 3. The prompt sampler doubles the rare share

`sample_known_words_weighted` weights each word by `1/(1 + sentences containing it)`, multiplies by
the at-risk boost, jitters, **sorts by weight** and keeps the top 500. Over 200 seeded draws on the
backup (repaired ranks):

| Sampler | Rare share of the 500 | Rare share of the first 100 |
|---|---:|---:|
| Current (inverse count × at-risk) | **22.2%** | **35.2%** |
| Inverse count only | 25.1% | 32.6% |
| Uniform random | 10.3% | 10.7% |

The inverse-count weight does the damage. A just-imported word has no sentences and weighs about
27 times more than a word with 26, so a batch import floods the prompt. Rare words also stay
under-used (median 12 sentences as scaffold against 26 for common words), because they are hard to
fit into ordinary sentences, and so they keep their high weight. **The at-risk boost added on
2026-06-06 is not the driver**: without it the rare share is slightly higher, because the boost
also lifts fragile common words.

At about five scaffold content words per sentence, a 22% rare offer predicts about 1.1 rare
non-target words per sentence. The observed September mean is 1.1.

## 4. The trend follows the imports

Every generated sentence, including retired ones, by creation month (repaired ranks):

| Month | Generated | Mean rare non-target | Rare share of scaffold | With ≥2 rare |
|---|---:|---:|---:|---:|
| May | 5,038 | 0.07 | 1.8% | 29 |
| June | 3,086 | 0.10 | 2.7% | 34 |
| July | 2,743 | 0.72 | 17.1% | 450 |
| August | 837 | 0.50 | 10.4% | 86 |
| September | 241 | 1.10 | 22.4% | 73 |

The jump coincides with the July Bookifier imports. The September rise has the same mechanism acting
on a smaller, due-word-driven generation volume under the maintenance experiment.

## 5. What the spec's pre-gate (B2) would do

"Reject a generated sentence with more than one rare non-target content word", applied to the 1,570
active reviewable generated sentences, with repaired ranks:

| Rule | Rejected | Words due within 7 days left with no reviewable sentence |
|---|---:|---:|
| more than 0 rare | 576 (36.7%) | 156 |
| **more than 1 rare** | **170 (10.8%)** | **36** |
| more than 2 rare | 43 (2.7%) | 8 |

On the spec's 60 rated sentences, the "more than 1" rule rejects 57% of word salad, 15% of forced
sentences and 3% of natural ones. The "more than 0" rule would reject 27% of natural sentences.
**Rarity separates word salad from natural sentences well, but it does not catch most forced
sentences**, so the graded coherence review (B3) is still needed. Retiring the existing rejects (B6)
must regenerate first for the 36 stranded due words.

The stored-rank run rejects more (219 at "more than 1"), because unranked common words count as rare.
**Shipping B1 or B2 before the rank backfill would push أَصْبَحَ and رَأَى out of the scaffold and
reject good sentences.**

## 6. Revised strategy

1. **Fix the ranks first** (this PR), then run the backfill on production. This comes before
   everything else, because every rarity-based rule depends on it. It also changes due-word priority
   for the 801 active lemmas that gain a rank, so it is a maintenance-experiment phase boundary.
2. **B1, rebalance the offer.** Cap the rare stratum at about 5% of the 500 offered words (25 words),
   choose within each stratum by the existing inverse-count × at-risk weight, and shuffle the result
   so the model does not read the rarest words first. At five scaffold words per sentence, this
   predicts about 0.25 rare non-target words per sentence, the level of the natural rated sentences
   (0.30).
3. **B2, reject more than one rare non-target word.** It costs no LLM call. With B1 in place it
   should rarely fire; it is the backstop for batches dominated by rare targets.
4. **Give rare words their own sentences.** Target words are exempt from both rules, so a rare word
   is still practised, as the target of a sentence whose other words are common. This is the
   intended division: a rare word is the one hard thing in its own sentence, not scaffold in
   someone else's.
5. **Book vocabulary belongs with its text.** Most rare words come from Bookifier and books the
   learner is reading. Authentic sentences from those texts, through the existing corpus pipeline
   and the Momo reading bridge, are better practice for them than generated sentences.
6. **Make rarity corpus-aware (idea, not scheduled).** A word that is frequent in the learner's own
   reading (the Hindawi and Momo corpora, or `FrequencyCoreEntry.hindawi_rank`) should not count as
   rare because it is uncommon in news. Evaluate this before tightening B1's quota further.

## Caveats

- The sentence ratings are the spec's single-rater sample, judged mostly from the English.
- CAMeL ranks are surface-form ranks; see §2.
- The analysis script classifies content words by the resolved lemma, as CLAUDE.md requires, but
  counts each distinct non-target lemma once per sentence.
