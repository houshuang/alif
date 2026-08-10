# Changelog

All notable changes to Alif — Arabic Reading & Listening Trainer.

This project is developed with Claude Code and Codex.

---

## 2026-08-10

- Make the 40-session flight download real: retain all downloaded sessions
  beyond the online freshness window, keep capacity for active/background
  sessions, and let Next Session consume stale cached material in airplane mode
  or after a weak-signal fetch failure.
- Keep offline review evidence queued indefinitely across repeated server
  errors instead of silently dropping it after eight attempts.
- Stream Codex prompts through stdin so embedded maintenance-passage generation
  cannot exceed the operating system's command-line argument limit.

## 2026-08-09

- Bound embedded v2 maintenance stories to 35–45 mapped Arabic words, default
  them to three sentences, and require selected-target form variation with a
  cap on identical-form repetition.
- Pair each revealed story sentence with its translation/transliteration while
  preserving inline lookup and red/yellow word marking.
- Replace length-confounded session-comprehension prominence with red/yellow
  word percentages and honest activity units (cards, story cards, child
  sentences, Arabic words, and reviewed-lemma judgments); expose the same
  reading-yield totals in deep analytics.

## 2026-07-30 — Reviewed *Momo* Chapter 1 prefill boundary

- Add a fixed, independently reviewed seven-row manifest for the safe Chapter
  1 preparation cohort, including source-preserving full tashkīl, faithful
  English, and independently reviewed transliteration; keep the three ambiguous
  neighboring rows explicitly excluded
- Add a dry-run/plan/apply prefill utility that pins the manifest, plan, child
  mappings, database path, clean deployed-main Git commit, and script bytes;
  requires a fresh mode-0600, checksum-matched, integrity-checked online backup
  with no SQLite sidecars and whose Alif schema, exact preimages, and static
  inventory correspond to live; rejects inode aliases of the live database and
  holds both the shared material lock and a SQLite writer boundary
- Create plan files privately and exclusively without following symlinks or
  overwriting an existing file, database, or SQLite sidecar
- Compare-and-set only Arabic, English, and transliteration for all seven rows
  or none, with same-transaction hash provenance and explicit invariants
  proving no activation, QA/mapping stamps, targets, vocabulary, learner state,
  reviews, evidence, or excluded rows changed
- Keep linguistic QA, contextual mapping verification, target validation, and
  final inactive preparation in the normal scoped corpus pipeline with
  activation zero and no provider pin
- Catch two deterministic identity collisions in the first production-copy
  normal-pipeline rehearsal: sentence 52133's fully vocalized `أُنَاسٌ` was
  routed to `نَسِيَ` #3711 instead of `نَاسٌ` #270, and `فَقَدْ` to noun
  `فَقْد` #2189 instead of particle `قَدْ` #2054
- Add fail-closed, code-only exact-surface resolution for those two approved
  identities. Require a unique gated destination and no stored exact-source
  conflict; preserve the sentence surface, replace the lossy target identity,
  and prevent CAMeL, proper-name creation, story/import, and NULL-repair
  fallbacks from widening the policy to unvocalized or other case forms
- Carry the same full-surface guard through verifier correction, mapping
  rescue, Discover, OCR, Quran, frequency-core intake, story compliance and
  persistence, reading-readiness/scaffold analysis, Hindawi name inference,
  and repair scripts. An unresolved identity remains unmapped and unclassified
  rather than becoming a free function word or a new lemma
- Require the destination to be the sole stored exact identity and gated
  (a gated row plus any ungated duplicate now fails closed), rebuild Discover
  alias metadata between independently committed batch items, reject
  conflicting Discover senses, refresh repaired StoryWord gloss/knowledge
  metadata, and preserve exact citations in all manual vocabulary importers
- Deploy the exact-surface resolver as PR #242 (`ab62535a`), verify a clean
  backend restart, and confirm the live resolver chooses `نَاسٌ` #270 for
  `أُنَاسٌ` and `قَدْ` #2054 for `فَقَدْ`
- Stop a second fresh production-copy rehearsal at 6/7 when the generic
  generated-sentence reviewer misclassified the established fictional name
  `مُومُو` and mildly formal published translation prose; activate nothing and
  prove the production exact-seven plus exclusions remain byte-identical to
  the pre-rehearsal backup
- Add the versioned `MOMO_PUBLISHED_ARABIC_V1` provenance policy to quality
  review only when live row metadata is exactly `source=corpus`,
  `kind=momo_book`. The allowlisted prompt note is not an acceptance override:
  mixed policies use separate provider calls, completed negative verdicts
  remain terminal, retries retain row identity, and QA writes compare-and-set
  source/kind as well as Arabic/English
- Reuse the same metadata-derived policy for active-sentence review and
  due-dense salvage so later maintenance cannot contradict corpus preparation;
  generic/generated quality-review prompts retain their existing rubric
- Deploy the source-aware review as PR #243 (`6ec5c8cb`), after an exact
  detached run of 1,895 backend tests and an adversarial review that found and
  closed the all-CAS-miss SQLite writer-lock path in due-dense salvage
