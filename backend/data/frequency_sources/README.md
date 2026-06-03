# Frequency Core Sources

`backend/scripts/build_frequency_core.py` builds `frequency_core_entries`, the
ranked list used for high-frequency vocabulary targeting and top-N coverage.

Default inputs:

- CAMeL MSA Frequency Lists, surface-form counts from a large modern MSA corpus.
- Kelly/Leeds Arabic frequency list, used as an additional learner-facing rank
  and approximate CEFR signal.

Current fusion weights favor reading practice over raw web frequency: Hindawi
book-prose evidence (850) and news/SAMER evidence (750) lead, while CAMeL (450)
acts as a broad support signal. Single-source outliers receive an agreement
penalty unless they are corroborated by another strong source or an explicit
curriculum DB source such as `avp_a1`, `duolingo`, or `textbook_scan`.
Confidence tiers use the same agreement model: `high` means at least two strong
frequency signals, so Hindawi + SAMER agreement can count as high confidence
even when Buckwalter/arTenTen/KELLY files are unavailable.

Optional inputs can be passed as TSV or CSV:

```bash
cd backend
PYTHONPATH=. python3 scripts/build_frequency_core.py \
  --hindawi-from-corpus \
  --buckwalter data/frequency_sources/buckwalter.tsv \
  --artenten data/frequency_sources/artenten.tsv \
  --news data/frequency_sources/news.tsv \
  --islamic data/frequency_sources/islamic.tsv
```

Accepted columns: `word`/`form`/`lemma`/`arabic`, optional `rank`,
`count`/`freq`, and optional `cefr`. The loader also accepts SAMER-style
`lemma#pos` and `Occurrences` columns.

The builder maps source forms to canonical Alif lemmas and aggregates each
source to its best lemma-level rank before fusion. Unmapped high-frequency items
remain in the ranked table with `lemma_id = NULL`, so top-N progress is honest
instead of silently compressing gaps away. By default the core excludes function
words, proper names, onomatopoeia, and Wiktionary reference/noise entries because
the scheduler cannot introduce those as normal vocabulary.

`--hindawi-from-corpus` derives a Hindawi/book-prose source from already
imported Hindawi sentences in the database (`Sentence.source = "corpus"`),
using mapped `SentenceWord.lemma_id` token counts and rolling variants up to
canonical lemmas. Use `--hindawi-corpus-sources corpus,passage` to include
additional sentence sources.

Production rebuild example when SAMER is available and Kelly is unavailable:

```bash
cd /opt/alif/backend
ALIF_SKIP_MIGRATIONS=1 PYTHONPATH=. .venv/bin/python3 scripts/build_frequency_core.py \
  --entries 5000 \
  --no-kelly \
  --hindawi-from-corpus \
  --news data/samer.tsv
```

## Quran (islamic) source — genuinely lemmatized (2026-06-03)

The `islamic` source is now populated from the **Quranic Arabic Corpus v0.4**
(`quranic-corpus-morphology-0.4.txt` in this dir; Kais Dukes, corpus.quran.com,
GPL, on Tanzil text — keep the attribution). Unlike the MSA surface-count
sources, the QAC carries a manually-verified dictionary lemma (`LEM`) per token,
so inflected forms are already grouped — and `app/services/quran_frequency.py`
maps those lemmas onto Alif rows with Quran-aware normalization (dagger-alef
U+0670, the QAC maddah caret U+005E, decomposed hamza+alef ءا) plus POS-aware
homograph disambiguation (أَمَرَ verb vs أَمْر noun → different Alif lemmas). It
is on by default in the rebuild above (weight raised 150 → 700, exempt from the
single-source agreement penalty — a high Quran frequency is its own
corroboration). Disable with `--no-quran`; override the file with
`--quran-morphology PATH`.

This drives a separate **"Quran Core"** progress track in the stats screen (rows
carrying `islamic_rank`, ordered by Quran frequency). ~58% of QAC content lemmas
map to existing Alif lemmas (84.7% token-weighted, ~1,290 distinct lemmas); the
unmapped residue (divine attributes like رحيم/غفور, prophet names, rare classical
roots) is reported as honest gaps — we never auto-create lemmas. See
`scripts/analyze_quran_freq_mapping.py` for mapping diagnostics.
