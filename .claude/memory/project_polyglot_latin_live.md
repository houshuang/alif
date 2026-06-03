---
name: project_polyglot_latin_live
description: Latin is live in Polyglot production as of 2026-05-25 (PR
metadata: 
  node_type: memory
  type: project
  originSessionId: cad9c2d2-24e4-4a6f-85ce-d610dc476af9
---

Latin shipped to Polyglot production 2026-05-25 (PR #140, squashed onto main as
`152b473`). It is the second Polyglot language alongside Modern Greek; Arabic is
a separate backend and unaffected.

State on the Hetzner server:
- **Lemmatizer**: LatinCy `la_core_web_lg` (installed via the `la` extra) behind
  the Greek-style lemma-quality safety net. Display is unified macron-free /
  u-i / canonical (facio not facere) — see polyglot/CLAUDE.md "Latin" section.
- **Seed**: 1,585 LLPSI Familia Romana lemmas as assumed-known (no FSRS card,
  source `llpsi_known`); 2,518 Roma Aeterna lemmas as the learn-frontier
  (`frequency_core`). Importer: `scripts/import_latin_vocab.py`; parsers
  `scripts/parse_llpsi_pdf.py` + `scripts/parse_roma_aeterna_apkg.py`. Source
  data lives in `polyglot/data/vocab/` (gitignored — LLPSI/RA copyright;
  user's files in ~/Downloads).
- **Reading text**: Eutropius Breviarium Book I, 20 pages (one per section).
  Verified end-to-end (page 1 renders, known words recognized). Add more books
  by pasting via `/api/texts/paste` or splitting like `import_eutropius_pages.py`.
- **Cron**: crontab has `POLYGLOT_LANGUAGES=el la`; the wrapper loops languages
  sequentially (lock-safe — never run a parallel Latin cron).
- **Verification mechanism**: LLPSI words are assumed-known; reading/review
  confirms them by collateral exposure (Stats funnel: assumed → confirmed). DCC
  core was not needed (LLPSI chapter order is the frequency backbone).

Full design + audits: `research/polyglot-latin-design-2026-05-25.md`. Related:
[[feedback_expo_metro_cache_deploy]], [[feedback_polyglot_mirror_alif]].
