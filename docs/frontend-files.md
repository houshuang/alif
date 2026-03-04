# Frontend Files

All frontend in `frontend/`.

## Screens (app/)
- `app/index.tsx` — Review screen: sentence-only (no word-only fallback), reading + listening, word lookup, word marking, back/undo, wrap-up mini-quiz (acquiring + missed words), session word tracking, story source badges on intro cards, reintro cards (retention-optimized layout with pattern/root/etymology/mnemonic + Continue button), experiment intro cards (A/B test for new words), session end card (journey timeline with graduated/advanced/slipped words, pipeline bar with deltas, response speed comparison, today stats, root coverage — fetches session-summary endpoint after sync flush)
- `app/learn.tsx` — Learn mode: 5-candidate pick → done (no quiz). Shows pattern decomposition (wazn + root), etymology, and mnemonic in highlighted info boxes. Story source badge for story words.
- `app/explore.tsx` — Explore tab with three sub-tabs (Words/Roots/Patterns). Words sub-tab: grid, category tabs (Vocab/Function/Names), smart filters (Leeches/Struggling/Recent/Solid/Next Up/Acquiring/Encountered), sparklines, search. Roots sub-tab: FlatList of root cards (Arabic letters, core meaning, coverage bar), search, tap → `/root/{id}`. Patterns sub-tab: FlatList of pattern cards (name, meaning, coverage bar), search, tap → `/pattern/{wazn}`.
- `app/words.tsx` — Redirect to /explore (legacy URL support)
- `app/stats.tsx` — Analytics dashboard organized into 5 sections: **Today** (hero card with comprehension bar, graduated pills, new words by source, calibration, today's transitions), **Vocabulary** (word lifecycle funnel: encountered→acquiring→learning→known with counts, reading coverage %, CEFR level + arrival predictions, acquisition pipeline with Leitner boxes), **Progress** (known words growth with week/month deltas, 14-day activity chart with words_learned overlay, learning pace 7d+30d, transitions 7d+30d, retention 7d+30d, comprehension 7d+30d side-by-side), **Sessions** (recent sessions with mini comprehension bars), **Deep Dive** (vocabulary health/stability distribution, struggling words, root progress, insights card with 10 derived stats)
- `app/story/[id].tsx` — Story reader with tap-to-lookup, WordInfoCard for lookups, ActionMenu in header bar (Ask AI, suspend story). All navigation (back, complete, suspend) goes to `/stories` via `router.replace`.
- `app/stories.tsx` — Story list with generate + import + book import, grouped sections (Active/Suspended/Completed collapsed), suspend all, suspend/reactivate toggle per story, book source badges, completion prediction (~Xd until ready), clickable per-page readiness pills for book imports (learned/new format, green when all page words acquiring; navigate to book-page detail), book footer shows "X/Y new words learning" (deduplicated, only words unknown at import)
- `app/book-page.tsx` — Book page detail: words (new vs already known) with status pills, sentences with seen/unseen indicators. Navigable from page pills on story list.
- `app/scanner.tsx` — Textbook page OCR scanner
- `app/book-import.tsx` — Book import: photograph cover + content pages → reading goal with sentence extraction
- `app/more.tsx` — More tab: Scanner, Chats, New Words, Activity Log
- `app/word/[id].tsx` — Word detail: forms, root family, pattern, etymology, memory hooks, review history, sentence stats. Root Family title links to `/root/{root_id}`, Pattern title links to `/pattern/{wazn}`. Actions (postpone/suspend/flag) in "..." overflow menu in header bar.
- `app/root/[id].tsx` — Root detail: 52pt Arabic root letters, core meaning, enrichment section (etymology story, cultural significance, literary examples, fun facts, related roots as pills), derivation tree grouped by pattern. Words tappable → `/word/{id}`, pattern headers tappable → `/pattern/{wazn}`.
- `app/pattern/[id].tsx` — Pattern detail: pattern name + meaning, enrichment section (explanation, how to recognize, semantic fields, example derivations, register notes, fun facts, related patterns as pills), word list sorted by knowledge state. Words tappable → `/word/{id}`.
- `app/chats.tsx` — AI chat conversations
- `app/listening.tsx` — Dedicated listening mode
- `app/review-lab.tsx` — Hidden route for testing review UI variants

## Components (lib/)
- `lib/review/ActionMenu.tsx` — "⋯" menu: Ask AI (auto-explain on review), Suspend, Flag. Supports `extraActions` prop for screen-specific actions (e.g., story suspend). Computes auto-explain prompt eagerly at open time.
- `lib/review/WordInfoCard.tsx` — Word info panel for review. Always shows full info (no root gate). Pattern decomposition line (wazn + root). Only known/learning root siblings shown. Prev/next arrows navigate tapped word history. Shows surface form transliteration for conjugated forms, full etymology (derivation, loanwords, cultural note), and full memory hooks (mnemonic, cognates, collocations, usage context, fun fact). **Confusion analysis**: when a word is marked "did not recognize" (yellow), shows: (1) inline-highlighted confusable words — matching letters dimmed (25% opacity), differing letters in orange, with `splitArabicGraphemes()` for proper diacritic grouping; (2) teaching hints ("has X not Y") + "dots only" pill for rasm_distance=0; (3) color band morphological decomposition — surface form with each morpheme colored (purple/orange/blue/green) + legend. `HighlightedArabic` component handles the inline diff rendering. Scrollable when content overflows (maxHeight 200).
- `lib/review/SentenceInfoModal.tsx` — Debug modal: sentence ID, source, review history, per-word FSRS difficulty/stability. **Selection reasoning**: when opened from review, shows why the sentence was chosen (scheduled review / acquisition repeat / on-demand / auto-intro fill), primary word state, selection score + pick order, and per-factor score breakdown
- `lib/AskAI.tsx` — AI chat modal (used in ActionMenu). Auto-sends combined explain prompt on open from review screen (sentence understanding + marked word breakdowns + lemma accuracy check + translation accuracy check). Shows auto-sent message as "Explaining this sentence..." label. Follow-up questions supported. Flag button after response.
- `lib/MarkdownMessage.tsx` — Markdown renderer for chat/AI responses
- `lib/WordCardComponents.tsx` — Reusable word display (posLabel, FormsRow, FormsStrip with per-form transliteration, PatternExamples, PlayButton)

## Infrastructure (lib/)
- `lib/api.ts` — API client with typed interfaces for all endpoints
- `lib/types.ts` — TypeScript interfaces
- `lib/offline-store.ts` — AsyncStorage session cache (30-min staleness TTL) + reviewed tracking. Background refresh via `fetchFreshSession()` for in-session staleness (15-min gap detection via AppState). Word lookup cache: versioned key (`v2`), 24h TTL per entry, `allowStale` fallback for offline use.
- `lib/sync-queue.ts` — Offline review queue, bulk sync
- `lib/theme.ts` — Dark theme, semantic colors
- `lib/net-status.ts` — Network status singleton + useNetStatus hook
- `lib/sync-events.ts` — Event emitter for sync notifications
- `lib/frequency.ts` — Frequency band + CEFR color utilities
- `lib/grammar-particles.ts` — Rich grammar info for 12 core Arabic particles (في، من، على, etc.), displayed via GrammarParticleView in WordInfoCard
- `lib/topic-labels.ts` — Human-readable labels + icons for 20 thematic domains
- `lib/mock-data.ts` — Mock words, stats, learn candidates for testing
- `lib/__tests__/` — Jest tests for sync, store, smart-filters, API, typechecks