- Pass a brand-new copied-production rehearsal 7/7, then apply the separately
  planned and backed-up exact-seven production preparation with every retry,
  rejection, blocker, diagnostic, translation, and activation result empty.
  All seven rows remain inactive; the active count stays 1,960 and no other
  *Momo*, learner, vocabulary, review, evidence, or unrelated table state moves

## 2026-07-29 — Verifier and authentic-corpus seam hardening

- Isolate mapping-verifier semantic failures to the attributable sentence row
  while retaining batch-fatal top-level, cardinality, and index-ownership
  checks; generation, rescue, and corpus callers now explicitly skip/retry the
  invalid marker and preserve clean siblings
- Route bare hamzated `أن`/`إن` and prefixed `بأن`/`وإن`/`وأن` through
  hamza-preserving contextual pairs, fail closed on unhamzated `ان`, preserve
  exact `آن` and lexical `بان`/`لأنّ`, resolve fully vocalized identities
  before target matching, and reject generated material if its required
  canonical target disappears after disambiguation/correction
- Extend the same identity layer to `فأن`/`فإن`; canonicalize productive
  attached-pronoun forms to base `أَنَّ`/`إِنَّ` without losing their surface,
  keep lexical `لأنّ` authoritative, and fail closed on unhamzated `فان`,
  unsupported ب+إن composition, and contextless citation ambiguity
- Deploy PR #239 as `fa5c646c`; exact ten-row and safe seven-row Momo dry-runs
  passed, but the live prepare-only call released all seven rows after provider
  failover produced no exact-gate-valid enrichment. The integrity-checked
  backup and all corpus/learner/history fingerprints were preserved; zero rows
  were prepared or activated
- Diagnose that fallback output had valid IDs/translations but normalized
  punctuation/spacing in every row and `ى`/`ي` spelling in one row; project
  only validated ordinary harakat onto the immutable source, reject all
  content/word-boundary/identity-bearing-mark mutations, expose exact token
  spellings in the prompt, and add a validated provider pin for controlled
  corpus operations
- Make completed-QA authentic rows governor-exclusive by excluding them from
  due-dense salvage and green-page book reactivation
- Preserve existing Arabic/transliteration or translation during one-field
  corpus enrichment, require gated lookup/corrections, keep suspension races
  retryable, and bypass empty activation scans
- Guard every external corpus phase with exact content compare-and-set checks;
  concurrent text or mapping edits preserve the external work while
  independently invalidating only unchanged derived stamps. Activation takes
  one short SQLite writer boundary, reloads selected-lemma demand/capacity, and
  compares planned parent/mapping snapshots before claiming visibility
- Recover safely from ID-less quality-review API fallback output by retrying
  unresolved sentences one at a time; batch array order is never trusted
- Add a hash-pinned, backup-confirmed two-phase exact-identity repair. A
  production-derived rehearsal repaired 1,208 collateral mappings atomically,
  excluded seven target-sensitive rows, preserved the 1,950 active count, and
  produced a zero-row second plan; apply holds both the shared material lock
  and a database writer boundary from live validation through commit
- Deploy PR #236 as `41d03e96`, take an integrity-checked online SQLite backup,
  and apply the reviewed production plan to exactly 1,208 mappings after the
  shared lock safely rejected an overlap with the normal material cron. The
  repair excluded all seven target-sensitive rows, preserved the live
  1,953-sentence activation snapshot and all QA/review/target state, logged
  ActivityLog #3886, passed `PRAGMA integrity_check`, and produced a zero-row
  second plan

## 2026-07-29 — Reviewed *Momo* dictionary inventory (PR #234)

- Add exactly three independently reviewed scaffold lemmas needed by the
  copied-snapshot *Momo* rehearsal: `كُلِّيّ` (total/overall), `إِلٰه`
  (god/deity), and `فَعَلَ` (to do)
- Harden `import_scaffold_lemmas.py` with repeatable exact `--only` scope,
  NFC-normalized vocalized identity, fail-closed metadata/root checks, and
  resumable quality gates after an interrupted post-insert run
- Pin the three reviewed existing roots (`ك.ل.ل`, `ء.ل.ه`, `ف.ع.ل`) so
  enrichment cannot create suffix/hamza-derived duplicate roots
- Deploy PR #234 as `e20148b7`; production created gated canonical lemmas
  #4530–4532 and reused roots #198/#809/#103
- Verify zero new learner, review, frequency-core, or sentence-mapping rows;
  all 243 *Momo* sentences remain inactive, untranslated, unverified, and
  un-quality-reviewed; corpus cron remains disabled and the active pool remains
  exactly at its 1,950 ceiling
- Preserve an immediate integrity-checked online backup at
  `/opt/alif-backups/alif_pre_pr234_momo_inventory_20260729.db`
  (`sha256=fd1a9eeeb81f36a80242e899c1172d3fbf1b4d325ea4722de45dffa2e61f3183`)
  and record the operation as ActivityLog #3879

