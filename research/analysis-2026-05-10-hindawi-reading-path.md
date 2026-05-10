# Hindawi Children Reading Path Analysis

**Date:** 2026-05-10
**Prod DB:** `/opt/alif/backend/data/alif.db`
**Raw corpus:** `/tmp/hindawi.parquet` (`children.stories`, 167 books, 1,910,060 words)

## Question

How far is the current production lemma knowledge from letting the learner comfortably read Hindawi children's books, and are the gaps broad/random or concentrated enough that a small set of common words unlocks meaningful coverage?

## Grounding

Current prod snapshot:

- Canonical standard lemmas: 2,835
- Strict known lemmas: 1,820
- Practical reading band (`known`, `learning`, `acquiring`, `lapsed`): 1,982
- Imported Hindawi corpus rows: 6,465 sentences
- Active corpus rows: 100

The project invariant matters here: canonical lemmas are the scheduling unit, and proper names / junk / onomatopoeia are excluded from content coverage. Function words were excluded using the existing `sentence_validator._is_function_word()` set.

## Imported Corpus Sentence Pool

The imported Hindawi sentence pool is much closer than full raw books.

Using the practical reading band:

| Metric | Current |
|---|---:|
| Mean content coverage | 86.0% |
| Perfect sentences | 2,834 / 6,465 |
| <= 1 unknown content item | 5,328 / 6,465 |
| <= 2 unknown content items | 6,239 / 6,465 |
| <= 3 unknown content items | 6,427 / 6,465 |
| >= 90% content coverage | 2,930 / 6,465 |

Missing-token concentration is strong:

| Add top missing mapped lemmas | Mean coverage | Perfect | <= 3 unknown |
|---:|---:|---:|---:|
| 0 | 86.0% | 2,834 | 6,427 |
| 25 | 89.7% | 3,530 | 6,443 |
| 50 | 91.2% | 3,838 | 6,454 |
| 100 | 92.7% | 4,173 | 6,458 |
| 200 | 94.0% | 4,522 | 6,461 |

Top 25 missing mapped lemmas cover 42.5% of all mapped missing-token occurrences in the imported corpus; top 100 cover 76.2%; top 200 cover 91.1%.

Most useful missing items in the imported pool are not exotic:

- `لَمْ` (did not), 149
- `أَيّ` (any), 67
- `بَعْد` (after), 66
- `مَاذَا` (what), 61
- `رَدَّ` (reply/return), 57
- `زَالَ` (cease), 51
- `جَرَى` (run), 47
- `أَمام` (in front of), 44
- `بَيْن` (between), 44
- `كَيْفَ` (how), 41

There are also obvious audit-before-teach rows: `فِلْمٌ` may be catching `فلم` = `فـ + لم`, and several high-frequency entries are currently `suspended`.

## Raw Full Books

The raw full-book pass used deterministic lookup only with CAMeL fallback disabled for speed, so treat these numbers as conservative. A targeted full-CAMeL validation on leading candidates is below.

Conservative full-corpus estimate:

| Metric | Value |
|---|---:|
| Content tokens analyzed | 1,096,302 |
| Strict known token coverage | 50.3% |
| Practical reading-band token coverage | 55.7% |
| Mapped missing tokens | 6.9% |
| Unmapped content tokens | 37.5% |
| Ceiling if every mapped missing lemma were learned | 62.5% |

This means full-book comfort is not primarily blocked by "different known DB lemmas forever." It is blocked by the raw-book mapping/import gap: many common surfaces are not mapping to current lemmas or are not in the lemma DB.

Top raw unmapped surfaces are broad, common, and actionable:

- `اخرى`, `شيئا`, `قائلا`, `مما`, `وعندما`, `فيما`, `راى`, `جميعا`, `شديد`, `تكن`, `يجب`, `فلما`, `ولما`, `ربما`, `سوى`

These are exactly the sort of forms that should be either mapped through morphology/function-word handling or imported as standard lemmas, not left as per-book surprises.

## Candidate Books

Full-CAMeL validation on likely easiest candidates:

| Book | Words | Active coverage | Unmapped | If top 25 mapped gaps learned | Mapped ceiling |
|---|---:|---:|---:|---:|---:|
| `لَيْلَى وَالذِّئْبُ` | 1,780 | 82.1% | 12.3% | 87.0% | 87.7% |
| `سَفْرُوتُ الْحَطَّابُ` | 967 | 80.0% | 14.4% | 85.6% | 85.6% |
| `هايدي` | 17,615 | 80.2% | 13.4% | 83.3% | 86.6% |
| `دِمْنَةُ وَشَتْرَبَة` | 1,157 | 75.4% | 18.5% | 81.5% | 81.5% |
| `أبوُ خَربُوش` | 512 | 71.0% | 16.8% | 83.2% | 83.2% |
| `الْأَرْنَبُ وَالصَّيَّادُ` | 922 | 66.5% | 14.6% | 85.4% | 85.4% |

