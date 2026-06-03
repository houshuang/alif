---
name: project_quran_frequency_track
description: "How Alif's Quran-frequency track works (the islamic source) + the QAC mapping gotchas"
metadata: 
  node_type: memory
  type: project
  originSessionId: c5f68377-bb2e-4066-a646-37e55d06c206
---

The `islamic` source of `frequency_core_entries` is the **Quran track**, shipped 2026-06-03 (PR #195). It populates `islamic_rank` and drives a separate "Quran Core" card on the stats screen.

**Source:** Quranic Arabic Corpus v0.4 morphology file, committed at `backend/data/frequency_sources/quranic-corpus-morphology-0.4.txt` (Kais Dukes, corpus.quran.com, GPL — keep attribution). Genuinely lemmatized (per-token `LEM`). **Do NOT compute from our own `QuranicVerseWord` table — dead end:** only 40 of 6,236 verses were ever lemmatized (Quran Reading Mode suspended 2026-04-07), and that sample carried mapping noise.

**Mapping lives in `app/services/quran_frequency.py`** (`map_quran_frequencies`): bw2ar (CAMeL) → `normalize_qac_lemma` → `lookup_lemma` + POS-aware homograph disambiguation. The two normalization gotchas that inflated unmapped residue (78.5%→84.7% token coverage when fixed): the QAC **maddah caret U+005E** (bw2ar leaves it unmapped, e.g. سَمَا^ء) must be stripped, and **decomposed hamza+alef ءا** (QAC writes آ as `'aA`, e.g. آمن/آية) must fold to ا — on top of the existing dagger-alef U+0670 handling in `normalize_arabic`. POS disambiguation routes أَمَرَ(V) vs أَمْر(N) to different lemmas; see [[feedback_quran_dagger_alef_normalization]].

**Result:** ~58% of QAC content lemmas map → ~1,290 distinct Alif lemmas. Unmapped 42% (divine attributes رحيم/غفور, prophet names, rare roots) are honest gaps — never auto-created.

**OPERATIONAL:** deploying the code does NOT populate `islamic_rank` — you must **rebuild the core** afterward: `cd /opt/alif/backend && ALIF_SKIP_MIGRATIONS=1 PYTHONPATH=.:/opt/limbic .venv/bin/python3 scripts/build_frequency_core.py --entries 5000 --no-kelly --hindawi-from-corpus --news data/samer.tsv` (Quran on by default; takes a few min via CAMeL → use nohup). Verify: `select count(*) from frequency_core_entries where islamic_rank is not null` ≈ 1,290.

**Classical track (beyond Quran) = NO-GO for now** (Task B, 2026-06-03): no off-the-shelf lemmatized classical Arabic frequency data; no validated classical lemmatizer (CAMeL is MSA-only). See `research/analysis-2026-06-03-classical-literary-frequency-track.md`. Viable build path = LLM-in-context lemmatization over OpenITI (like polyglot's Greek/Latin).
