# Frequency-Core Curriculum

The frequency core is Alif's general-reading vocabulary curriculum. It answers
"how many of the top N Arabic words do I know?" without hiding missing mappings.

## Top-N Semantics

`top 500` means the first 500 rows in `frequency_core_entries`, not the first
500 rows that happen to map to existing lemmas. Rows with `lemma_id = NULL`
remain in the table and count against the denominator. This is intentional:
unmapped high-frequency words are curriculum gaps, not invisible rows.

Learned coverage counts `known`, `learning`, and `acquiring`: once a word has
entered acquisition, it is already being learned for top-N progress purposes.
Pipeline coverage counts those states plus `lapsed` and `encountered`.

`next_gaps` is the earliest not-yet-introduced curriculum list, not a "not
graduated yet" list. It excludes rows with `introduced_at` set and rows in an
introduced state (`known`, `learning`, `acquiring`, `lapsed`, `suspended`).
Rows marked `needs_manual_review` are unresolved mapping/curation work; rows
marked `unmapped` are still missing from the DB.

## Sources And Weights

| Source | Weight | Intended signal |
|---|---:|---|
| CAMeL MSA | 1000 | Broad modern MSA frequency |
| Buckwalter/Parkinson | 900 | Learner-oriented 5,000-entry reference |
| arTenTen export | 800 | Large modern web usage |
| KELLY | 600 | CEFR/learner frequency signal |
| Hindawi/books | 400 | Book prose and children's literature |
| News | 300 | Current public prose |
| Islamic/classical | 150 | Religious/classical target-domain relevance |

Each source is aggregated to lemma-level rank before fusion. If several source
forms map to the same Alif lemma, that source contributes once using the best
rank; counts may still be summed for audit metadata.

## Confidence

`confidence_tier` is a source-confidence label, not a learning label:

| Tier | Meaning |
|---|---|
| high | Mapped lemma appears in at least two broad sources among CAMeL, Buckwalter/Parkinson, arTenTen, KELLY |
| medium | Mapped lemma has one broad source plus a domain source, or strong beginner KELLY signal |
| low | Weak evidence, source skew, or unmapped row |

`gap_status = "unmapped"` means the source item was high enough to rank but did
not map to an existing Alif lemma. These rows should drive future vocabulary
curation instead of being filtered out.

## Builder

The builder is `backend/scripts/build_frequency_core.py`.

```bash
cd backend
PYTHONPATH=. python3 scripts/build_frequency_core.py --dry-run

PYTHONPATH=. python3 scripts/build_frequency_core.py \
  --entries 5000 \
  --buckwalter data/frequency_sources/buckwalter.tsv \
  --artenten data/frequency_sources/artenten.tsv \
  --hindawi data/frequency_sources/hindawi_books.tsv \
  --news data/frequency_sources/news.tsv \
  --islamic data/frequency_sources/islamic.tsv
```

Default downloadable inputs are CAMeL MSA and KELLY/Leeds when available.
Optional files accept `word`/`form`/`lemma`/`arabic`, optional `rank`, optional
`count`/`freq`, and optional `cefr`.

## How Scheduling Uses It

New-word selection gives strict priority to frequency-core ranks:

- top 500 outranks active book words,
- top 1,000 also outranks active book words,
- top 2,000 and top 5,000 remain above ordinary source tiers,
- book/page priority still matters inside the book path and below the highest
  frequency-core bands.

Review scheduling uses the same core for lanes:

- main lane: all acquiring words, all core/proxy rank <=5,000 due words, and
  non-artifact due words,
- slow lane: low/null-rank artifact FSRS debt from book/OCR/story/scaffold
  paths, sampled at 10% of the session budget.

## API And UI

`GET /api/stats/analytics` returns `frequency_core` with:

- `total_entries`,
- `learned_prefix_count`,
- bands for 100, 250, 500, 1,000, 2,000, and 5,000,
- `low_confidence_count`,
- `unmapped_count`,
- `next_gaps`.

The stats card must label low-confidence/unmapped rows as gaps and keep
introduced words out of the gap list. A top-N number is only motivational if it
is honest.