`لَيْلَى وَالذِّئْبُ` is the best immediate target among checked short books. Its remaining mapped gaps are small and teachable (`لَمْ`, `كَذَبَ`, `جَرَى`, `تَعَوَّد`, `أَيْن`, `خَدَعَ`), but the unmapped surfaces still cap it below comfortable whole-book reading until imported/fixed.

## Longer Passage Follow-up

The current longer-passage work stores cohesive review passages as `Story(format_type="maintenance_passage")` plus `Sentence(source="passage")` rows, and the selector groups only those intentional passage rows by shared `story_id`. That means Hindawi should not be used by opportunistically bundling adjacent unrelated corpus sentences. It can be used by promoting selected consecutive raw-book windows through the same passage storage path after quality/translation checks.

I added `backend/scripts/rank_hindawi_passages.py` as a read-only scouting tool for that promotion step. It ranks consecutive 3-5 sentence windows from raw Hindawi parquet by current prod lemma knowledge, with a fast broad mode (`--disable-camel`) and full-CAMeL title-specific reruns.

Full-CAMeL checks found immediately viable authentic windows:

| Book/window | Current active coverage | Unmapped | Note |
|---|---:|---:|---|
| `دِمْنَةُ وَشَتْرَبَة`, start sentence 10 | 100.0% | 0.0% | Strong 4-sentence dialogue around the bull/lion scene. |
| `لَيْلَى وَالذِّئْبُ`, start sentence 45 | 100.0% | 0.0% | Strong 4-sentence wolf/dialogue window. |
| `لَيْلَى وَالذِّئْبُ`, start sentence 46 | 100.0% | 0.0% | Adjacent viable dialogue window. |
| `الْأَرْنَبُ وَالصَّيَّادُ`, start sentence 14 | 90.5% | 0.0% | Reaches 100% after tiny mapped pre-study list (`لَمْ`, `مرحة`). |

The practical next step is an importer/promoter that takes one chosen Hindawi window, runs translation and the existing quality gate, then stores it as a maintenance passage. The existing review selector should then surface it naturally as a cohesive longer passage.

## Fastest Path

1. **Use Hindawi as a reading-pack source immediately, not as whole books yet.** There are already 6,465 imported sentences, and 6,427 are within 3 unknown content items under current practical knowledge. The blocker is that only about 100 are active; many verified/translated inactive rows need a safe reading-pack selector or reactivation quality gate.

2. **Teach/audit the high-leverage mapped gaps first.** Top 100 mapped missing lemmas cover 76.2% of missing-token occurrences in the imported sentence pool. Start with particles/prepositions and common verbs: `لَمْ`, `أَيّ`, `بَعْد`, `مَاذَا`, `رَدَّ`, `زَالَ`, `جَرَى`, `أَمام`, `بَيْن`, `كَيْفَ`, `لَنْ`, `إِذَا`, `بَعْض`, `أَوْ`, `قَبْل`.

3. **Do not blindly introduce the top list.** First audit homographs and suspended rows. Examples: `فِلْمٌ` likely captures `فلم`; `سَرَّ`, `مَارٌ`, and several suspended textbook-scan rows need state/data review before being used as curriculum.

4. **For whole books, fix/import unmapped surfaces before promising comfort.** Raw full-book coverage is capped by unmapped tokens. A targeted book-unlock pipeline should extract top unmapped surfaces for one selected book, classify them as morphology gap / function word / proper name / real lemma, then import or map them.

5. **Recommended first target:** `لَيْلَى وَالذِّئْبُ`. After learning its top mapped gaps, it reaches about 87% mapped coverage, and the remaining work is a finite unmapped-surface cleanup. That is close enough to make it a good pilot for a book-specific unlocker.

## Implementation Implication

The shortest product path is a `Hindawi Reading Pack` flow:

- Query verified corpus/book sentences, including inactive rows.
- Re-run the existing sentence quality gate before display/reactivation.
- Rank by current known coverage and by coverage gain from target missing lemmas.
- Produce a 20-50 sentence reading pack plus a small pre-study list.
- Separately, for a selected full book, generate a top-unmapped-surface audit and import/fix queue.
