---
name: project_book_frequency
description: "book_frequency_v1 (LIVE 2026-11-25) — book-text rank steers lanes, relief and intake toward \"all high-frequency words firm\"; readout dates and open follow-ups"
metadata:
  node_type: memory
  type: project
  originSessionId: bece1fa3-bc6e-47bf-9bfb-0809a977e658
  modified: 2026-11-25T07:47:10.637Z
---

Stian's goal since 2026-11-25, in his words: "work aggressively at reaching 100% of high frequent words at different bands with speedy recognition." He wants the firm base to hold as many high-frequency words as possible, a fast-recognition measure later, and something concrete for a one-year blog post (project started 2026-02-08).

**What is live (PR #350, deployed 2026-11-25):** `book_frequency_entries`, built on the server by `scripts/build_book_frequency.py` from `backend/data/corpora/barec_sentences.json` (server-only file, also on the Mac). While it has rows and `ALIF_BOOK_FREQUENCY_LANES` is not `0`: main lane = book rank ≤ 2,000 / acquiring / own reading; `automatic_attention_v2` broad frequency = book rank ≤ 2,000; intake tiers 340/335/330 for the top bands; leech reintro order by book rank. Progress: `GET /api/stats/book-frequency`. Experiment log entry "2026-11-25 (5)"; analysis `research/analysis-2026-11-25-vocabulary-coverage.md`.

**Baseline on prod, 2026-11-25 (`/api/stats/book-frequency`, words Alif lacks counted as not firm):** firm 368/500 in ranks 1–500, 279/500 in 501–1,000, 422/1,000 in 1,001–2,000 (top 1,000: 64.7%, top 2,000: 53.5%). Real-book coverage about 81%, own pages 82%. Readouts due 2026-12-09 and 2026-12-23.

**Open, agreed as next steps (in IDEAS.md "Book-frequency follow-ups"):** create lemmas for book words Alif lacks (33 in top 1,000, 200 in top 2,000) then re-run the builder; separate lemmas for participles/verbal nouns (Stian finds the grouping confusing: mu- forms folded into the verb while مسافر is separate); per-word speed measure from `reading_timing`; show bands in the app; finish Al-Kitaab Part 1 / Madinah Book 1 and find Parts 2–3 lists; ACTFL reading/listening tests for the anniversary (Norway availability unconfirmed; CIMA needs writing and speaking).

**Why:** the fused frequency core ranks in-vocabulary lemmas first, so it hid the gaps. **How to apply:** rank questions go to the book list; coverage claims go through running text ([[feedback_text_to_lemma_hardened_path]]). Intake is still earned through the recovery ladder; Stian has not asked to raise the daily cap.
