# Frequency Core Sources

`backend/scripts/build_frequency_core.py` builds `frequency_core_entries`, the
ranked list used for high-frequency vocabulary targeting and top-N coverage.

Default inputs:

- CAMeL MSA Frequency Lists, surface-form counts from a large modern MSA corpus.
- Kelly/Leeds Arabic frequency list, used as an additional learner-facing rank
  and approximate CEFR signal.

Optional inputs can be passed as TSV or CSV:

```bash
cd backend
PYTHONPATH=. python3 scripts/build_frequency_core.py \
  --buckwalter data/frequency_sources/buckwalter.tsv \
  --artenten data/frequency_sources/artenten.tsv \
  --hindawi data/frequency_sources/hindawi_children.tsv \
  --news data/frequency_sources/news.tsv \
  --islamic data/frequency_sources/islamic.tsv
```

Accepted columns: `word`/`form`/`lemma`/`arabic`, optional `rank`,
`count`/`freq`, and optional `cefr`.

The builder maps source forms to canonical Alif lemmas and aggregates each
source to its best lemma-level rank before fusion. Unmapped high-frequency items
remain in the ranked table with `lemma_id = NULL`, so top-N progress is honest
instead of silently compressing gaps away. By default the core excludes function
words, proper names, onomatopoeia, and Wiktionary reference/noise entries because
the scheduler cannot introduce those as normal vocabulary.
