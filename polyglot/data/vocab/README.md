# Latin seed vocabulary

Drop the source files here, then run `scripts/import_latin_vocab.py`. Each file
is a CSV (`.csv`) or TSV (`.tsv`/`.txt`) with a header row; the importer detects
columns by fuzzy name match, so most exports work as-is. Recognized headers:

| logical field | matched header contains | required |
|---|---|---|
| lemma | `lemma` / `headword` / `latin` / `word` | yes |
| gloss | `gloss` / `definition` / `meaning` / `english` / `translation` | no |
| pos | `part` / `pos` | no |
| rank | `rank` / `frequency` / `freq` / `order` | no (falls back to row order) |
| chapter | `chapter` / `cap` (LLPSI capitulum) | no |

Lemmas are normalized to a lookup key (macrons stripped, `j→i`, `v→u`); the
display form keeps the original spelling. Everything is scoped to
`language_code='la'` — a Latin import can never match a Greek row.

## Files

### `dcc_core.tsv` — DCC Latin Core Vocabulary (~1,000), the frequency backbone
- Source: https://dcc.dickinson.edu/vocab/core-vocabulary (CC BY-SA 3.0 — attribute Dickinson College Commentaries).
- Ready spreadsheet (export to TSV): https://github.com/bobtodd/nlp/blob/master/latin/data/dcc_core_latin_vocabulary_bridge20150730_all.xls
- Or export CSV from the Bridge customizable list: https://bridge.haverford.edu
- Columns there are `Headword | Definition | Part of Speech | Semantic Group | Frequency`.

### `llpsi_fr.tsv` — LLPSI Familia Romana (~1,800), the assumed-known seed
- Authoritative lemma+POS+gloss: the official Latin–English Vocabulary PDF
  (https://hackettpublishing.com/pdfs/Familia_Romana_Latin-English_Vocabulary.pdf,
  mirror https://www.thelatinlibrary.com/ll1/MasterVocab.pdf). Parse the 3-column
  layout into `lemma  gloss  pos`.
- For per-chapter order, export a chapter-tagged Anki deck to TSV (Anki desktop:
  File → Export → Notes in Plain Text), e.g. https://ankiweb.net/shared/info/1397480336,
  and add a `chapter` column.
- This is your textbook vocabulary — you own the book; keep the file local
  (don't commit copyrighted text).

### `roma_aeterna.tsv` — LLPSI 2 (Roma Aeterna), the learn-frontier (optional)
- Vocab via the combined Anki deck (https://ankiweb.net/shared/info/1763694683)
  or latin-is-simple (https://www.latin-is-simple.com/en/vocabulary/group/1224/).
- Copyrighted (Ørberg/Hackett); keep local.

## Run

```bash
cd polyglot
.venv/bin/python scripts/import_latin_vocab.py --phase all \
    --dcc-file data/vocab/dcc_core.tsv \
    --llpsi-file data/vocab/llpsi_fr.tsv \
    --ra-file data/vocab/roma_aeterna.tsv      # optional
```

Phases run independently and are idempotent:
- `dcc` / `roma_aeterna` → `FrequencyEntry` rows (rank backbone / learn-frontier).
- `promote` → `Lemma` rows for those entries (the "to learn" pool; no ULK).
- `llpsi` → `Lemma` rows + `UserLemmaKnowledge(state='known', source='llpsi_known')`
  with **no FSRS card** — assumed-known scaffold. Reading/review then confirms
  each by collateral exposure (`confirmed_at`) or a red miss lapses it into
  acquisition. That is the "which words do I already know?" mechanism.

Function words (per `lemma_quality.FUNCTION_WORD_SETS['la']`) are created as
mappable lemmas but never enrolled as scaffold targets. The importer
canonicalizes citation forms through LatinCy (infinitive `facere` → lemma
`facio`) so seeded lemmas match reading-time lemmatization — install the LatinCy
model first (`pip install -e ".[la]"`) or pass `--no-canonicalize`.

## Regenerate the TSVs from source

These were produced by committed parsers (run them if you re-download the source):

```bash
.venv/bin/python scripts/parse_llpsi_pdf.py \
    --pdf ~/Downloads/Lingua-Latina-Vocabulary.pdf --out data/vocab/llpsi_fr.tsv
.venv/bin/python scripts/parse_roma_aeterna_apkg.py \
    --apkg ~/Downloads/Lingua_Latina_II_-_Roma_Aeterna_Latin_to_English.apkg \
    --out data/vocab/roma_aeterna.tsv
```

Reading texts (e.g. `eutropius_book1.txt`) are imported via `POST /api/texts/paste`
(`language_code='la'`); split long texts into one page per section.

**Production was seeded from these on 2026-05-25** (1,585 LLPSI assumed-known,
2,518 Roma Aeterna learn-frontier, Eutropius Book I).

This directory's source files are gitignored; only this README is tracked.
