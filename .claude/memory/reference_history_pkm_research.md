---
name: History + PKM Research & Petrarca Integration Plan
description: Research on historians using PKM tools (Obsidian, Zettelkasten), and concrete integration plan for their repos/tools with Petrarca
type: reference
---

## Research Files
- `research/history-pkm-people.html` — Directory of 12 people at the intersection of history and PKM tools, with links and Petrarca-relevant repos section
- `research/petrarca-integration-plan.html` — Concrete integration plan with 5 prioritized items

## Key People (most relevant to Petrarca)
- **Shawn Graham** (Carleton University) — 6 relevant repos: steamroller (KG extraction), kg-hybrid (spaCy+LLM), discourser (novelty via embeddings), claude_antiquities_extractor_skill. GitHub: shawngraham
- **Sean Takats** (Univ. Luxembourg / Digital Scholar) — Co-CEO of Zotero/Tropy org. Zotero Translation Server is the biggest tool find.
- **Chris Aldrich** (boffosocko.com) — History of notetaking, starred Stian's hypothesis-to-bullet repo. Valuable for Hypothesis integration patterns.
- **Dan Allosso** (Saint Paul College) — Runs Obsidian Book Club, author of "How to Make Notes and Write"

## Petrarca Integration Priority Order
1. **Entity extraction prompt changes** (1 session) — constrained relationship predicates (~15 types), canonical IDs (`roger_ii_sicily`), mentions arrays, prescribed extraction workflow. Zero deps.
2. **Hypothesis integration** (1-2 sessions, ~370 lines) — highlighted passages as user-curated atomic claims, tag→entity linking, reading depth signal, auto-import annotated URLs. API: `GET /api/search?user=acct:stian@hypothes.is`, incremental via `search_after` cursor.
3. **Semantic vectors spike** (2 hours) — discourser-style custom dimensions (primary_vs_secondary, military_vs_cultural). Already have MiniLM via limbic.
4. **Zotero Translation Server trial** (2 hours) — `docker pull zotero/translation-server`, port 1969, `POST /web` with URL. Complements trafilatura (metadata only, no body text). 600+ site-specific extractors.
5. **Dual-model verification** — only if entity quality still poor after #1

## Dropped
- PressForward: trafilatura already covers extraction
- spaCy pre-pass: LLM extraction already good for general articles
- Gooseberry: sync pattern is 20 lines of Python
