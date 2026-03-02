# Changelog

All notable changes to Alif — Arabic Reading & Listening Trainer.

This project has been developed entirely with [Claude Code](https://claude.com/claude-code) since its initial commit.

---

## 2026-03-02

- Revert to Expo SDK 54 for App Store Expo Go compatibility
- Add VPS deployment guide to README

## 2026-03-01

- Add offline reading support for ~1 hour sessions (AsyncStorage sync queue, 30-min session staleness TTL, background session refresh)

## 2026-02-27

- Fix AskAI auto-explain: eager state init instead of useEffect
- Fix AskAI messages area: replace flex:1 with explicit min/maxHeight
- Fix stale word lookup cache causing sparse WordInfoCards
- Fix common phrases alignment in Word Detail

## 2026-02-26

- Auto-explain chat: auto-send explain prompt on open, remove manual buttons
- Word info card: reduce height, add pattern link, function word transliteration
- Fix flag handling: retire bad Arabic sentences, dedup flags, recover stuck flags

## 2026-02-25

### Explore Tab (#6)
- New Explore tab with three sub-tabs: Words, Roots, Patterns
- Browse/search all roots with coverage stats, enrichment, derivation trees
- Browse/search all patterns with enrichment and word lists
- Cross-linked from word detail screens

### Word Card Redesign (#5)
- FormsStrip component showing conjugation forms with transliteration
- Pattern examples, ALA-LC transliteration standardization
- Surface etymology, overflow menu, scrollable content
- Compute forms transliteration on-the-fly

### Fixes
- Fix bare medial alif transliteration
- Fix word detail back button, add transliteration to root family
- Explore tab polish: back buttons, sorting, CEFR dots

## 2026-02-24

- Raise pipeline caps to fix sentence drought on heavy-usage days (#3)
- Make story generation async with polling to prevent iPhone timeouts (#4)

## 2026-02-23

- Enable fill phase for user-facing sessions + warm cache recency detection (#2)

## 2026-02-22

### Memory Hooks Overhaul
- Redesign memory hook prompt based on cognitive science research
- Add premium overgenerate-and-rank mnemonics for hard/lapsed words
- Generate mnemonics on first failure, not on word introduction

### Pipeline Tuning
- Suppress auto-intro when acquiring pipeline exceeds 40 words
- Exclude function words from scheduling and due counts
- Reduce sentence recency window from 4 days to 1 day
- Add word mapping flag type with LLM evaluation
- Add flag button to AI chat for quick sentence flagging

### Performance
- Fix 18s session load → 1.2s: skip material gen during fast session build
- Skip CAMeL disambiguation during session build lemma backfill

### Other
- Clean up audit findings: total_reviews, grammar_confused, dead code (#1)
- Extract textbook page numbers during OCR scanning
- Fix misleading "caught up" and show due card breakdown

## 2026-02-21

### Morphological Patterns (Wazn)
- Add `Lemma.wazn` and `wazn_meaning` fields
- Pattern display in learn cards, word info cards, word detail
- Pattern family (other words with same wazn) in word detail endpoint
- API: `/api/patterns`, `/api/patterns/{wazn}`, `/api/patterns/roots/{root_id}/tree`
- `PatternInfo` table with per-pattern enrichment

### Comprehensibility Gate Tightening
- Exclude encountered words from scaffold count
- Count unmapped words as unknown
- Add regression tests for scaffold exclusion rules

### Bug Recovery
- Restore 10+ features silently reverted by bundled commit 7ee81cf
- Add regression tests and git diff discipline rules to prevent future silent reverts
- Show form label when tapped word differs from lemma (plural, comparative, etc.)

## 2026-02-20

### Tashkeel Fading & Root-Aware Boost
- Backend-driven tashkeel fading: three modes (always/fade/never) based on word stability
- Root-aware stability boost: words with 2+ known root siblings get Rating.Easy on graduation
- Learning analysis report

### Sentence Selection
- Show sentence selection reasoning in review sentence info modal
- Highlight due words in sentence info modal
- Relax recency filter for failed sentences + rescue pass for blocked words
- Filter function words from word introduction candidates
- Fix Claude CLI JSON parsing for extra data after JSON object

## 2026-02-19

### Claude CLI Migration (Free LLM)
- Switch background LLM tasks to Claude CLI (free via Max plan)
- Mount Claude CLI into Docker container for cron jobs
- Two-tier architecture: background → Claude CLI (free, ~15-30s), on-demand → Gemini Flash (paid, ~1-2s)
- Sentence gen cron → Claude Sonnet, quality gate/enrichment → Claude Haiku

### Story Reader Improvements
- Use WordInfoCard in story reader, dismissable without losing highlight
- Re-tap unselects, all words clickable including function words
- Zero unknown words + self-correction loop for story generation
- Preserve punctuation in story words, split English translation
- Soft-delete stories instead of hard-delete to prevent ID reuse

### Session & Pipeline
- Session diversity: reserved intro slots + scaffold decay + tighter generation
- Raise sentence pipeline cap from 300 to 600
- Fix DB locking: generate-then-write pattern for sentence generation
- Exclude encountered words from comprehensibility gate
- Add LLM mapping verification to book imports and multi-target sentences

### Other
- Add LLM task_type logging and usage audit script
- Restore function word detection for stories and book progress
- Add book ULK consistency check to cron
- Dedup story word counts by lemma_id

## 2026-02-18

### Stats Screen Redesign
- 5 sections: hero card, acquisition pipeline, insights, session history, charts
- Record day insights: most intros and graduations in a day
- Accurate graduation stats, fix chart colors and funnel labels

### Stability & Performance
- Fast session loads: skip on-demand gen, rotate stale in background
- Fix shrinking session sizes: cooldown + on-demand gate
- Fix cascading DB lock crash in session building
- Reduce prefetch storm: single prefetch at session-complete
- Fix fill-phase crash and grammar card duplication
- Fix refresh session: bypass cache to fetch fresh from server
- Increase SQLite busy_timeout to 15s

### NLP Pipeline Fixes
- Fix forms_json entries shadowing direct lemma bare forms (two-pass lookup)
- Improve LLM mapping verification prompt to reduce false positives
- Discard sentences with bad lemma mappings instead of nulling
- Fix word source display: show learning source (book/story) over lexical origin
- Fix source attribution: book/story sources override OCR/collateral on acquisition

### Other
- Leitner system review enhancements
- Add leech reintroduction to 6h cron, fix CEFR to include learning
- Fix Most seen filter not showing

## 2026-02-17

- Fix lemma mapping pipeline: CAMeL disambiguation, al-prefix guard, extended forms
- Add lookup collision tracking and resolution (B5)
- Add resolve_existing_lemma() fallback in story import dedup
- Add VERIFY_MAPPINGS_LLM environment variable

## 2026-02-16

### Book Import Enhancements
- Page-level tracking with detail screen + OCR enhancement for dark images
- Word category classification for proper names and onomatopoeia
- Track book word progress: new-at-import words vs started learning
- Show learning progress in story list with page pills and footer
- Strict source-based priority tiers for word introduction
- Fix book page step: 2.0 gap ensures strict page ordering

### Word Detail Improvements
- Add Postpone and Suspend buttons to word detail page
- Fix wrong word shown on tap: decouple display from variant resolution
- Fix word detail source_info badge and frequency rank formatting
- New words started today by source in stats TodayHeroCard

### Infrastructure
- Preserve word origin source through acquisition
- Book import creates encountered ULK records
- Set ULK source from priority tier + link to book/story on auto-intro
- Enforce 300-sentence pipeline cap + rotation in cron
- Add automatic lemma enrichment after book/story import

## 2026-02-15

### Book Import
- OCR children's books into reading goals with sentence extraction
- Sentence-level segmentation with lemma mapping
- Book sentence creation resolves conjugated forms via CAMeL morphology
- Save book upload images to disk for retry on failure
- Ensure every sentence_word has a lemma_id

### Analytics
- Enriched analytics: CEFR predictions, book pages, story ETAs
- Clarify grammar section with legend and descriptive labels

### Sentence Generation
- Larger batches, vocabulary diversity, stale rotation
- Fix hamza normalization in target word matching
- Fix variant form lookup + sentence generation query ordering

### Other
- Improve WordInfoCard nav: bigger buttons + swipe gesture
- Hide book-import from tab bar, archive completed stories

## 2026-02-14

### Story Generation
- Story generation benchmark: Opus wins, GPT-5.2 confirmed worst
- Switch story generation to Opus with retry loop + claude-p wrapper
- Add story generation benchmark script

### Scheduling
- Acquisition due-date gating + graduated leech cooldowns
- Demand-driven auto-introduction: remove acquiring pipeline caps
- Remove recap mechanism (redundant with within-session repetition)

## 2026-02-13

### Sentence Pipeline Overhaul
- Gemini Flash for generation, fail-closed quality gate, POS vocabulary expansion
- Cross-model quality review: Claude Haiku reviews Gemini-generated sentences
- Sentence info debug modal with FSRS difficulty percentage

### Stats & UI
- Stats screen overhaul: hero card, acquisition pipeline, session history
- AI chat: split explain into "Explain marked" and "Explain full"
- CEFR progress bar shows acquiring words with recognition in second color

### Session Management
- Session fill phase + sentence pre-warming for continuous learning
- Make all words tappable in sentence review

### Other
- Remove function word exclusions: all words now learnable with FSRS tracking
- Add memory hooks service and migrations
- Improve etymology backfill: generate loanword origins instead of null

## 2026-02-12

### Algorithm Redesign
- Three-phase word lifecycle: Encountered → Acquiring (Leitner 3-box) → FSRS-6
- Redirect variant review credit to canonical lemma
- Learning phase redesign: auto-intro, aggressive repetition, smaller cohort
- Comprehensibility gate: ≥60% known scaffold words required
- Uncap learning pipeline: raise caps, dynamic difficulty, fix graduation
- Add almost-due fallback: never return empty sessions
- Shorter retry intervals for words never answered correctly

### Sentence Generation
- Quality gate: Gemini Flash review for sentence generation pipeline
- Multi-target sentence validation
- Improve prompts to fix 57% failure rate
- Parallelize on-demand sentence generation with ThreadPoolExecutor
- Accept encountered words in sentence validation (expand vocab from 77 to 547)
- Raise sentence word count floor: 5-7/6-9/8-11/11-14

### Frontend
- Add back button to sentence review with undo support
- Smart filter tabs: Leeches, Struggling, Recent, Solid, Next Up
- Category tabs: Vocabulary/Function/Names
- Review sparkline on word cards
- Redesign word list as compact two-column grid
- Story suspend/reactivate, sparkline inter-review gaps
- Fix wrap-up quiz and add story context to Learn cards

### Infrastructure
- Pin py-fsrs>=6.0.0, clean up v4 references
- Add deterministic Arabic→ALA-LC transliteration service and backfill
- Add diacritics backfill script for undiacritized lemmas
- Add SAMER readability lexicon backfill
- Box 1 capacity cap for auto-introduction (MAX_BOX1_WORDS=8)
- Topical learning cycles: focus word introduction by domain
- Remove legacy word-only review fallback, /submit endpoint, get_due_cards
- Add frontend test suite (74 tests)
- Add 'Refresh session' to sentence review action menu

## 2026-02-11

### Word & Sentence Quality
- Sentence diversity overhaul: scaffold freshness, retire old sentences, stronger generation
- Add Arabic word sanitization: clean DB, harden all import paths
- Add word frequency data, CEFR levels, suspend/flag system, action menu
- Quality hardening: safe JSON parsing, N+1 fix, typed frontend, 37 new tests
- Harden ingestion: hamza-aware dedup, MLE disambiguator, clitic-aware imports

### NLP
- LLM-confirmed variant detection: CAMeL candidates + Gemini Flash verification (77 merges applied)
- Consolidate root validation into shared is_valid_root() in morphology.py
- Auto-backfill root meanings on all import paths

### Story Reader
- Redesign story reader with Arabic font
- Morphological fallback for story word lookup
- Complete story import→learn→read pipeline + word provenance
- Handle proper nouns in story import as function words

### UI Polish
- Design pass on stories and words screens
- Compact word detail: back button, Arabic-forward layout, grammar form chips
- Add Clear Cache button, fix root meaning overflow
- Prioritize sentence generation by due date, cap pipeline at 200

### Infrastructure
- Add pre-deploy checks: layout lint + TypeScript validation
- Add activity logging to all batch scripts and CLI tool
- Add README with project overview, setup guide, and screenshots

## 2026-02-10

### OCR Textbook Scanner
- Add OCR textbook scanner and story image import via Gemini Vision
- Fix function word false clitic stripping, restructure OCR pipeline
- Add gloss validation to OCR pipeline and wiktionary import

### Other
- Rich intro cards mid-session, WordInfoCard improvements
- Day 3 learner analysis

## 2026-02-09

### Review UX Redesign
- Use Scheherazade New for Arabic text, redesign review layout
- Redesign word lookup panel: compact fixed-height box with root etymology
- Unify front/back word tapping: tristate cycle (off → missed → confused → off)
- Modernize review UX, word detail, and chat context/markdown

### Morphology & Variants
- Add confused/misread review state + CAMeL Tools morphology integration
- Variant cleanup with never-merge list for production DB false positives
- Redesign word info card with deep analytics, review lab, reintro pipeline

### Grammar
- Activate grammar learning pipeline: tracking, lessons, selection, UI
- Update grammar tagger to 48 features, add LLM forms backfill script

### Sentence Generation
- Add retry feedback for sentence generation failures
- Auto-introduce collocate words when sentence generation fails

### Other
- Add root extraction backfill script using LLM
- Add npm install to deploy pipeline for frontend dependencies

## 2026-02-08 — Initial Release

### Core App
- FastAPI backend with SQLite (single user, WAL mode)
- Expo (React Native) frontend for iOS and web
- FSRS spaced repetition (py-fsrs v6)
- LiteLLM with multi-model fallback (Gemini Flash → GPT → Claude Haiku)

### Review Modes
- Sentence-first review with greedy set-cover algorithm
- Reading mode with diacritized Arabic and tap-to-lookup
- Listening mode with ElevenLabs TTS (replay, slow playback)
- Learn tab: one word at a time with sentence quiz

### Story Mode
- Generate micro-fiction with known vocabulary (GPT-5.2)
- Import any Arabic text with word lookup
- Coordinate-based word tap detection for RTL text

### Vocabulary Management
- Import scripts: Duolingo (196 words), Wiktionary, AVP A1
- Al-prefix deduplication, knowledge scores
- Word list with study word filtering
- Offline sync queue with idempotent reviews

### AI Features
- AI chat for sentence explanation
- Batch sentence generation pipeline
- Batch audio generation with optimized Arabic TTS

### Infrastructure
- Docker Compose deployment for single VPS
- Hetzner server with DuckDNS domain
- Deploy/backup scripts with GFS retention
- Alembic migrations (auto-run on startup)
- JSONL interaction logging
