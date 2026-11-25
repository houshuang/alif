---
name: project-word-cards
description: "Stack word panel + lazy word cards (derivation chain, form glosses, set phrases) LIVE 2026-11-25; backlog run, feedback pending"
metadata:
  node_type: memory
  type: project
  originSessionId: e8be027d-4090-426c-a622-ab676a41d61d
  modified: 2026-11-25T10:23:29.398Z
---

Reading-dock word panel redesigned to the "Stack" layout (PR #357, deployed + OTA 2026-11-25) after a
design-explorer review with Stian. He wanted: no row labels, no clitic split (he knows ـها/ـهم/لِ/ال),
derivation chains ("really cool, very useful"), set phrases ("extremely useful" — genuinely frequent
collocations, never example sentences; every phrase must contain the word), scannable root list,
mu-/‑ing forms with glosses; taller is fine if it scrolls and never covers the sentence.

**Why:** the panel is seen on every missed word, so it should carry the most learning value.

**How to apply:** data lives in `Lemma.word_card_json`, filled lazily by `scripts/enrich_word_cards.py`
(cron 120/run; a one-off backlog run of ~2,870 words started 2026-11-25 10:20 UTC, log
`/tmp/word_cards_backlog.log` on alif). Phrase candidates: `data/corpora/lemma_phrase_candidates.json`
on the server (rebuild with `build_phrase_candidates.py` after vocabulary/corpus changes). Open:
Stian's first impressions on the phone; layouts "Columns" and "Tree" were the alternatives
("we can adjust later"). Related: [[project-book-frequency]].
