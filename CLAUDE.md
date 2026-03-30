# Alif — Arabic Reading & Listening Trainer

## Project Overview
A personal Arabic (MSA/fusha) learning app focused exclusively on reading and listening comprehension. No production/writing exercises. Tracks word knowledge at root, lemma, and conjugation levels using FSRS spaced repetition. Combines LLM sentence generation with deterministic rule-based validation (clitic stripping + known-form matching).

## Quick Start
```bash
# Backend
cd backend
cp .env.example .env  # add API keys
pip install -e ".[dev]"
python3 scripts/import_duolingo.py  # import 196 words
python3 -m uvicorn app.main:app --port 8000

# Frontend
cd frontend
npm install
npx expo start --web  # opens on localhost:8081
```

## Architecture
- **Backend**: Python 3.11+ / FastAPI / SQLite (single user, no auth, WAL mode, 30s busy_timeout) — `backend/`
- **Frontend**: Expo (React Native) with web + iOS mode — `frontend/`
- **SRS**: py-fsrs v6 (FSRS-6 with same-day review support) — `backend/app/services/fsrs_service.py`
- **LLM**: Two-tier model strategy. Background/cron tasks use Claude CLI (free via Max plan): Sonnet for sentence gen, Haiku for quality gate + enrichment + hooks. On-demand/user-facing tasks keep Gemini Flash (fast, ~1s). Story gen: Claude Opus (retry loop). General fallback: Gemini 3 Flash → GPT-5.2 → Claude Haiku API. Keys: GEMINI_KEY, OPENAI_KEY, ANTHROPIC_API_KEY in `.env`
- **Claude Code CLI**: `claude -p` wrapper for free LLM via Max plan. Integrated into `llm.py` as `claude_sonnet`/`claude_haiku` model overrides. Also: standalone `generate_structured()` + `generate_with_tools()` in `claude_code.py`. See `docs/backend-services.md`.
- **TTS**: ElevenLabs REST, `eleven_multilingual_v2`. Voice: PVC clone of @roots_of_knowledge Arabic teacher (voice_id `G1HOkzin3NMwRHSq60UI` = "Arabic Knight" PVC). New PVC `zZplJlGYgfVjN9bBzAWS` training with 77 min curated audio (pending verification). IVC v2 `CgiZNnLDkBFp39WsQkMb` available as interim. Voice pool (3 voices) for story audio rotation. Key: ELEVENLABS_KEY in `.env`. Audio cached by SHA256 in `backend/data/audio/`. Story audio in `backend/data/story-audio/`.
- **NLP**: Rule-based clitic stripping + known-form matching + CAMeL disambiguation fallback. `LemmaLookupDict` tracks collisions (hamza-sensitive resolution). Two-pass lookup: bare forms first, forms_json second (prevents derived forms shadowing direct lemmas). Extended forms_json indexing (past_3fs, past_3p, imperative, passive_participle). **LLM disambiguation**: when collisions or multiple clitic candidates exist, `disambiguate_mappings_llm()` uses sentence context (Gemini Flash) to pick the right lemma — runs at generation time before verification. **Mapping correction pipeline** (always on): `verify_and_correct_mappings_llm()` checks all mappings, asks Gemini for corrections instead of discarding. `correct_mapping()` finds/creates correct lemma in DB. `TokenMapping.via_clitic` tracks clitic-derived mappings for extra scrutiny. Corrections logged to `data/logs/mapping_corrections_*.jsonl`. **Quranic orthography**: ta maftouha → ta marbuta fallback in Quran lemmatization (word-final ت → ة re-lookup). See `docs/nlp-pipeline.md`.
- **Migrations**: Alembic for SQLite. Every schema change needs a migration. Auto-runs on startup.
- **Hosting**: Hetzner (46.225.75.29), docker-compose. Backend port 3000→8000. Frontend systemd (`alif-expo`) port 8081. DuckDNS: `alifstian.duckdns.org`. Claude CLI bind-mounted into container (node + claude binary + auth config from host).
- **Offline**: AsyncStorage sync queue for all mutable actions (sentence reviews, story actions, word introductions, reintro results, experiment intro acks, grammar introductions). 30-min session staleness TTL, stale allowed when offline. Auto-prefetch: 2 sessions cached in background after every session load. Deep prefetch (up to 20) via More tab button. Background session refresh (15-min gap detection). 12s fetch timeout with stale-cache fallback. Reviewed-set pruning when >1000 entries (keeps only keys matching cached sessions). Word lookup cache: 24h TTL, stale fallback offline, bypasses cache when cached result has no gloss (forces refetch from backend).