## 2026-07-28 — Scoped corpus preparation hardening (PR #232)

- Harden scoped corpus preparation with a cursor-progressive deterministic
  preflight, early authentic QA, explicit per-row mapping verdicts, exact-row
  retries, compare-and-set writes, full Arabic identity checks, and no corpus
  lemma creation
- Separate the transient `2000-01-01` claim from durable `2000-01-02`
  inventory/mapping blocks and `2000-01-03` linguistic-QA rejections; durable
  blockers reopen only through an explicit exact-ID retry after curation
- Rehearse on a disposable working copy derived from the immutable production
  snapshot: two exact rows prepared cleanly, one received a terminal
  naturalness rejection, no row activated, and learner tables were unchanged
- Replay all 243 *Momo* rows: 235 are inventory-complete; the remaining eight
  contain nine standalone OCR-spaced `و` tokens and require separately
  confirmed source normalization, not vocabulary backfill
- Prospectively repair stale target bookkeeping atomically when mappings
  change; preserve historical review observations and keep any exact repair of
  the 18 affected reviewable production rows separately confirmation-gated
- Keep corpus cron disabled. The code-only deployment performs no corpus or
  learner-data mutation, and the snapshot's 1,961 active rows leave zero
  activation capacity under the 1,950 ceiling
- Deploy PR #232 as `094ee1c1` on 2026-07-28 at 22:44 UTC. The backend returned
  HTTP 200 after restart, the corpus-enrichment flag remained absent, and the
  immediate before/after corpus and learner-history counts were identical
- Record the July 28 production learning audit and its confirmation boundaries;
  no production corpus or learner data was changed

## 2026-07-27

- Deploy through PR #231 (`0f374904`): policy-aware FSRS calibration, assisted
  rating-2 lapses, Box-1 exposure reduction, graduation-policy alignment,
  exact-token form/tashkīl evidence, persistent optional yellow-cause capture,
  and lemma-aware function-word homographs

## 2026-07-26

- Validate and deploy the learning-system foundation, including FSRS 6.3.1
  pinning/config telemetry, acquisition graduation success gating, and
  workload-neutral established-lapse recovery (PRs #224–226)

## 2026-07-25

- Add protocol-v2 rapid re-tests for rating-1 failures with counter-neutral
  acquisition safety and no quiz-driven graduation (PRs #220/#223)
- Strip stored LLM web citations and disable Codex web search for learner-facing
  enrichment; add review-debt urgency stats (PRs #221/#222)

## 2026-07-21

- Add gloss/POS sense-gating for same-skeleton citation-form homographs (PR #219)

## 2026-07-20

- Add one-per-day reintroduction cooldown, reserve rescue intro slots, and show
  recovery burndown (PR #217)
- Re-enable recognition-direction mnemonics behind an independently calibrated
  storage judge (PR #218)

## 2026-07-15

- Add citation-strict `/api/discover/add` lookup and remediate collision damage;
  import 243 hand-vetted inactive *Momo* corpus sentences (PRs #211–216)
- Make lemma enrichment backlog honest, bounded, and durable; repair corrupt
  citation bare forms and add recovery/*Momo* statistics

## 2026-07-10

- Deploy PR #208 (`13b25e3`) to production: sustained-break recovery gating, bounded episode-local leech reintroduction, the exact-surface yellow pilot, and explicit primary-story curriculum selection. Backend/API smoke checks passed, Alembic remained at `d4f6a8b0c2e4`, and no data backfill was required.

## 2026-07-09

- Make informational leech reintro cards acknowledgement-only; Continue no longer writes a synthetic successful review
- Separate acquisition episode type (`new`/`leech_reintro`) from curriculum provenance with a nullable Alembic migration
- Correct recovery overload to include due previously-seen Box-1 debt and gate intake on primary reading cards/accuracy
- Make mature duplicate-card skipping preserve every due obligation, canonical-form failures, and acquisition repetitions
- Make speculative session prefetch read-only for learning state and exclude cards that require cold-word promotion
- Correct FSRS-cleared and state-transition analytics; add primary non-acquisition recall bands by elapsed gap
- Deploy PR #207 to production with the additive acquisition-episode migration; leave historical episode kinds unbackfilled
- Add a sustained-break recovery trigger at 750 strict main-lane FSRS due cards while preserving the earned 0/8/30 intake budget
- Bound leech reintroduction to 8/day below Box-1/Box-2/FSRS debt ceilings and require five fresh episode-local reviews before re-suspension
- Add a migration-free, reading-only 50/50 exact-surface retrieval pilot for non-trivial yellow morphology events; no extra cards or schedule changes, and only one unresolved episode per lemma
- Require explicit `Story.metadata_json.curriculum_role="primary"` before an imported story receives the strong target-text intake tier
- Document the production-data analysis, simulation thresholds, monitoring windows, and contemporary/classical curriculum boundary; implement and independently review the next-phase code

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
