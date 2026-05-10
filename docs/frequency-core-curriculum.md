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
It is deduplicated by lemma ID so definite/plural frequency rows do not repeat
the same unintroduced lemma; unmapped rows are deduplicated by display form.
Rows marked `needs_manual_review` are unresolved mapping/curation work; rows
marked `unmapped` are still missing from the DB.

## Sources And Weights

| Source | Weight | Intended signal |
|---|---:|---|
| Hindawi/books | 850 | Book prose and children's literature |
| Buckwalter/Parkinson | 800 | Learner-oriented 5,000-entry reference |
| arTenTen export | 800 | Large modern web usage |
| News/SAMER | 750 | Current public prose and readability-aligned frequency |
| KELLY | 650 | CEFR/learner frequency signal |
| CAMeL MSA | 450 | Broad modern MSA support signal |
| Islamic/classical | 150 | Religious/classical target-domain relevance |

Each source is aggregated to lemma-level rank before fusion. If several source
forms map to the same Alif lemma, that source contributes once using the best
rank; counts may still be summed for audit metadata.

Single-source outliers are downranked by an agreement penalty unless they have
at least two strong frequency signals or an explicit curriculum DB boost
(`avp_a1`, `duolingo`, `textbook_scan`). This keeps the top bands conservative:
Hindawi/SAMER can lead the reading curriculum, while CAMeL-only web artifacts or
Hindawi-domain proper-noun-like outliers do not jump into the first 500 rows on
one corpus alone.

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
  --hindawi-from-corpus \
  --buckwalter data/frequency_sources/buckwalter.tsv \
  --artenten data/frequency_sources/artenten.tsv \
  --news data/frequency_sources/news.tsv \
  --islamic data/frequency_sources/islamic.tsv
```

Default downloadable inputs are CAMeL MSA and KELLY/Leeds when available.
Optional files accept `word`/`form`/`lemma`/`arabic`, optional `rank`, optional
`count`/`freq`, and optional `cefr`. SAMER-style `lemma#pos` and `Occurrences`
columns are also accepted.

`--hindawi-from-corpus` uses the imported Hindawi sentence corpus already in the
database (`Sentence.source = "corpus"`) as the Hindawi/books source. It counts
mapped `SentenceWord.lemma_id` tokens and rolls variants up to canonical lemmas.

The 2026-05-10 production rebuild used the sources currently available on the
server:

```bash
cd /opt/alif/backend
ALIF_SKIP_MIGRATIONS=1 PYTHONPATH=. .venv/bin/python3 scripts/build_frequency_core.py \
  --entries 5000 \
  --no-kelly \
  --hindawi-from-corpus \
  --news data/samer.tsv
```

Before replacing production rows, take a SQLite backup:

```bash
sqlite3 data/alif.db ".backup data/alif.db.backup_freq_$(date +%Y%m%d_%H%M%S)"
```

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
- when the due queue is larger than the session, effective frequency rank is
  used as a priority signal before raw age of debt. Older debt still breaks
  ties inside the same frequency neighborhood, but obscure overdue words do not
  crowd out high-frequency overdue words just because they are older.
- low-priority leeches are not permanently excluded; their automatic
  reintroduction cooldown is longer, so rare words that keep failing consume
  less near-term review bandwidth.

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