## Reference Docs
| Doc | Contents |
|-----|----------|
| `docs/scheduling-system.md` | Word lifecycle, session building, FSRS/acquisition phases, all constants |
| `docs/backend-services.md` | All backend service descriptions with key behaviors |
| `docs/frontend-files.md` | All frontend screens, components, and infrastructure files |
| `docs/data-model.md` | SQLAlchemy models and table schemas |
| `docs/api-reference.md` | Full API endpoint reference |
| `docs/nlp-pipeline.md` | NLP pipeline: clitic stripping, CAMeL Tools, morphology |
| `docs/review-modes.md` | Full UX flows for all review modes |
| `docs/scripts-catalog.md` | All import, backfill, cleanup, analysis scripts |

## Review Modes (summary)
- **Sentence-First Review**: greedy set cover, ternary ratings, all words get equal FSRS credit
- **Reading Mode**: front-phase word lookup (root/pattern/sibling navigation), triple-tap marking, back/undo, confusion analysis on yellow tap
- **Listening Mode**: ElevenLabs TTS, reveal Arabic → reveal English
- **Learn Mode**: 5-candidate pick → done (info-dense card with root/pattern chips → detail pages, pattern examples, forms strip, etymology, mnemonic)
- **Story Mode**: generate/import, tap-to-lookup reader (root/pattern navigation), complete/suspend/archive. 4 formats: standard, long (12-20 sentences), breakdown (audio: half→full sentences), arabic_explanation (simple Arabic explanations). Story audio via ElevenLabs with voice rotation (3 male voices, deterministic by story_id). Archive system (orthogonal to status). `times_heard` passive listening tracking. Auto-generate cron keeps ≥3 active non-archived stories.
- **Quran Reading Mode**: Verse-by-verse reading interleaved in review sessions. Front: Arabic text (Uthmani tashkeel, Scheherazade font, 36pt/30pt front/back with ~1.9x lineHeight for diacritics, RTL word layout via `row-reverse`) with full word interaction — ALL words tappable (including function words, which show gloss-only card), prev/next arrows to navigate between tapped words, persistent highlights removable by re-tap, ActionMenu (Ask AI, flag lemma, refresh) on ProgressBar. Uses shared `tappedOrder`/`tappedCursor`/`tappedCacheRef` infrastructure from sentence review. Back: flip to reveal English translation (Sahih International) + ALA-LC transliteration (via `transliterate_arabic()`, not risan/quran-json phonetic data), rate as "Got it" / "Partially" / "Not yet". Simple level-based SRS (not FSRS): 1d → 3d → 7d → 14d → 30d → 60d → graduated. 2 new verses per session (max 5 total), gated by non-understood backlog < 20. Rapid repetition: "Not yet" = due next session, "Partially" = 2h. "Got it" button available on front side (skip translation). Sequential from Al-Fatihah. Data: `QuranicVerse` + `QuranicVerseWord` tables, 6236 verses imported from risan/quran-json CDN. Lazy lemmatization: `lemmatize_quran_verses()` processes next 20 verses when buffer runs low. **Ta maftouha fallback**: when Quran lemmatization fails to find a lemma, tries replacing word-final ت with ة and re-lookup — handles ~15 high-frequency Quranic words (رحمت/رحمة, نعمت/نعمة, etc.). **Hamzat al-wasl fallback**: `_hamzat_wasl_lookup()` restores dropped initial alef after proclitic stripping (بسم → ب + اسم) — handles Arabic morphophonological rule where hamzat al-wasl words lose their alef when preceded by proclitics. **New Quran lemma creation**: `_create_unknown_quran_lemmas()` gets general Arabic glosses (not Quran-specific theological meanings), extracts consonantal roots in the same LLM call, links/creates Root records, and triggers `enrich_lemmas_batch()` for forms/etymology/transliteration automatically. **Function word glosses**: `_QURAN_FUNCTION_GLOSSES` dict provides glosses for Quranic pronouns and particles (إياك, إياه, الم, etc.) populated in verse API response. **Quran-only lemmas stay "encountered"** — never auto-enter the learning pipeline. Gold accent (#d4a056) distinguishes from regular blue-accented cards. Service: `quran_service.py`. Import: `scripts/import_quran.py`. API: `POST /api/review/verse`, verse cards in session response.
- **Podcast Mode**: Audio learning episodes in 3 formats: (1) **story** — LLM-generated stories from high-stability vocabulary, (2) **book** — existing DB stories (books, Qur'an) with automatic long-sentence breakdown, (3) **ci** — Arabic-in-Arabic comprehensible input (5-phase: establish context → circumlocute new word → build complexity → full passage → close). Long sentences (≥8 words) auto-broken into ~4-word chunks, each paired with matching English fragment. Explicit "Complete" button increments `times_heard` on all content words via pre-computed `word_lemma_ids` in metadata, closes detail view, moves episode to "Completed" list. Auto-generation cron (Step I) maintains ≥4 unheard episodes, alternating story and CI formats. File-based storage: MP3 + JSON pairs in `data/podcasts/`. 25 story themes + 12 CI topics. Scripts: `generate_story_podcasts.py` (`--from-story N`, `--count N`, `--ci-topic "..." --ci-target "word:gloss"`). API: `/api/podcasts`, `/api/podcasts/complete/{fn}`.

## Design Principles
- **FOUNDATIONAL: Every word in every sentence earns review credit** — when a sentence is reviewed, ALL non-function words get a review (acquisition or FSRS), regardless of whether they are the "target" word or collateral scaffold. This is the core learning mechanism. A word seen 10 times collaterally with correct ratings has been learned — the system must recognize this. No word should be invisible to the review engine. Encountered words that appear in reviewed sentences are auto-introduced to acquisition and get their first review immediately; Tier 0 instant graduation handles familiar words (recognized on first review → straight to FSRS). **No artificial throttles on this flow.**
- **Word introduction is automatic** — `build_session()` reserves `INTRO_RESERVE_FRACTION` (20%) of session slots for new words, even when due queue exceeds limit. Accuracy-based rate: <70%→0, 70-85%→3, ≥85%→5 slots. Per-call cap: MAX_AUTO_INTRO_PER_SESSION=5. **Pipeline backlog gate**: reserved intro slots suppressed when acquiring count exceeds a dynamic threshold that scales with 2-day accuracy: ≥90%→80, ≥80%→60, <80%→40 (base `PIPELINE_BACKLOG_THRESHOLD`). Undersized-session fill still works. **Never-reviewed boost**: acquiring words with `times_seen == 0` OR zero accuracy (`times_correct == 0`) get `NEVER_REVIEWED_BOOST` (5.0x) score multiplier so their single-target sentences compete against multi-word FSRS sentences in greedy selection — prevents box-1 starvation where new words never get surfaced. **Unknown scaffold cap**: `MAX_UNKNOWN_SCAFFOLD` (2) — sentences with >2 unknown non-target words are rejected, preventing overwhelming density after large OCR batches. **Fill phase always runs** when session is undersized — in fast mode uses `_find_pregenerated_sentences_for_words()` (DB queries only, no LLM); in prefetch mode uses `_generate_on_demand()`. OCR/story import creates "encountered" state only.
- **All words get intro cards equally** — target vs collateral distinction only matters for sentence generation coverage, not for learning credit or intro card eligibility. Once a sentence is in a session, ALL words are equally important. The intro card filter is purely: `times_seen == 0`, `experiment_intro_shown_at is None`, `total_encounters < 5`. Words with high encounter counts (≥5, e.g. from extensive story reading) skip intro cards since they're already familiar. **Variant skip**: variants whose canonical lemma is already known/learning skip intro cards (e.g. بنية skipped when بني is known).
- **Intro cards for all new words (A/B experiment concluded 2026-03-21)** — every new acquiring word gets an intro card before its first sentence review. Card-first showed +28pp first-review accuracy, 2.4x faster graduation vs sentence-first. **Rescue cards**: acquiring words with ≥4 reviews and <50% accuracy get a re-teaching intro card (7-day cooldown). Both types limited to words with sentences in the current session. **Capped at `MAX_INTRO_CARDS_PER_SESSION` (5)** to avoid overwhelming sessions. `_build_intro_cards()` in `sentence_selector.py`. **Session interleaving** (2026-03-30): intro cards are distributed among review sentences (2 first, then 1 every 3 sentences) rather than front-loaded. Same-word sentences spaced apart via greedy max-gap algorithm in `buildInterleavedSession()` (`frontend/app/index.tsx`). **Card content**: FormsStrip with ALA-LC `forms_translit`, etymology before memory hook, `source` label — prefers `ulk.source` (book, textbook_scan, duolingo, story_import) over `lemma.source` (dictionary provenance), via `_display_source()` helper.
- **Instant graduation (Tier 0) is correct** — if a user recognizes a word on first review, they know it. Moving it through Leitner boxes wastes session slots. Tier 0 (first correct review → FSRS) is the intended fast path for familiar words. FSRS safety net catches any false positives (word lapses → stability drops → reviewed sooner).
- **No concept of "due"** — the app picks the most relevant cards. Don't use "due" in UI text. Use "ready for review".
- **No bare word cards in review** — ONLY sentences. Generate on-demand or skip if no comprehensible sentence.
- **Comprehensibility gate** — ≥60% known scaffold words required. All acquiring words count as known (they've been introduced). Encountered excluded (only actively studied words count).
- **Function words** — ~80 particles/prepositions/pronouns/conjunctions (populated from `FUNCTION_WORD_GLOSSES` in `sentence_validator.py`). Excluded from story/book "to learn" counts, book page word introduction, FSRS review credit, scheduling/due counts, and scaffold diversity checks. They still appear in sentences and get glosses. Detection checks both surface form AND resolved lemma bare form (catches cliticized forms like بِهِ → بِ).
- **Story word counts are deduped and live** — `total_words`, `known_count`, `unknown_count` count unique lemmas, not tokens. Each lemma counted once even if it appears multiple times in the story. Counts recalculate live on every `get_story_detail()` call (not on the list endpoint — too expensive with write locks). Recalculation also re-checks function word flags and resolves variant→canonical knowledge via multi-hop chain following (A→B→C uses C's known state). Additionally returns `cold_unknown_count` (unknown, no known root sibling), `warm_unknown_count` (unknown but ≥1 known root sibling), and `reading_readiness_pct` = `(known + 0.6 × warm) / total × 100`. Pretesting: `GET /api/stories/{id}/pretest-words` returns top 5 cold unknowns by story token frequency.
- **No on-demand sentence generation in session build** — sessions build entirely from pre-generated sentences (DB queries only, <1s). `warm_sentence_cache()` generates for gaps after each session. The cron generates via `generate_material_for_word()` every 3h. Fill phase uses `_find_pregenerated_sentences_for_words()` for undersized sessions.
- **Tapped words are always marked missed** — front-phase tapping auto-marks as missed (rating≤2). Yellow (confused) words get Rating 2 (Hard) — brings next review sooner without lapsing. Red (missed) words get Rating 1 (Again).
- **Confusion analysis on yellow tap** — when a word transitions to "did not recognize" (yellow), `confusion_service.py` analyzes why: (1) morphological decomposition via clitic stripping + form matching, (2) visual similarity via edit distance + rasm skeleton (dots-removed), (3) phonetic similarity via `PHONETIC_MAP` (ص≈س, ح≈ه, ع≈أ, ط≈ت, etc. — catches sound-alike confusions like سبع↔صباح), (4) prefix disambiguation hint (و/ف/ب/ل/ك — "part of root" vs "is prefix"). Similarity pool includes `encountered` state. All rule-based, no LLM, <50ms. Endpoint: `GET /api/review/confusion-help/{lemma_id}?surface_form=...`.
- **al-prefix is NOT a separate lemma** — الكلب and كلب are the same lemma. All import paths dedup.
- **Be conservative with ElevenLabs TTS** — costs real money. Only generate for sentences that will be shown. Story audio is more expensive (full story text) — only generate when requested or via cron.
- **Voice cloning** — Current voice is a PVC of @roots_of_knowledge (Arabic MSA teacher, YouTube/TikTok). Audio extracted from YouTube (`yt-dlp`). IVC uses generic multilingual model phonemes (flattens emphatics ظ/ض). PVC fine-tunes model weights on speaker's actual pronunciation. ElevenLabs PVC API is multi-step: create voice → upload samples (≤11MB each, must pass Arabic language detection) → verification (manual or captcha) → train. See `research/voice-cloning-writeup-2026-03-01.html` and `research/tts-alternatives-2026-03-22.md`.
- **Sentence pipeline**: tier-based lifecycle, no fixed cap binding. Tier 1 (due ≤12h): target 3 sentences, floor 2. Tier 2 (12-36h): target 2, floor 1. Tier 3 (36-72h): target 1, floor 0. Tier 4 (72h+): target 0, floor 0 — sentences actively retired. Safety valve cap at 2000 (should never bind). Pool size bounded by review urgency (~200 tier 1-3 words), not vocabulary size. Cron runs `update_material.py` every 3h. `warm_sentence_cache()` runs after every session load.
- **Verb conjugation recognition** — `build_lemma_lookup()` Pass 3 generates ~36 conjugation forms per verb (past suffixes + present prefix/suffix combinations). Weak verb support: uses `past_1s` from forms_json for irregular stems (قال→قلت, مشى→مشيت). Noun inflection: generates sound plurals (ـات/ـون/ـين) and dual forms. Pass 2 indexes ALL string keys from forms_json (no hardcoded whitelist). LLM enrichment provides expanded forms: `past_1s`, `past_3fp`, `present_3fp`, `present_3mp`, `sound_f_plural`, `sound_m_plural`, `dual`.
- **Canonical lemma is the unit of scheduling** — variant forms tracked via `variant_stats_json` but never get independent FSRS cards. **Multi-hop chain resolution**: variant chains (A→B→C) are followed to the root canonical everywhere: story knowledge maps, word introduction priority (`_resolve_to_canonical`), book page priority mapping, **review credit** (`sentence_review_service.py`), and **session building** (`sentence_selector.py`). Without this, a variant whose canonical is itself a variant would never resolve correctly. Bug fix (2026-03-23): single-hop resolution in review service caused variants like غرفة to be introduced despite root canonical غرف being known.
- **All import paths must run variant detection** — `detect_variants_llm()` + `detect_definite_variants()` + `mark_variants()` post-import.
- **All import paths must run quality gate** — `import_quality.classify_lemmas()` filters junk, classifies standard/proper_name/onomatopoeia.
- **Every sentence_word must have a lemma_id** — all 5 storage paths reject unmapped words. Exception: book_import keeps sentences with `lemma_id=None`. Mapping uses `build_comprehensive_lemma_lookup()`.
- **All sentence generation must go through `generate_material_for_word()`** — this is the single verified pipeline: disambiguation → LLM verification → correction → `mappings_verified_at`. Scripts (`update_material.py`, `pregenerate_material.py`, `generate_sentences.py`) and `warm_sentence_cache()` all use it. Never create a separate generation path that skips verification — this was the source of 29 bad-mapping flags (2026-03-21 fix).
- **Lemmatization feedback loop** — Four layers: (1) **Generation-time correction**: `verify_and_correct_mappings_llm()` catches wrong mappings before storage. `correct_mapping()` finds the correct lemma in DB — if not found, the sentence is **rejected** (never auto-creates lemmas). (2) **Background batch verification**: `verify_sentence_mappings()` checks existing sentences via single batched LLM call (up to 20 per call). Runs in `warm_sentence_cache` Phase 4 (background catch-up after every session). Unfixable sentences (correct lemma not in DB) are **retired**. Tracks `Sentence.mappings_verified_at` — NULL=unchecked, timestamp=verified. (3) **User flag resolution**: when a word_mapping flag identifies a wrong mapping, fixes it if correct lemma exists in DB, otherwise **retires the sentence**. Never auto-creates lemmas. Propagates fixes to other active sentences (LLM-verified, max 50). (4) **Disambiguation**: `disambiguate_mappings_llm()` resolves ambiguous tokens (collisions/multi-clitic) using sentence context.
- **No auto-created lemmas from corrections** — `correct_mapping()` and flag resolution only use existing DB lemmas. If the correct lemma isn't in the vocabulary, the sentence is rejected/retired. This prevents orphan lemmas that bypass quality gate, variant detection, and enrichment from becoming review targets.
- **No words without English gloss — EVER** — Three validation gates guarantee every word shown to the user has a gloss: (1) **Sentence storage gate**: `generate_material_for_word()` rejects sentences where any lemma has empty `gloss_en`. (2) **Quran 6-layer fallback**: lemma → function word dict → pronoun suffix decomposition → DB proclitic lookup → morphological lookup (38K forms) → LLM batch translation → transliteration last resort. (3) **Frontend cache bypass**: `lookupReviewWord()` skips 24h cache when cached result has no `gloss_en`. All lemma creation paths skip creating lemmas with empty `gloss_en`. `warm_sentence_cache` Phase 5 auto-backfills (up to 10/run). Tests: `test_gloss_coverage.py`.
- **Homograph-aware correction** — `correct_mapping()` accepts `current_lemma_id` to handle homographs (same bare form, different meaning — e.g. سلم "peace" vs سلم "ladder"). Searches `.all()` instead of `.first()`, prefers a different lemma from the one currently assigned. If only the same homograph exists, returns None (unfixable → sentence retired). Bug fix (2026-03-23): without this, corrections silently found the same wrong lemma and concluded "already correct."
- **Verification failure ≠ success** — `verify_and_correct_mappings_llm()` returns `None` on LLM failure (distinct from `[]` = verified OK). Tries Gemini → Claude Haiku fallback. Callers discard/skip sentences that can't be verified. Generation-time verification rejects bad sentences before storage; background Phase 4 catches stragglers.
- **No LLM calls in session build critical path** — `build_session()` must stay fast (<1s). All LLM work (sentence generation, mapping verification) happens at generation time or in `warm_sentence_cache` background tasks. A previous synchronous verification gate (step 4b) caused 30-60s timeouts and was removed (2026-03-17).
- **Tashkeel fading is backend-driven, front/back split** — `show_tashkeel` boolean per word in API response. Backend knows both the setting (mode + threshold) and word stability. Three modes: always, fade (hide diacritics for words with stability ≥ threshold), never. **Phase-aware rendering**: on card front, well-known words show without diacritics (reading challenge); on card back, full tashkeel is always restored (verification). Applies only to review sessions — story reader always shows full tashkeel. Production setting: fade mode, 90-day threshold (raised from 60d on 2026-03-21 after high-stability lapses). **Card-level 3-state toggle**: dot toggle cycles default (fade per backend) → all vowels → no vowels. Dot opacity indicates state (0.2/0.5/1.0). Overrides are per-card only, don't persist. **Graduated fading (2026-03-27)**: scaffold words (is_due=False) fade at `min(threshold/3, 30d)` — currently 30d — while target/due words fade at the full configured threshold. Scaffold words at 30d+ stability don't need the crutch; due words do.
- **Dual Arabic fonts (50/50 mixing)** — Review cards alternate between **Scheherazade New** (SIL, learner-optimized, conservative ligatures) and **Amiri** (Bulaq press tradition, aggressive ligatures matching printed books). Deterministic split by `sentence_id % 2` in `arabicFontForSentence()` (`theme.ts`). Builds familiarity with both learner-friendly and print-style typography. Font packages: `@expo-google-fonts/scheherazade-new`, `@expo-google-fonts/amiri`.
- **BiDi text direction** — Pure Arabic text uses `writingDirection: "rtl"`. Mixed Arabic+English explanatory text (etymology, mnemonics, cognates, usage notes, fun facts) uses `writingDirection: "ltr"` **plus** `ltr()` wrapper (prepends U+200E Left-to-Right Mark) from `theme.ts`. The LRM is needed because iOS Core Text determines paragraph direction from the first strong character, overriding the style — text starting with Arabic gets RTL layout even with `writingDirection: "ltr"`. The `ltr()` helper must wrap any mixed-language text content that could start with Arabic characters.
- **Tiered graduation** — acquisition uses aggressive graduation tiers: (0) first correct review → instant graduation, (1) 100% accuracy + 3 reviews → any box, (2) ≥80% accuracy + 4 reviews + box ≥ 2, (3) standard: box ≥ 3 + 5 reviews + ≥60% accuracy + 2 calendar days. Tiers 0-2 fire on **any review** (including collateral appearances), tier 3 requires word to be due. FSRS safety net catches false positives from fast graduation.
- **Root-aware stability boost** — words graduating from acquisition with 2+ known root siblings get `Rating.Easy` (~3.6x stability boost). `ROOT_SIBLING_THRESHOLD=2` in `acquisition_service.py`.
- **Morphological patterns (wazn)** — `Lemma.wazn` stores normalized pattern (e.g. "fa'il", "maf'ul", "form_2"), `Lemma.wazn_meaning` stores human description. Displayed in learn cards, word info cards, and word detail. Pattern family (other words with same wazn) returned in word detail endpoint. API: `/api/patterns` lists patterns, `/api/patterns/{wazn}` lists words with enrichment, `/api/patterns/roots/{root_id}/tree` shows root derivation tree. `PatternInfo` table stores per-pattern enrichment. Backfill: `scripts/backfill_wazn.py`, `scripts/backfill_pattern_enrichment.py`.
- **Root & pattern enrichment** — LLM-generated cultural/linguistic content for roots (`Root.enrichment_json`) and patterns (`PatternInfo.enrichment_json`). Auto-triggered when a word enters acquisition and its root/pattern has 2+ studied words but no enrichment. Root: Claude Sonnet (etymology, cultural significance, literary examples, fun facts, related roots). Pattern: Claude Haiku (explanation, recognition tips, semantic fields, example derivations, register notes). Backfill: `scripts/backfill_root_enrichment.py`, `scripts/backfill_pattern_enrichment.py`.
- **Explore tab** — Frontend tab with three sub-tabs: Words (existing word browser), Roots (browse/search all roots with coverage stats), Patterns (browse/search all patterns with coverage stats). Detail pages: `/root/{id}` shows enrichment + derivation tree, `/pattern/{wazn}` shows enrichment + word list. Cross-linked from word detail. API: `/api/roots`, `/api/roots/{id}`.

## Critical Rules for All Agents

### 1. IDEAS.md — Always Update
The file `IDEAS.md` is the master record of ALL project ideas. Read at start of work, add new ideas discovered during development, never remove ideas.

### 2. Interaction Logging — Log Everything
Every user interaction must be logged. Append-only JSONL files (`data/logs/interactions_YYYY-MM-DD.jsonl`). Schema:
```json
{"ts": "ISO8601", "event": "review", "lemma_id": 42, "rating": 3, "response_ms": 2100, "context": "sentence_id:17", "session_id": "abc123"}
```

### 3. Testability — Claude Must Be Able to Test Everything
- All logic in the API, never in the UI. Every service has pytest tests, every endpoint testable with curl.
- Web preview via `npx expo start --web`. Mock data in `frontend/lib/mock-data.ts`.

### 4. Skills — Generate and Update
Create reusable Claude Code skills (`.claude/skills/`) for common operations.

### 5. Experiment Tracking — Document Everything
- `docs/scheduling-system.md`: Update on ANY scheduling change
- `research/experiment-log.md`: Add entry BEFORE algorithm changes
- `research/experiment-log.md` is **append-only** — NEVER delete existing entries. New entries go at the top (after the header).
- `research/research-hub.html`: Update when adding new research documents (add doc-row entry in appropriate category section)
- `research/README.md`: Update when adding new research documents
- `research/analysis-YYYY-MM-DD.md`: Save analysis findings
- **All reports and analysis HTML pages go inside the repo** (in `research/`), not in external dirs like `~/.agent/diagrams/`. Link them from the experiment log entry that prompted them.

### 6. Git Diff Discipline — Prevent Silent Reverts
**CRITICAL**: Before every commit, run `git diff --stat HEAD` and review what changed. Watch for:
- **Append-only files shrinking** (`experiment-log.md`, `IDEAS.md`) — this means entries were deleted. NEVER acceptable.
- **Large service files with net deletions** — if `sentence_selector.py` or similar core files show significant line removals, verify those removals are intentional, not regressions.
- **Schema files losing fields** — if `schemas.py` or `types.ts` show removed fields, verify the backend doesn't still compute them.
- **When replacing/rewriting a file**, always diff the old version against the new one to check nothing was lost: `git diff HEAD -- path/to/file`
- **Bundled commits are dangerous** — if a commit touches >5 files across different features, split it or review each file's diff individually.

### 7. Branch Workflow for Non-Trivial Changes — Self-Review Gate
For changes that touch core algorithm files (`sentence_selector.py`, `session_builder`, `fsrs_service.py`, `acquisition_service.py`) or modify >3 files:
1. Create a branch: `git checkout -b sh/<feature-name>`
2. Make changes and commit on the branch
3. Create a PR: `gh pr create --title "..." --body "..."`
4. **Self-review the PR diff** before merging — look at every file's diff on GitHub (`gh pr diff`) and verify:
   - No unintended deletions in append-only files
   - No features silently removed from large files
   - No schema fields lost that the backend still computes
   - Net line counts make sense (a "feature addition" shouldn't have large net deletions)
5. If the self-review passes, merge the PR: `gh pr merge --squash`
6. If issues found, fix on the branch, push, and re-review

Direct commits to `main` are OK for: documentation-only changes, single-file bug fixes, test additions, and changes the user explicitly asked to deploy immediately.

### 8. Gate Audit on Lifecycle Changes
When changing how words move between states (encountered → acquiring → FSRS) or adding new flows that alter word states, **audit every gate and filter that operates on those states**. Gates include: comprehensibility gate (×2), unknown scaffold cap, pipeline backlog gate, focus cohort, variant resolution, intro card filter, listening readiness, function word exclusion. The full gate registry is in `docs/scheduling-system.md` §19.17. Lesson learned: the collateral credit change (2026-03-18) broke sessions because the comprehensibility gate wasn't updated for the new box-1 acquiring words it created.

### 9. Code Style
- Python: type hints, pydantic models for API schemas
- TypeScript: strict mode, functional components
- No test plans or checklists in PR descriptions
- Branch prefix: `sh/` for all GitHub branches

### 10. SQLite Write Lock Discipline — Never Hold During Slow Calls
**CRITICAL**: SQLite WAL mode allows only one writer at a time. `db.flush()` and `db.add()`+autoflush acquire the write lock, which is held until `db.commit()` or `db.rollback()`. If an LLM call (5-90s), TTS call, or any network I/O runs between flush and commit, **every other writer in the app blocks for that duration**, causing "database is locked" errors.

**Required pattern** for any function that does both DB writes and slow external calls:
```
Phase 1: Read — query DB, collect data, close/commit session
Phase 2: Slow work — LLM calls, TTS, network I/O (no DB session dirty)
Phase 3: Write — open/reuse session, write results, commit (milliseconds)
```

**Checklist when writing new code:**
- `db.flush()` must NEVER be followed by an LLM/network call before `db.commit()`
- Functions receiving a `db` parameter must not make LLM calls while the session has dirty state
- Background tasks (`BackgroundTasks.add_task`) must not receive the request's `db` session
- Long-running scripts must commit between steps, not hold one session for the entire run
- Non-critical writes (cache updates, counts) should use try/except with rollback so lock contention doesn't crash read endpoints

**Past incidents**: `store_multi_target_sentence` held write lock 30-60s during LLM verification (broke OCR uploads). `_import_unknown_words` held lock during batch translation. Chat endpoint held session during 15s LLM call. All fixed 2026-03-29.

## Key Backend Files
- `backend/app/models.py` — SQLAlchemy models (see `docs/data-model.md`)
- `backend/app/schemas.py` — Pydantic request/response models
- `backend/app/routers/` — API routes (see `docs/api-reference.md`)
- `backend/app/services/` — All services (see `docs/backend-services.md`)
- `backend/app/services/podcast_service.py` — Podcast service: TTS stitching, completion with word credit, file-based metadata
- `backend/app/services/quran_service.py` — Quran reading: verse selection (backlog-gated SRS), review submission, lazy lemmatization pipeline
- `backend/scripts/` — All scripts (see `docs/scripts-catalog.md`)
- `backend/scripts/generate_story_podcasts.py` — Podcast generation: LLM stories, book-to-podcast, CI episodes
- `backend/scripts/import_quran.py` — Import Quran from risan/quran-json CDN (6236 verses) + initial lemmatization

## Testing
```bash
cd backend && python3 -m pytest
cd frontend && npm test
```

### Simulation Framework
End-to-end simulation of multi-day learning journeys:
```bash
python3 scripts/simulate_sessions.py --days 30 --profile beginner
```
Profiles: `beginner` (55%), `strong` (85%), `casual` (70%), `intensive` (75%), `calibrated` (80%, from production data). Code: `backend/app/simulation/`.

## Deployment
```bash
# Deploy backend + pull latest
ssh alif "cd /opt/alif && git pull && docker compose up -d --build"

# Expo dev server is a systemd service
ssh alif "systemctl restart alif-expo"

# Expo URL (always display after deploy):
# exp://alifstian.duckdns.org:8081
# Web: http://alifstian.duckdns.org:8081
```

## Server Operations — MUST READ
See `.claude/skills/server-ops.md` for full details. Summary of hard-won rules:

1. **ALL `ssh` commands require `dangerouslyDisableSandbox: true`** — SSH is always blocked by local sandbox. Never try without it.
2. **NEVER write inline Python in `docker exec python3 -c`** — Triple-nested quoting fails ~50% of the time. For any Python > 2 lines, write to `/tmp/claude/script.py`, then `scp` + `docker cp` + run.
3. **Read `backend/app/models.py` BEFORE writing DB queries** — Don't guess table/column names. They've caused repeated failures (e.g., `lemma` vs `lemmas`, `query()` vs `get()`).
4. **Check `backend/scripts/` before writing ad-hoc queries** — Existing scripts cover most analytics and maintenance tasks.
5. **One deploy per session** — Get code right locally (tests pass), then deploy once. Multiple deploys waste time and risk inconsistent state.
6. **Push before deploy** — `git push` BEFORE running deploy commands. The deploy does `git pull` on the server — if you haven't pushed, the server pulls stale code.

Next: more story imports, listening mode improvements
