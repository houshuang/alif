# Vocabulary coverage against real books, 25 November 2026

Read-only analysis of a production snapshot taken on 25 November 2026 (79,647 reviews since
8 February). It led to `book_frequency_v1` (experiment log, same date).

## Question

After nearly ten months, how does the learner's vocabulary compare with courses and
frequency lists, and why do common words still seem to be missing?

## What does not work: matching list headwords

The first pass matched the headwords of SAMER (a hand-lemmatized list of 35,000 content
words from Hindawi books and news) against `lemmas.lemma_ar_bare`. It reported 71–74%
running-text coverage and listed سبب and جانب as absent. Both claims were wrong:

- بِسَبَبِ and بِجَانِبِ are their own lemmas, known, with 164 and 182 reviews;
- the bare nouns in sentences are mapped to the verbs سَبَّبَ and جانَبَ;
- participles and verbal nouns resolve to their verb through `forms_json`.

A headword match cannot see any of this. Coverage has to be measured on running text with
the production resolver (`lookup_lemma_id`, as `scripts/reading_readiness.py` does).

## Coverage of running text

Readable = function word, name, or a word in long-term review. Texts are the BAREC sources
in `backend/data/corpora/barec_sentences.json` (not in Git) and the learner's own 15
photographed pages.

| Text | Readable |
|---|---|
| Learner's own pages (Nov 2026) | 82% (88% with words being acquired) |
| Hindawi books | 81% |
| Kalima translated books | 80% |
| Wikipedia | 80% |
| Children's library (Green Library) | 86% |
| Arabian Nights | 86% |
| Quran sample | 86% |
| Subtitles (MSA, 2,500 tokens) | 89% |
| Pre-Islamic odes | 72% |

Error of a few points either way: the resolver sometimes picks a wrong look-alike in
unvocalized text.

## Does a book-derived list transfer?

Gap words were ranked on half of the Hindawi and Kalima sentences and the gain measured on
text the list never saw.

| Gap words learned | Held-out books | Own pages | Wikipedia |
|---|---|---|---|
| 0 | 80.7% | 82.2% | 80.4% |
| 300 | 86.8% | 88.1% | 86.7% |
| 500 | 88.4% | 90.1% | 88.4% |
| 1,000 | 90.9% | 92.5% | 90.9% |
| 2,000 | 93.5% | 94.1% | 92.9% |

Half of the top 1,000 gap words already exist in Alif: 110 suspended as leeches (رأى is the
largest single gap), 173 seen but never started, 167 with no learner row, 50 in acquisition.

## The due backlog and the firm base by book rank

Words ranked by count in 266,000 book tokens.

| Due words | Count | Share of book text | Median days overdue |
|---|---|---|---|
| Top 1,000 | 121 | 4.1% | 4 |
| 1,001–2,000 | 127 | 1.2% | 17 |
| Beyond 2,000 or absent | 331 | 0.6% | 43 |

Of 1,969 firm words (stability ≥ 30 days), 663 are in the top 1,000 and 494 in 1,001–2,000;
812 are beyond rank 2,000 or absent from the sample and carry about 3% of book text.

## Why Alif's own frequency core hid this

`frequency_core_entries` ranks every lemma already in the vocabulary above every missing
one: ranks 1–2,500 are 100% linked, and شركة (SAMER rank 133) sits at core rank 2,704,
linked to the verb شَرَكَ. Its top-2,000 coverage of about 95% is true by construction.

## Grouping of participles and verbal nouns

`docs/design-principles.md` says a participle or verbal noun is its own lemma. Of about
1,000 verbs with forms, a separate lemma exists for 151 active participles, 66 passive
participles and 179 verbal nouns; the rest resolve to the verb. مُناسِب is filed under
ناسَبَ and مُغلَق under أغلق, while مُسافِر has its own entry. Not addressed by
`book_frequency_v1`; listed in IDEAS.md.

## Textbook lists

Al-Kitaab Part 1: about 40 of 364 entries have a word not in long-term review, 27 of them
among the top 1,000 book gap words. Madinah Book 1: 18 of 242, 8 among the top 1,000.

## Also found

`select_next_words` returned at most one word per call since PR #285: a loop variable named
`count` replaced the caller's `count` with the token count of the last open reading target.
Fixed with `book_frequency_v1`.
