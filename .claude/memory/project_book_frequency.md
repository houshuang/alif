---
name: project_book_frequency
description: "book_frequency_v1 + frequent_word_intake_v1 (LIVE 2026-11-25) — book-text rank steers lanes and intake toward \"all high-frequency words firm\"; baseline after the word import, readout dates, open follow-ups"
metadata:
  node_type: memory
  type: project
  originSessionId: bece1fa3-bc6e-47bf-9bfb-0809a977e658
  modified: 2026-11-25T10:58:53.446Z
---

Stian's goal since 2026-11-25, in his words: "work aggressively at reaching 100% of high frequent words at different bands with speedy recognition." He wants something concrete for a one-year blog post (project started 2026-02-08) and said "accuracy of accounting, linking of words, etc is important".

**Live on prod (2026-11-25):**
- `book_frequency_v1` (PR #350): `book_frequency_entries` built by `scripts/build_book_frequency.py` from `backend/data/corpora/barec_sentences.json` (server-only file). Main lane = book rank ≤ 2,000. Progress: `GET /api/stats/book-frequency`; stats page shows the bands (PR #351).
- `frequent_word_intake_v1` (PR #352, `app/services/frequent_intake.py`): own allowance for book rank ≤ 2,000 and open Al-Kitaab/Madinah targets (12 new + 6 reintroduced leeches a day, closes at 40 frequent words in Box 1); acquiring reserve 40%. Rollback `ALIF_FREQUENT_WORD_INTAKE=0`. Reason: learner picks kept Box 1 loaded, so ordinary intake and leech reintroduction were at zero for ten days.
- 435 missing words created with `scripts/stage_word_list.py` (lists in `backend/data/word_lists/`), list rebuilt.
- Stian decided new participles/verbal nouns start **from their verb's review state** (`scripts/seed_derived_from_verb.py`; 105 seeded). Apply the same rule to later batches without asking again.

**Baseline after the import (use this, not the morning figures):** firm 366/500, 272/500, 415/1,000 (top 1,000 63.8%, top 2,000 52.7%); not started 31/93/315; suspended 5/21/51. Readouts 2026-12-09 and 2026-12-23 (experiment-log entries "2026-11-25 (5)", "(6)", "(7)"); on 12-09 also compare miss rates of seeded words with their verbs.

**Open (IDEAS.md "Word-credit follow-ups", `research/analysis-2026-11-25-word-credit.md`, `research/analysis-2026-11-25-reading-speed.md`):** second batch (~390 words at 12–16 book occurrences); verbal nouns spelled like their verb (عِلْم, نَظَر); remapping ~4,000 stored `sentence_words` rows that still credit the verb; resolver errors (عاد→وعد, بدأ→أبداً); duplicate entries (بلد/بِلاد); per-word speed cannot be read from sentence timings, a timed word check would be needed (not decided); ACTFL test for the anniversary not booked.

**Why:** the fused frequency core ranks in-vocabulary lemmas first, so it hid the gaps. **How to apply:** rank questions go to the book list; coverage claims go through running text ([[feedback_text_to_lemma_hardened_path]]); a "missing" or "folded" word usually means a missing lemma, and creating the lemma moves its tokens.
