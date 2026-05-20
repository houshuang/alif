# Polyglot — Design Spec

Polyglot is the second backend in this monorepo: a reading-comprehension SRS
for **Modern Greek (primary)**, **Ancient Greek**, and **Latin**. It is
forked from Alif (Arabic, see `../backend/`) and uses Alif's algorithms as
its design contract: ports of Alif's services back the SRS engine; the
divergences are Greek/Latin-driven and listed explicitly. This document is
the single place to read *why* polyglot is built the way it is.

**Companion docs**

| Doc                  | Purpose                                                                   |
|----------------------|---------------------------------------------------------------------------|
| `README.md`          | Quick orientation — what is this, how to run it                           |
| `DESIGN.md` (this)   | Design decisions, user flows, what's built and what's not, **the why**    |
| `CLAUDE.md`          | Rules for agents editing the code; hard invariants; gates audit            |
| `NEXT_SESSION.md`    | Rolling work-in-flight handoff; cumulative session log                    |

---

## 0. Snapshot (as of 2026-05-20)

- **Backend**: 152 tests passing. Modern Greek end-to-end loop works:
  PDF intake → lazy page processing → mark unknown → enters acquisition →
  sentence review session with intro cards → FSRS scheduling → leech
  detection.
- **Frontend**: 101 tests passing. Reading screen + sentence-review screen
  with intro card interleave (Globe tab switches between Arabic/Greek modes).
- **Languages**: Modern Greek fully wired (simplemma + GR-NLP-TOOLKIT + Claude
  verifier). Ancient Greek + Latin scaffolded with `ProviderUnavailable`
  fallback — OdyCy / LatinCy wire-up deferred.
- **Deployment**: not yet on Hetzner. Local dev only (port 3001). Frontend
  app config (`frontend/app.json` → `polyglotApiUrl`) points at a placeholder.
- **Open scope** (see § 13): cron install of warm-cache script, Haiku
  cost-discipline experiment, Greek TTS, OdyCy+LatinCy wiring, mnemonic
  hooks on failure.

---

## 1. Mission

### 1.1 User

Polyglot is built for a single learner — Stian — who reads Modern Greek at
an intermediate level and wants to read at native pace. The primary
constraint surface is *reading comprehension*: he can sound out almost any
word but stalls on vocabulary in real texts (history, literature, news).

The same engine will later serve Ancient Greek and Latin reading — both
because Stian wants to read Sophocles + Thucydides + Latin without slogging
through grammar drills, and because the architecture is forgiving enough
that pivoting to a new language is mostly a matter of providing a
lemmatizer.

### 1.2 Goal

Make reading authentic Greek (textbook chapters, news articles, novels) the
*core SRS surface*. Every word the user reads earns review credit if it
appears in a reviewed sentence. Unknown words flow into a spaced-repetition
queue that's fed back into the same kind of authentic material.

### 1.3 Success metric

**Genuinely-known words growing week over week.** Inherited from Alif's
north-star metric. "Genuinely known" means an FSRS-graduated lemma with
stability ≥ 1.0 days, not a session-end count.

### 1.4 Anti-goals (what polyglot will NOT do)

- **No production / writing exercises.** Same scope cut as Alif.
- **No gamification or streak coercion.** Authentic reading is the reward.
- **No multi-user, no auth, no accounts.** Single-user app. Polyglot may
  eventually ship to one or two other beta users via the same Hetzner host
  with separate DB files — never a hosted product.
- **No grammar drills as primary content.** Grammar surfaces *only* as
  context when reading sentences that exercise it.
- **No content marketplace.** All texts are pasted/uploaded by the user.

---

## 2. Languages

| Language       | Status       | Lemmatizer                                       | Notes                                                                  |
|----------------|--------------|--------------------------------------------------|------------------------------------------------------------------------|
| Modern Greek   | **Primary**  | `simplemma` (default) + GR-NLP-TOOLKIT (richer) | Quality gate via Claude in sentence context; cognate auto-link to Ancient |
| Ancient Greek  | Scaffolded   | OdyCy (stub) — simplemma fallback                | Bare-form auto-links to Modern when both exist                          |
| Latin          | Scaffolded   | LatinCy (stub) — simplemma fallback              | Independent SRS pipeline; cognate scope TBD                             |

**Why simplemma for the common path.** GR-NLP-TOOLKIT 0.3.0 has no
lemmatizer (only POS/NER/morph/dep). simplemma is pure-Python, ~50ms per
1000 tokens, and good enough for ~90% of forms. The remaining ~10% are
handled by the LLM quality gate. We learned this the hard way (see
`polyglot/CLAUDE.md`).

**Why the LLM quality gate.** simplemma can't distinguish homographs in
context: χώρα (country) vs χωρώ (I fit). It also misclassifies proper nouns
(Τίγρης → τίγρη / "tigress") and gets adj/noun POS wrong on inflected
endings. Claude reads the sentence + simplemma's proposal and either
confirms or proposes a correction.

---

## 3. Two-backend distinction (why polyglot exists separately from Alif)

### 3.1 The decision

Alif's data model is dense with Arabic-specific machinery: roots (ك-ت-ب),
awzān (verb forms), tashkeel (diacritics), Quranic verses, clitic stripping
(و / ال / فا / ها / ك / ي / كم), CAMeL Tools morphology. Building
Indo-European languages into the same codebase would have meant either
polluting Alif's schema with NULL columns or designing an abstraction
against one working example — premature.

The chosen path is **fork-then-converge**:

| Phase   | When                          | What                                                                     |
|---------|-------------------------------|--------------------------------------------------------------------------|
| Phase 1 | Now → ~6 weeks of dogfooding   | Polyglot is a sibling backend. Separate package, venv, SQLite, port.   |
| Phase 2 | After ~6 weeks                 | Extract `alif_core/` with the algorithms demonstrably identical across both backends (FSRS, acquisition, session builder, ULK lifecycle). Both backends import from it. |

The frontend stays a single Expo app with a language switcher that picks
which backend URL to talk to.

### 3.2 The two backends at a glance

|                       | `backend/` (Alif)                         | `polyglot/` (this)                                  |
|-----------------------|-------------------------------------------|-----------------------------------------------------|
| Languages             | Arabic (MSA)                              | Modern Greek, Ancient Greek, Latin                  |
| Primary use case      | FSRS-driven sentence review               | Reading-as-mapping → SRS                            |
| Port (dev)            | 8000                                      | 3001                                                |
| DB file               | `backend/alif.db`                         | `polyglot/polyglot.db`                              |
| Python package        | `alif-backend`                            | `polyglot-backend`                                  |
| Heavy NLP             | CAMeL Tools (Arabic morphology)           | simplemma + GR-NLP-TOOLKIT + Claude verifier        |
| LLM verifier model    | Claude Sonnet/Haiku via CLI                | Claude Sonnet/Haiku via CLI                         |
| Audio                 | ElevenLabs (deployed)                     | Not built yet                                       |
| Hosting               | systemd `alif-backend` (Hetzner, prod)    | Not yet deployed                                    |

`backend/` does not import from `polyglot/` and vice versa. The frontend is
shared. **Do not confuse the two when editing** — `pwd` first, then look at
the function name and the data model.

---

## 4. The Mirror-Alif principle (most important design rule)

This is *the* architectural decision that shapes everything else.

### 4.1 The rule

> Polyglot mirrors Alif's design and code by default. Divergence requires a
> specific Greek/Latin-driven reason, recorded in the change itself.

Alif is the product of 100+ days of real-user iteration. Every button
label, every scheduling constant, every guard, every empty-state copy line
has a history — a bug, a confusion, a feature request, something that
worked after several that didn't. Polyglot is not a fresh design exercise;
it is a port.

### 4.2 What this means concretely

**Mirror by default:**

- **UI / UX**: Before designing or porting any screen, read Alif's
  equivalent TSX (not just the docs). Sentence card layout, two-stage
  reveal, per-word tap semantics, action-row positioning, button labels
  ("Know All" → "Continue" toggling trick), empty-state copy. Real users
  have already filtered Alif's choices.
- **Scheduling / SRS values**: FSRS desired-retention (0.95), Leitner
  intervals (4h / 1d / 3d), daily intro cap (30), tier graduation
  thresholds, leech detection window (8 reviews, <50%), comprehension
  cooldowns (7d / 2d / 4h) — copy verbatim. Polyglot has no Greek-specific
  data yet to justify a refit.
- **Data model + API shape**: Field names, payload structure, idempotency
  keys (`client_review_id`), enum values (`understood` / `partial` /
  `no_idea`). Phase-2 alif_core extraction will be vastly easier if both
  backends already speak the same dialect.
- **Code structure**: File layout, function names, helper boundaries.
  `submit_sentence_review`, `start_acquisition`, `_intro_shown_recently` —
  same names. Future diffing into shared code expects this.

**Defensibly diverge (language-driven):**

- **Cut Arabic-specific machinery**: clitic stripping, awzān, tashkeel
  toggle, Hindawi-tier seed selection, CAMeL Tools morphology, root-pattern
  reasoning, mappings against an Arabic frequency core.
- **Add Greek/Latin-specific needs**: simplemma lemmatization, accent
  restoration on all-caps headings, Modern↔Ancient cognate auto-linking,
  L1 cognate detection (φιλοσοφία → philosophy).
- **Drop fields polyglot's backend doesn't accept** when porting an Alif
  client call. Don't invent stubs.

**Before introducing a feature without an Alif analogue**, search Alif
first. If Alif doesn't have it, ask which case applies:

1. *Alif doesn't need it* (e.g., cognate auto-linking — Arabic has fewer
   transparent cognates). Adding it in polyglot is fine.
2. *Alif hasn't implemented it yet but plans to* (suspect). Mirror Alif's
   absence — don't preempt Alif's eventual design, because when Alif does
   add it, you'll either retrofit polyglot to match or fork the design
   permanently.

### 4.3 Why this matters

The Phase-2 alif_core extraction is the payoff for this discipline. The
more divergent polyglot becomes from Alif, the smaller the shared surface,
and the more we end up maintaining two divergent systems forever — which
is the worst of both worlds.

---

## 5. Architecture

### 5.1 Repository layout

```
alif/
├── backend/                # Alif backend (Arabic)
├── frontend/               # Shared Expo app
├── polyglot/               # THIS BACKEND
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py     # Engine + WAL pragmas + ensure_schema()
│   │   ├── models.py       # Multilingual schema (13 tables)
│   │   ├── schemas.py      # Pydantic request/response
│   │   ├── routers/
│   │   │   ├── languages.py
│   │   │   ├── texts.py
│   │   │   ├── reviews.py        # SRS surface (submit, due, stats, session)
│   │   │   ├── materials.py      # Generate sentences + warm cache
│   │   │   ├── profile.py        # Cognates
│   │   │   └── stats.py
│   │   └── services/
│   │       ├── core/             # (placeholder for alif_core extraction)
│   │       ├── languages/        # NLP per-language providers
│   │       │   ├── base.py       # NLPProvider protocol + registry
│   │       │   ├── el.py         # Modern Greek
│   │       │   ├── grc.py        # Ancient Greek (stub)
│   │       │   └── la.py         # Latin (stub)
│   │       ├── reading_intake.py     # Page processing + mark_lemma
│   │       ├── lemma_quality.py      # LLM verification gate
│   │       ├── lemma_gloss.py        # On-demand gloss
│   │       ├── pdf_extract.py        # PDF → pages
│   │       ├── sentence_harvest.py   # Page → Sentence rows
│   │       ├── sentence_selector.py  # Pick + session builder
│   │       ├── sentence_validator.py # Greek-flavored validation primitives
│   │       ├── sentence_review_service.py  # Write-side per-word credit
│   │       ├── material_generator.py # LLM sentence generation
│   │       ├── canonical_resolution.py # Variant chain resolver
│   │       ├── cognate_detector.py   # External L1 cognates
│   │       ├── fsrs_service.py       # FSRS-6 engine (ported from Alif)
│   │       ├── acquisition_service.py # Leitner 3-box + tiered grad
│   │       ├── leech_service.py      # Sliding-window leech detection
│   │       ├── interaction_logger.py # JSONL daily logs
│   │       └── activity_log.py       # Batch event ActivityLog table
│   ├── deploy/
│   │   └── polyglot-update-material.sh   # Cron wrapper (not yet installed)
│   ├── scripts/
│   │   └── warm_sentence_cache.py
│   ├── tests/
│   ├── data/
│   │   ├── polyglot.db
│   │   ├── frequency/        # SUBTLEX-GR, Perseus, Dickinson Core
│   │   ├── hf_cache/         # Project-local HuggingFace cache
│   │   └── logs/             # Interaction JSONL
│   ├── pyproject.toml
│   ├── README.md
│   ├── DESIGN.md             # ← this file
│   ├── CLAUDE.md
│   └── NEXT_SESSION.md
```

### 5.2 Process / deployment topology

|                | Local dev                                          | Hetzner (planned)                                  |
|----------------|----------------------------------------------------|----------------------------------------------------|
| Backend        | `.venv/bin/uvicorn app.main:app --port 3001`        | systemd unit `polyglot-backend`, port 3001         |
| Database       | `polyglot/polyglot.db` (SQLite, WAL)                | `/opt/polyglot/data/polyglot.db`                   |
| Frontend       | `cd ../frontend && npx expo start --web`            | Existing `alif-expo` systemd service               |
| Cron           | Manual: `scripts/warm_sentence_cache.py`            | `45 */3 * * * /opt/polyglot-update-material.sh`    |

The frontend `app.json` carries both backends' URLs (`apiBaseUrl` for Alif,
`polyglotApiUrl` for polyglot). `frontend/lib/language-context.tsx` decides
which to call based on user's active language.

### 5.3 SQLite discipline

- WAL mode, 30s `busy_timeout`, `synchronous=NORMAL`, `foreign_keys=ON`,
  `cache_size=-64000` (set in `database.py:_set_sqlite_pragmas`).
- **Never hold the write lock across LLM / network calls.** Three-phase
  pattern: read → release → slow work → write. Inherits Alif's hard-won
  rule (the 2026-03-29 incident).
- Schema deltas applied idempotently at startup via
  `database.ensure_schema()`. Alembic skeleton is in place; deltas will
  consolidate into proper revisions when polyglot graduates to multi-user.

### 5.4 LLM backend

Same architecture as Alif — Claude CLI (`claude -p`) is the primary backend
for all batch/background text tasks; free via Max plan.

- **Quality gate**: `POLYGLOT_QG_MODEL=sonnet` (default; ~$0.30-0.50/page)
  or `haiku` (~10× cheaper, untested at scale).
- **Material generation**: `POLYGLOT_GEN_MODEL=sonnet`,
  `POLYGLOT_VERIFY_MODEL=haiku`. One generation + one verification call per
  batch (default `POLYGLOT_BATCH_WORD_SIZE=4`).
- **CLI JSON constraint**: always use `--json-schema` (constrained
  decoding), not `--json-mode`. The CLI puts the structured output in
  `structured_output`, not `result`. Don't regress this — see
  `lemma_quality._call_claude` for the parsing pattern.
- **Verification failure ≠ success**: any LLM timeout / parse error
  returns `None`. We never silently treat a failed call as "all good." On
  total failure inside a generation batch, the entire batch is discarded
  (Hard Invariant #2 mandates the verification gate).

---

## 6. Hard invariants

These are enforced in code and asserted in tests. Each has a history —
either inherited from Alif's incident record or established in polyglot's
own iteration. Numbering matches `CLAUDE.md`.

1. **Lazy page processing.** Pages tokenize + LLM-verify only on first
   view (`GET /api/texts/{sid}/pages/{n}`). Importing a 300-page textbook
   is fast; cost is paid per-page-view.
2. **Quality gate is the lemmatization safety net.** simplemma misclassifies
   homographs; the LLM-in-context gate catches these. Mandatory; never skip
   for "speed."
3. **Bulk-mark presumes content lemmas only.** Function words and
   caps-headings are excluded — they're meta-text, not vocabulary.
4. **Cognate propagation is 'encountered', not 'known'.** Modern↔Ancient
   semantic drift is real (Modern άλογο "horse" ↔ Ancient ἄλογος
   "irrational"). Auto-promoting would silently create errors.
5. **Modern↔Ancient bare-form linking is bidirectional + idempotent.** Runs
   on every Lemma creation. Cheap DB lookup; no LLM call.
6. **External L1 cognate detection is opt-in.** Off by default; gated by
   env vars. Will be turned on after prompt quality is dialed in.
7. **Claude CLI puts structured data in `structured_output`**, not
   `result`, when using `--json-schema`. Don't regress.
8. **Verification failure ≠ success.** Failed LLM calls leave
   `mappings_verified_at` NULL so the next page-view retries.
9. **Canonical lemma is the unit of scheduling.** Every ULK-creation site
   must redirect through `resolve_canonical_lemma_id` at entry — don't
   trust callers. Currently wired at 7 sites + the reviews router. The
   sentence-review service uses `resolve_canonical_via_map` for hot-path
   batch resolution.
10. **Mark-unknown enters the SRS engine.** `mark_lemma(state='unknown')`
    calls `start_acquisition(due_immediately=True)`, landing the lemma in
    Box 1 with the first review due now (subject to the daily intro cap).
11. **Review log idempotency via `client_review_id`.** Offline retries
    observe the post-state from the first call, not a second FSRS step.
    Unique index enforces at DB level.
12. **Intro-card working-memory gate.** `experiment_intro_shown_at` is
    stamped on display; `_intro_shown_recently(ulk, now)` blocks Tier 0 /
    Tier 1 / Tier 2 fast-grad and Box 1→2 advancement for 10 minutes
    (FAST_GRAD_INTRO_GAP). Correct reviews inside the window count for
    exposure but reschedule to Box 1 + 30 min (FAST_INTRO_RETRY_INTERVAL).
    Same field dedups intro cards across sessions; rescue cards observe a
    7-day cooldown on the same field.

### Foundational invariant (separate, but worth restating)

**Every word in every sentence earns review credit.** When a sentence is
reviewed, ALL non-function words get a review (acquisition or FSRS),
regardless of whether they were the "target" or collateral scaffold. This
is the core learning mechanism. A word seen 10 times collaterally with
correct ratings has been learned; the system must recognize this. There is
no privileged "target" review distinct from collateral.

---

## 7. Data model

13 tables. Multilingual from the schema — `Lemma.language_code` is the
primary partition; cross-language refs (Modern↔Ancient cognates) use
`cognate_lemma_id` and explicitly cross language codes.

```
Language (1)──────(*) Lemma (1)──────(*) UserLemmaKnowledge (ULK)
                    │
                    ├──(*)── canonical_lemma_id  → Lemma   (variant chain)
                    ├──(*)── cognate_lemma_id    → Lemma   (cross-language)
                    ├──(*)── FrequencyEntry      (rank info per source)
                    └──(*)── SentenceWord        → Sentence

Story (1)──────(*) Page (1)──────(*) PageWord
                          │
                          └──(*) Sentence    (harvested when page processes)

ReviewLog               (per-lemma review event; idempotent via client_review_id)
SentenceReviewLog       (sentence-level event; one row per submission)
MaterialJob             (background gen tracking)
ActivityLog             (batch + service events)
ContentFlag             (user-reported flags on lemmas/sentences)
UserProfile             (single row; L1 list, etc.)
```

### Key fields

**`Lemma`** — `lemma_id`, `language_code`, `lemma_form` (display, accented),
`lemma_bare` (lowercased, accent-stripped for lookup), `gloss_en`, `pos`,
`canonical_lemma_id` (variant → canonical), `cognate_lemma_id` (cross-
language), `word_category` (NULL / `proper_name` / `function_word`),
`source` (`reading_intake` / `quality_gate` / `manual` / ...).

**`UserLemmaKnowledge`** — one row per *canonical* lemma the user has
interacted with. Drives all scheduling.

  - `knowledge_state`: `new` / `encountered` / `acquiring` / `learning` /
    `known` / `lapsed` / `suspended`.
  - `fsrs_card_json`: py-fsrs v6 card data (state, stability, difficulty,
    due, last_review).
  - `acquisition_box` (1/2/3) + `acquisition_next_due`: Leitner state.
  - `times_seen`, `times_correct`, `total_encounters`,
    `distinct_contexts`: rolling stats.
  - `experiment_intro_shown_at`: working-memory gate (Hard Invariant #12).
  - `leech_suspended_at`, `leech_count`: leech management.

**`Sentence`** — `id`, `language_code`, `text`, `translation_en`, `source`
(`page` / `harvested` / `llm`), `page_id`, `sentence_index_in_page`,
`is_active`, `mappings_verified_at`. Sentences with NULL
`mappings_verified_at` are invisible to the reviewer (Hard Invariant #2).

**`SentenceWord`** — `sentence_id`, `position`, `surface_form`, `lemma_id`
(NULL allowed for caps-headings and unmapped surface forms — see
sentence-eligibility filter in `sentence_selector`).

**`ReviewLog`** — append-only. `lemma_id`, `rating` (1-4), `reviewed_at`,
`response_ms`, `session_id`, `review_mode`, `comprehension_signal`,
`credit_type` (`target` / `collateral`), `was_confused`,
`client_review_id` (unique), `is_acquisition` (bool), `fsrs_log_json`
(pre-state snapshot for undo).

**`SentenceReviewLog`** — one row per `POST /submit-sentence` call. Carries
the comprehension signal, missed/confused lemma id lists, primary lemma,
client_review_id (unique). Used for undo.

---

## 8. Core systems

### 8.0 Page pre-warming (cron — added 2026-05-20)

To avoid the 2-3 min Sonnet wait when the user flips to a fresh page, a
cron task keeps a configurable buffer (default 5) of *already-verified*
pages ahead of the user's last-viewed position. The signal:

- `Page.viewed_at` — stamped by `GET /api/texts/{sid}/pages/{n}` on each
  user view. Cron-warmed pages do NOT stamp it; only real opens do.
- `last_viewed = max(page_number where viewed_at IS NOT NULL)` per story.
- `pages_ahead = count(page_number > last_viewed AND mappings_verified_at IS NOT NULL)`.
- If `pages_ahead < buffer`, the cron processes the next pages forward
  until the buffer fills.

Mechanics live in `reading_intake.warm_pages_ahead(story_id, buffer=5)`
and `warm_all_active_stories(language_code)`. CLI:
`scripts/warm_pages_ahead.py`. Cron entry-point:
`deploy/polyglot-update-material.sh` runs this phase *before* the
sentence-cache warm — newly verified pages are the source of new
lemmas that the sentence cache then needs to cover.

**Cost shape**: bounded by reading speed. The buffer only refills as
the user advances. Per-page Sonnet quality-gate is ~$0.30-0.50. Worst
case per cron pass: 5 pages = ~$2.50. If you read 0 pages, the cron
does 0 work.

**Known limitation**: skipping pages (read page 100 before pages 1-99)
leaves earlier pages lazy — next time you go back, you eat the wait.
Acceptable for sequential reading; revisit if random-access becomes
common.

### 8.1 Reading-as-mapping (the entry flow)

The flow that defines polyglot. Inspired by Tadoku-style extensive reading
where the only correctness signal is *can you read it*.

1. **Upload** (`POST /api/texts/pdf` or `/paste`). PDF: pages extracted via
   `pdf_extract.py`, stored lazily — page text is in `Page.raw_text`, no
   per-token processing until view.
2. **Open page** (`GET /api/texts/{sid}/pages/{n}`).
   - First view: `reading_intake.process_page` runs. Tokenize via language
     provider. simplemma lemmatizes each surface form. LLM quality gate
     verifies non-trivial mappings in sentence context. Function words +
     caps-heading sentences flagged. Sentences harvested via
     `sentence_harvest.py` (grouped by `sentence_index`).
   - Subsequent views: page returns from cache; no recomputation.
3. **Tap a word** (UI). Shows lemma + gloss. Four mark actions:
   - **known** — adds ULK with state=`known`. Skips from review forever.
   - **unknown** — calls `start_acquisition(due_immediately=True)`. Lemma
     enters Box 1 with first review due now (subject to daily cap).
   - **encountered** — adds ULK with state=`encountered`. No scheduling.
     Will get auto-promoted to `acquiring` when seen in a reviewed sentence.
   - **ignore** — marks `word_category='proper_name'`. Filtered from
     selection, scaffold counts, intro cards.
4. **Next page** (UI button). Calls `bulk_mark_remaining_known`: every
   content-lemma surface form not already known/marked is added to ULK as
   `known`. The user only has to flag *unknowns*; everything else is
   presumed known. This is the productivity multiplier of reading-as-mapping
   over flashcard-style intake.

**Why lazy processing.** A 298-page Greek textbook costs ~$8 on Sonnet if
quality-gated all-at-once at import. Lazy per-view means the cost is paid
only for pages actually read.

### 8.2 LLM quality gate (`lemma_quality.py`)

simplemma assigns the best lemma per surface form, but its training data
has no sentence context — homographs are a coin flip. The quality gate
sends each non-trivial sentence + simplemma's proposals to Claude, which
either confirms or proposes corrections.

**What it catches** (validated on textbook page 11):

- **Homograph disambiguation**: χώρα ("country") vs χωρώ ("I fit")
- **Proper noun recognition**: Τίγρης → "Tigris" (keep as proper name) vs
  τίγρη ("tigress")
- **POS confusions**: adj↔noun on inflected endings
- **Citation parsing**: bibliography lines aren't real Greek prose

**Idempotency on partial failure**: if any verification batch returns
`None` (timeout, parse error), `Sentence.mappings_verified_at` stays NULL
so the next page-view retries. Per-word `verified_at` is still stamped on
batches that succeeded — retries only re-send failed batches.

**Heading detection**: lines with ≥80% all-caps + ≤10 tokens get
`quality_note='heading'`. Excluded from review eligibility (Hard
Invariant #3).

**Lemma creation**: unique to polyglot — the gate is *allowed* to create
new `Lemma` rows with `source='quality_gate'`. Source of truth is an
authentic imported text; the correct lemma must be representable. Alif's
"no auto-create lemmas" rule was tuned for LLM-generated content; polyglot
inverts that for imports. Created lemmas immediately flow through the
canonical resolver and (eventually) the enrichment gates.

### 8.3 Canonical resolution (`canonical_resolution.py`)

Variant lemmas (`Lemma.canonical_lemma_id NOT NULL`) must never get their
own ULK row. Schedule on the canonical; track variant exposure inside the
canonical's `distinct_contexts` count.

**Multi-hop, cycle-safe**: `resolve_canonical_lemma_id(db, lemma_id)`
follows the chain. `resolve_canonical_via_map(lemma_id, lemmas_by_id)` is
the hot-path variant — pre-load all lemmas in a sentence map, then
resolve in O(1) per word.

**Wired at 7 ULK-creation sites + the reviews router**:
`start_acquisition`, `submit_review` (FSRS), `submit_acquisition_review`,
`mark_lemma` (every state branch), `propagate_known_via_cognate`,
`_auto_mark_known`, `submit_sentence_review` (via the map variant).

**Discipline**: when adding a new ULK-creation path, redirect at function
entry — don't trust callers. Hard Invariant #9.

### 8.4 Cognate linking

Two distinct mechanisms:

**Intra-Greek (Modern↔Ancient)** — `link_intra_greek_cognates()`. Runs on
every Lemma creation. Bare-form match between `el` and `grc` lemmas
auto-links via `cognate_lemma_id`. Bidirectional, idempotent, cheap (DB
lookup only).

**External (L1 → target)** — `cognate_detector.py`. Off by default. When
`POLYGLOT_DETECT_COGNATES=1`, runs after Lemma enrichment; detects
high-transparency cognates between Greek lemmas and user's L1 list
(English, Norwegian, German, French, Italian, Spanish — pulled from
`UserProfile`). High-confidence detections can auto-mark `known` when
`POLYGLOT_AUTO_MARK_COGNATES=1` is also set.

**Propagation rule** — when Modern φιλία is marked known, Ancient φιλία
becomes `encountered` (NOT `known`). Semantic drift is real (Modern
άλογο "horse" ↔ Ancient ἄλογος "irrational"). Auto-promoting would
silently create wrong "known" states.

### 8.5 FSRS engine (`fsrs_service.py`)

Port of Alif's FSRS-6 wrapper. Uses `py-fsrs` v6.x.

- **Desired retention**: 0.95 (Alif's optimizer fit; refit when polyglot
  has its own data).
- **Same-day reviews**: supported via w17–w19 (FSRS-6 feature).
- **State machine**: `learning` → `known` (stability ≥ 1 day on Good) →
  `lapsed` (Again) → back to `learning`.
- **`submit_review(db, lemma_id, rating_int, ...)`**: applies the FSRS
  step, writes `ReviewLog`, returns the new state + due. Variant-redirect
  at entry. Idempotent by `client_review_id`.

### 8.6 Acquisition Leitner 3-box (`acquisition_service.py`)

Encoding phase before FSRS — handles the "I just learned this word"
period where FSRS's normal intervals are too generous.

- **Boxes**: Box 1 (4h interval) → Box 2 (1d) → Box 3 (3d).
- **Rating semantics**:
  - **Good (3+)**: advance one box, or graduate per tier rule below.
  - **Hard (2)**: stay in same box, refresh timer if due.
  - **Again (1)**: reset to Box 1.
- **Due-date gating**: Box 2+ advancement requires the timer to have
  elapsed. Box 1→2 within the same session is allowed (encoding) unless
  the intro-card working-memory gate fires.

**Tiered graduation** (first match wins):

| Tier | Trigger                                                       |
|------|---------------------------------------------------------------|
| 0    | First review correct (times_seen was 0, rating ≥ 3)            |
| 1    | 100% accuracy across ≥ 3 reviews                              |
| 2    | ≥ 80% accuracy across ≥ 4 reviews, Box ≥ 2                    |
| 3    | Box 3, ≥ 5 reviews, ≥ 60% accuracy, ≥ 2 distinct UTC days     |

Tier 0 / 1 / 2 are blocked by the intro-card working-memory gate (Hard
Invariant #12 — see § 8.10).

### 8.7 Daily intro cap + recovery mode

`DAILY_INTRO_CAP = 30` net-new acquisitions per UTC day. Enforced *inside*
`start_acquisition` so every caller honors it (Alif's hard lesson — the
cap drifted out of central code into one caller and other paths bypassed
it).

**Recovery mode** kicks in when Box 1/2 debt piles up: ≥ 5 unreviewed Box
1 words OR ≥ 30 due Box 2 words. Reduces the effective cap to:
- 0 (no new intros) below 20 reviews/day or accuracy < 80%
- 4 (mid budget) below 60 reviews/day OR accuracy 80–85%
- 8 (full recovery budget) at ≥ 60 reviews/day AND accuracy ≥ 85%

`leech_reintro` source bypasses the cap — re-introducing a leech is not
net-new vocabulary.

**Cap-deferred words** stay `encountered` and bump `total_encounters` on
each appearance. They get promoted to `acquiring` on a future day when
the cap re-opens.

### 8.8 Sentence harvest (`sentence_harvest.py`)

After a page processes, sentences are harvested into the `Sentence` table.
One row per sentence, grouped by `PageWord.sentence_index`. Idempotent via
unique constraint on `(page_id, sentence_index_in_page)`. SentenceWord rows
carry the canonical lemma_id (defense-in-depth canonicalization at storage).

Caps-headings and pure-punctuation artefacts are dropped at harvest time.
This is where the reading flow connects to the review flow — harvested
sentences become candidates for the sentence picker.

### 8.9 Material generation (`material_generator.py`)

LLM sentence generation for due lemmas that don't have enough material.

- **`batch_generate_material(language_code, lemma_ids)`**: takes a list of
  due lemmas, runs one Sonnet generation call producing N sentences per
  target, then one Haiku verification call checking that the generated
  sentences actually contain the target lemma in a comprehensible context.
- **Three-phase pattern**: read (gather lemma + gloss + scaffold data) →
  close session → LLM call → reopen session and write. SQLite write lock
  is never held across LLM calls.
- **Mandatory verification (Hard Invariant #2)**: if verifier returns
  total failure (None), the entire batch is discarded. Per-sentence "wrong"
  verdicts drop just that candidate.
- **Glossless-target check at function entry**: lemmas without
  `gloss_en` are rejected. The user can't review what doesn't have an
  English translation.
- **Function-word tokens** may carry `lemma_id=NULL` (matches
  `sentence_harvest`'s shape).

Defaults: `POLYGLOT_GEN_MODEL=sonnet`, `POLYGLOT_VERIFY_MODEL=haiku`,
`POLYGLOT_BATCH_WORD_SIZE=4`, `POLYGLOT_SENTENCES_PER_TARGET=2`.

### 8.10 Intro-card working-memory gate (Hard Invariant #12)

Ported from Alif (this session, PR #94). The conceptual problem: three
correct answers in 30 seconds after seeing an intro card is **working
memory recall**, not learning. Letting Tier 0 / Tier 1 / Tier 2 fire on
that pattern would silently promote words that haven't been encoded.

**Mechanism**:

- `UserLemmaKnowledge.experiment_intro_shown_at`: nullable timestamp,
  stamped by `POST /api/reviews/experiment-intro-ack` when the frontend
  displays an intro card.
- `_intro_shown_recently(ulk, now)`: returns True while the gap is below
  `FAST_GRAD_INTRO_GAP = 10 min`.
- **Guards** (all inside `submit_acquisition_review`):
  - Tier 0 (first-correct grad): blocked.
  - Box 1→2 advancement: stays in Box 1, reschedules to
    `now + FAST_INTRO_RETRY_INTERVAL` (30 min).
  - Tier 1 (perfect-accuracy grad): blocked.
  - Tier 2 (≥ 80% grad): blocked.
  - Tier 3: not blocked — requires ≥ 2 UTC days, so working-memory
    timing physically can't satisfy it.
- The same field doubles as the dedup key for `_build_intro_cards` (don't
  re-emit) and the **rescue-card cooldown** (≥ 4 reviews, < 50% accuracy
  → re-teach card every `RESCUE_COOLDOWN_DAYS = 7`).

**Intro card emission** (`sentence_selector._build_intro_cards`):

- **New cards**: acquiring lemmas with `times_seen = 0`,
  `experiment_intro_shown_at IS NULL`, `times_correct = 0`, restricted to
  lemmas in the picked sentences.
- **Rescue cards**: acquiring lemmas with `≥ RESCUE_MIN_SEEN = 4` reviews,
  `< RESCUE_MAX_ACCURACY = 50%`, intro not shown in `RESCUE_COOLDOWN_DAYS = 7`.
- Function words + proper names skipped.
- Dynamic cap: base 4, +1 per 15 un-introed acquiring lemmas, max 6
  (`_dynamic_intro_cap`). Total session budget:
  `INTRO_NEW_CARDS_PER_SESSION = 6`.
- Cards ordered by the position of their target sentence in the session,
  so the frontend can splice them in linearly.

**Frontend interleave** (`frontend/lib/polyglot-review-helpers.ts`):

- `buildInterleavedSlots(sentences, introCards, alreadyShownLemmaIds)`:
  emits each intro card before its target sentence, never re-emits across
  reloads, flushes orphans (intros for lemmas not in any sentence) at the
  front. In-session dedup via a Set ref so a prefetch reload doesn't
  re-fire while the ack request is in flight.
- On display: `useEffect` posts `ackExperimentIntro(lemma_id)`. Fire-and-
  forget; transient failure means the card may re-appear next session
  (benign).

### 8.11 Sentence selection (`sentence_selector.py`)

Read-side spine of review. Two functions:

**`pick_sentence_for_lemma(db, lemma_id, language_code, exclude=...)`**:
given a due lemma, returns the best Sentence row covering it, or `None`.

- Variant lemma → canonical at entry.
- Exclude already-used sentences (within-session dedup).
- Three preference tiers:
  1. **Page-first, all-known**: a textbook page sentence whose every
     content scaffold lemma is already `known`/`learning`. 3× score
     bonus, `selection_reason="page_first_all_known"`. Zero-friction —
     the user has already read that page.
  2. **Any harvested sentence**, ranked by
     `(0.3 + 0.7 × comprehensibility) × source_bonus`. Lower-
     comprehensibility sentences still rank above nothing.
  3. **None**: caller defers to generation or skips the lemma.

**`build_session(db, language_code, limit) -> SessionBundle`**: assembles
a session.

- Walks acquisition-due first (Box 1 → 2 → 3, then by due time), then
  FSRS-due (oldest due first).
- For each lemma: call picker; if None, skip; else add sentence to
  selected list and used-set.
- After sentences are picked, `_build_intro_cards` emits intro/rescue
  cards for lemmas in the session.
- Returns `SessionBundle(sentences, intro_cards)`.

### 8.12 Sentence review (`sentence_review_service.py`)

Write-side spine. The Foundational Invariant lives here.

**`submit_sentence_review(db, sentence_id, comprehension_signal, primary_lemma_id, missed_lemma_ids, confused_lemma_ids, ...)`**:

- Pre-load the sentence's lemma map; resolve all variants → canonical at
  entry via `resolve_canonical_via_map`.
- For each content lemma in the sentence (function words + proper names
  skipped):
  - If in `missed_lemma_ids`: rate Again (1).
  - If in `confused_lemma_ids`: rate Hard (2).
  - Else: derive rating from `comprehension_signal` — `understood` → Good
    (3), `partial` → Hard (2) for collateral / Good for target,
    `no_idea` → Again (1).
- Route per-lemma: encountered → `start_acquisition` first; acquiring →
  `submit_acquisition_review`; learning/known/lapsed → `submit_review`
  (FSRS).
- Cap-deferred encountered words bump `total_encounters` without a
  review (the cap discipline shouldn't punish the learner — they did read
  the word).
- Write `SentenceReviewLog` row with the full payload + `client_review_id`
  (unique).
- Each ReviewLog row carries `credit_type = "target" | "collateral"` and
  `was_confused`.

**Idempotency**: `client_review_id` lookup returns `duplicate=True` for
replays.

**Undo** (`undo_sentence_review`): restores pre-state from each ReviewLog's
`fsrs_log_json` snapshot, then deletes the rows. Idempotent.

### 8.13 Leech detection (`leech_service.py`)

A word becomes a leech when its sliding window over the last 8 reviews
drops below 50% accuracy (requires ≥ 5 reviews to fire).

- **Cooldowns**: 3 days → 7 days → 14 days on repeated suspensions.
  Stats preserved across cycles — the word must genuinely improve recent
  performance to escape.
- **Rare-lemma multiplier**: lemmas with `frequency_rank > 5000` get a
  4× cooldown multiplier — these are textbook-tail words; rare leeches
  shouldn't dominate the recovery surface.
- `check_single_word_leech(db, lemma_id)` runs after every review; bulk
  sweep is a separate cron path (deferred).

### 8.14 Logging

Two complementary streams:

**Interaction log** (`interaction_logger.py`): JSONL daily files at
`polyglot/data/logs/interactions_YYYY-MM-DD.jsonl`. Append-only. Events:
`review`, `sentence_review`, `card_shown`, `auto_introduce`,
`experiment_intro_shown`, `word_graduated`, `leech_suspended`. Disabled
when `TESTING=1` (set in conftest).

**Activity log** (`activity_log.py`): `ActivityLog` SQL table. Service-
level events — batch generation runs, leech sweeps, manual data fixes.
Used for the "what's been happening" UI surface.

---

## 9. User flows

### 9.1 First-time setup

1. Open frontend. Globe tab → select "Modern Greek". `LanguageContext`
   persists the choice via AsyncStorage.
2. Tab bar flips to Modern-Greek mode: Reading, Review, Stats.
3. (Optional) Upload a PDF or paste text into Reading.

### 9.2 Reading a textbook chapter (the core daily flow)

1. **Open Reading tab** → list of stories with progress bars.
2. **Tap a story** → open at last-read page.
3. **Page processes lazily** (first view only):
   - simplemma tokenizes + lemmatizes.
   - LLM quality gate verifies non-trivial mappings (~2-3 min on Sonnet).
   - Sentences harvest into the Sentence table.
4. **Page renders**: every word is tappable. Color codes based on ULK
   state: green=known, yellow=encountered, gray=function word, white=new.
5. **Tap unknown words** → bottom bar shows lemma + gloss. Four buttons:
   known / unknown / encountered / ignore.
   - "unknown" enters Box 1 of acquisition; lemma is due immediately.
6. **Tap "Next page"** → backend calls `bulk_mark_remaining_known`:
   every untouched content lemma on the page is marked `known`.
7. Repeat. The user spends time on words that surprised them; everything
   else is presumed known.

### 9.3 Sentence-review session

1. **Open Review tab** → `GET /api/reviews/session?language_code=el`.
2. **Server builds the bundle**:
   - `build_session` walks acquisition-due (Box 1/2/3) then FSRS-due,
     picks one sentence per lemma via the picker.
   - `_build_intro_cards` emits intro/rescue cards for content lemmas in
     the picked sentences that haven't been introduced yet.
   - Returns `{ sentences, intro_cards }`.
3. **Frontend interleaves**: `buildInterleavedSlots` emits each intro card
   immediately before the sentence whose target lemma it covers.
4. **Intro card slot** (when current):
   - Renders `IntroCardView`: lemma form + gloss + POS + optional cognate
     pointer + "rescue" hint if rescue type.
   - `useEffect` posts `ackExperimentIntro(lemma_id)` — stamps
     `experiment_intro_shown_at`, arming the working-memory gate.
   - "Got it — continue" → advance to next slot.
5. **Sentence slot** (when current):
   - **Front**: Greek sentence alone, words tappable. Per-word tap cycles
     off → missed (red) → confused (yellow) → off. Content-lemma filter
     means function words and proper names are tappable for gloss but
     never accumulate marks.
   - Three action buttons: "No idea" (left), middle (toggles "Know All"
     ↔ "Continue" based on whether any marks exist), "Show Translation"
     (right).
   - **Back** (after "Show Translation"): adds the English line under a
     divider. Same three actions, with "Hide Translation" replacing "Show".
6. **Submit** → `POST /api/reviews/submit-sentence`:
   - Comprehension signal: derived from buttons (No idea → no_idea, Know
     All → understood, Continue → partial).
   - Missed + confused lemma_id lists from marks.
   - Server distributes ratings per the rules in § 8.12.
   - Each lemma's response logged; leech check runs per lemma.
7. Advance to next slot. End of session → reload via
   `loadSession()`.

### 9.4 Mark-unknown → SRS pipeline

1. User taps a word in Reading, hits "unknown".
2. `POST /api/texts/.../mark` with `{lemma_id, state: "unknown"}`.
3. `reading_intake.mark_lemma`:
   - Canonical-resolve.
   - Call `start_acquisition(lemma_id, source="reading_intake",
     due_immediately=True)`.
   - Subject to daily cap: if hit, stays `encountered`, gets promoted on
     a future day.
4. Lemma is now Box 1, due immediately. Next Review session picks it.
5. First review:
   - If correct (rating ≥ 3) AND no recent intro → Tier 0 grad to FSRS.
   - If correct AND recent intro → stays Box 1, due in 30 min.
   - If wrong (rating 1) → stays Box 1, retry in 5 min.

### 9.5 Cognate auto-link (Modern → Ancient)

1. User imports a Modern Greek text. New lemma `φιλία` is created.
2. `link_intra_greek_cognates` runs: looks for an Ancient `φιλία` with the
   same `lemma_bare`. If exists, `cognate_lemma_id` is set both ways.
3. Later, user marks Modern `φιλία` as known.
4. `propagate_known_via_cognate` runs: Ancient `φιλία`'s ULK is set to
   `encountered` (NOT `known` — see Hard Invariant #4).
5. When the user opens an Ancient text containing `φιλία`, it's already
   marked as encountered — they know they've seen it in Modern.

### 9.6 Leech detection and reintroduction

1. A word in Box 2/3 keeps getting rated Again or Hard. After 5+ reviews
   with < 50% rolling accuracy:
2. `check_single_word_leech` returns True. Lemma gets
   `knowledge_state="suspended"`, `leech_suspended_at=now`, `leech_count++`.
3. Cooldown: 3d (first time), 7d (second), 14d (third+). 4× multiplier
   for rare lemmas (`frequency_rank > 5000`).
4. After cooldown, `reactivate_if_suspended(lemma, source="leech_reintro")`
   restores to `learning` with a fresh FSRS card. Bypasses the daily intro
   cap (re-intro is not net-new).
5. Frontend can detect a leech-reintroduced lemma and show an indicator
   ("you struggled with this — let's try again").

---

## 10. API surface

### 10.1 Languages

| Method | Path                                  | Purpose                              |
|--------|---------------------------------------|--------------------------------------|
| GET    | `/api/languages`                      | List configured languages + status   |

### 10.2 Texts (Reading)

| Method | Path                                                  | Purpose                                |
|--------|-------------------------------------------------------|----------------------------------------|
| GET    | `/api/texts`                                          | List stories (with progress)           |
| POST   | `/api/texts/paste`                                    | Create story from pasted text          |
| POST   | `/api/texts/pdf`                                      | Create story from uploaded PDF         |
| GET    | `/api/texts/{story_id}`                               | Story metadata                         |
| GET    | `/api/texts/{story_id}/pages/{n}`                     | Page view (triggers lazy processing)   |
| POST   | `/api/texts/{story_id}/pages/{n}/mark_remaining`      | Bulk-mark untouched content words known|
| POST   | `/api/texts/{story_id}/extract-sentences`             | Re-harvest sentences (bulk)            |

### 10.3 Reviews (SRS)

| Method | Path                                       | Purpose                                                   |
|--------|--------------------------------------------|-----------------------------------------------------------|
| POST   | `/api/reviews/introduce`                   | Enrol a lemma into acquisition                            |
| POST   | `/api/reviews/submit`                      | Single-lemma review (auto-routes acquisition vs FSRS)     |
| POST   | `/api/reviews/submit-sentence`             | Sentence review, distributes across content lemmas        |
| POST   | `/api/reviews/undo-sentence`               | Reverse a sentence review by client_review_id             |
| POST   | `/api/reviews/experiment-intro-ack`        | Stamp `experiment_intro_shown_at` (working-memory gate)   |
| GET    | `/api/reviews/due`                         | Lemmas due now                                            |
| GET    | `/api/reviews/next-sentence`               | Pick the best sentence for one lemma                      |
| GET    | `/api/reviews/session`                     | Build a full session (sentences + intro_cards bundle)     |
| GET    | `/api/reviews/stats`                       | Box distribution + due counts                             |

### 10.4 Materials

| Method | Path                                       | Purpose                                                   |
|--------|--------------------------------------------|-----------------------------------------------------------|
| POST   | `/api/materials/generate`                  | Generate sentences for explicit lemma_ids                 |
| POST   | `/api/materials/warm-cache`                | Fill gaps for lemmas in active study below sentence target|

### 10.5 Profile

| Method | Path                                       | Purpose                                                   |
|--------|--------------------------------------------|-----------------------------------------------------------|
| GET    | `/api/profile`                             | User profile (L1 list, etc.)                              |
| GET    | `/api/lemmas/{id}/cognates`                | Cognate links for a lemma                                 |
| POST   | `/api/cognates/detect`                     | Force cognate detection for a lemma set                   |

### 10.6 Stats

| Method | Path                                       | Purpose                                                   |
|--------|--------------------------------------------|-----------------------------------------------------------|
| GET    | `/api/stats?language_code=...`             | One-shot dashboard payload: knowledge breakdown by state, Leitner box distribution, FSRS stability histogram, today (reviews / pages / new lemmas / graduated / streak), last-14-day activity, frequency-rank coverage bands (null when no frequency list loaded), enriched story progress, recent `ActivityLog` entries |

---

## 11. Frontend

### 11.1 Tab structure

Active language drives tab visibility via `frontend/lib/language-context.tsx`.

| Active language | Tabs visible                                          |
|-----------------|-------------------------------------------------------|
| Arabic          | Sentence Review, Reading, Stories, Listening, Stats, … (Alif's full tab set) |
| Modern Greek    | Reading, Review, Stats, Globe                         |

Globe is always present — it's the language switcher.

### 11.2 Reading screen (`frontend/app/polyglot.tsx`)

- List of stories with per-story progress bars.
- Story view: page navigation, per-word tap → bottom-bar gloss + four mark
  buttons.
- "Next page" button calls bulk-mark.
- Single-line bottom bar preserves the reading rhythm — no popups.

### 11.3 Review screen (`frontend/app/polyglot-review.tsx`)

Mirrors Alif's `SentenceReadingCard` + `ReadingActions` design.

- Sentence card: front (Greek alone) → "Show Translation" → back
  (Greek + English under divider).
- Per-word tap: cycles off → missed (red underline) → confused (yellow
  underline) → off. Function words / proper names show gloss but don't
  accumulate marks.
- Three-button action row, middle label toggles "Know All" / "Continue".
- Intro card: rendered when slot type is `intro`. Form + gloss + POS +
  optional cognate pointer. "Got it — continue" advances. Ack posted on
  display.

**Slot-based progression**: the session is a list of `SessionSlot`
(`intro` or `sentence`); `index` walks the slots. `slots.length` is the
displayed total.

### 11.4 Stats screen (`frontend/app/polyglot-stats.tsx`)

Knowledge-state breakdown (encountered / acquiring / learning / known /
lapsed / suspended) with progress bars. Per-story progress. Acquisition box
distribution. Recent activity timeline (from `ActivityLog`).

### 11.5 Language switching

`LanguageContext` persists via AsyncStorage. Tab visibility flips on change.
API client picks `apiBaseUrl` (Alif) or `polyglotApiUrl` based on context.

---

## 12. Gates audit — current state

Source of truth lives in `polyglot/CLAUDE.md`. Summarized here for design-doc completeness; if these conflict, CLAUDE.md wins.

| Gate                                                | Status                    |
|-----------------------------------------------------|---------------------------|
| Lemma quality verification (LLM in sentence context) | **Ported**               |
| Mapping correction pipeline                          | **Ported**               |
| Verification failure ≠ success                       | **Ported**               |
| Function-word exclusion                              | **Ported**               |
| Canonical lemma chain (multi-hop, cycle-safe)        | **Ported** (7 ULK sites + router) |
| Cross-language cognate links                         | **Ported + extended** (unique to polyglot) |
| External L1 cognates                                 | **Ported** (opt-in)      |
| Gloss-on-demand                                      | **Ported**               |
| `--json-schema` for constrained CLI decoding         | **Ported**               |
| Two-phase commit (NLP work outside DB transaction)   | **Ported**               |
| No bare word cards (sentences only)                  | **Ported** end-to-end     |
| Sentence review (per-word credit, collateral)        | **Ported**               |
| FSRS scheduling                                      | **Ported**               |
| Acquisition Leitner 3-box                            | **Ported** (incl. intro-card gate) |
| Session builder                                      | **Ported** (minimal — no recovery budget yet) |
| Intro card filter                                    | **Ported** (new + rescue, dedup, dynamic cap) |
| Comprehensibility gate                               | **Ported** (picker-side) |
| Material generation (LLM sentences)                  | **Ported**               |
| Warm sentence cache                                  | **Ported** (cron not yet installed) |
| Variant chain resolution                             | **Ported**               |
| Proper-name handling (filter from review)            | **Schema in place** (enforcement at picker pending) |
| Leech auto-management                                | **Ported**               |
| Listening readiness gate                             | N/A (no TTS yet)          |
| Audio cache by SHA256                                | N/A                       |
| Daily intro cap (incl. recovery mode)                | **Ported**               |
| Review log idempotency                               | **Ported**               |
| Interaction log JSONL                                | **Ported**               |
| Activity log (ActivityLog table)                     | **Ported**               |

---

## 13. Known missing pieces / next priorities

Lifted from `polyglot/NEXT_SESSION.md` § "What's next" (which is the
canonical to-do source). Priorities are not in order — pick based on what
surfaces in dogfooding.

### 13.1 Operational
- **Cron install of `polyglot-update-material.sh`** on Hetzner. Script
  exists in `polyglot/deploy/`; not yet `scp`'d. 45-min offset from Alif's
  cron (30 */3 → 45 */3) so they don't hit Claude CLI at the same minute.
- **Deploy polyglot-backend systemd unit**. Service file pattern mirrors
  `alif-backend`. Port 3001. Frontend's `app.json` already has
  `polyglotApiUrl` placeholder.

### 13.2 Quality
- **Haiku cost-discipline experiment** for the quality gate. Sonnet runs
  ~$0.30-0.50/page; Haiku is 10× cheaper. Set `POLYGLOT_QG_MODEL=haiku`
  on a held-out page set and measure homograph accuracy.
- **All-caps Greek headings** — currently flagged as headings and excluded.
  Could instead restore accents pre-lookup (frequency-list match) or pass
  sentence-case to the verifier. Probably not worth doing until it bites.

### 13.3 Language coverage
- **Ancient Greek**: OdyCy wire-up. Stub exists at
  `app/services/languages/grc.py`. Smoke-test on `Antigoni-Thucydides
  Epitafios` PDF in Dropbox/Greek/Textbooks.
- **Latin**: LatinCy wire-up. Stub at `la.py`. Smoke-test on the Greek
  high-schooler Latin notes PDF in the same folder.

### 13.4 Affordances
- **Greek TTS via ElevenLabs**. Same `eleven_multilingual_v2` model Alif
  uses. Different voice ID. Cost discipline like Alif: only generate for
  sentences that will be shown.
- **Listening mode** — port Alif's after audio works.
- **Mnemonic generation on failure** — Alif regenerates memory hooks for
  lapsed/struggling words. Deferred.

### 13.5 Architecture
- **Phase-2 alif_core extraction** — when both backends have been
  dogfooded enough to confirm the algorithms are stable. Trigger condition:
  ~6 weeks of polyglot daily use with no Greek-driven divergence in FSRS,
  acquisition, or session-builder constants.

---

## 14. Phase-2: the `alif_core` extraction plan

The end-state of the fork-then-converge plan. Both backends will import
shared logic from a new `alif_core/` package; their per-language code
becomes the thin layer on top.

### 14.1 What gets extracted

Strong candidates (algorithms with no Arabic/Greek divergence):

- **FSRS engine**: `fsrs_service.py` is already nearly identical across
  Alif and Polyglot. py-fsrs v6.x wrapper, idempotency, leech reactivate.
- **Acquisition Leitner**: `acquisition_service.py`. Box intervals,
  tiered graduation, intro-card gate, daily cap.
- **Sentence review service**: per-word credit distribution, idempotency,
  undo.
- **Sentence selector** (session builder portion): walk due → pick →
  intro-card emission.
- **Leech service**: sliding-window detection, cooldowns.
- **Canonical resolution**: variant chain resolver.
- **Interaction logging**: shared JSONL schema.

### 14.2 What stays per-backend

- **Lemma quality gate** prompts and call patterns (Arabic uses CAMeL
  morphology hints; Greek uses simplemma proposals).
- **Sentence harvest** specifics (PDF page handling, Arabic line vs Greek
  sentence boundaries).
- **NLP providers**: clitic stripping for Arabic; simplemma/OdyCy/LatinCy
  for polyglot.
- **Material generation prompts**: Arabic prompts reference wazn / root
  family; Greek prompts reference cognates / scaffold.
- **Per-language data models**: Arabic `root_id` / `wazn` fields; Greek
  `cognate_lemma_id`.

### 14.3 Trigger condition

> Extract when the algorithm constants and code paths haven't diverged for
> ~6 weeks of polyglot daily use.

If polyglot needs a different FSRS desired-retention, a different Leitner
interval, or a different intro-card window — that's a signal to defer the
extraction until we understand why. The whole point is to extract from
proof-of-shape, not proof-of-concept.

### 14.4 Migration mechanics (TBD)

- Probably `pip install -e ../alif_core` from both backends.
- New `polyglot/app/services/core/` directory already exists as the
  landing pad — currently empty.
- Tests live in `alif_core/tests/` and run against both database fixtures.

---

## 15. Configuration

### 15.1 Environment variables

| Variable                          | Default     | Purpose                                                         |
|-----------------------------------|-------------|-----------------------------------------------------------------|
| `POLYGLOT_QUALITY_GATE`           | `0`         | Enable LLM quality gate during page processing                  |
| `POLYGLOT_QG_MODEL`               | `sonnet`    | Model used by quality gate (`sonnet` / `haiku`)                  |
| `POLYGLOT_GEN_MODEL`              | `sonnet`    | Model used by material_generator generation step                 |
| `POLYGLOT_VERIFY_MODEL`           | `haiku`     | Model used by material_generator verification step               |
| `POLYGLOT_BATCH_WORD_SIZE`        | `4`         | Target lemmas per generation batch                              |
| `POLYGLOT_SENTENCES_PER_TARGET`   | `2`         | Sentences requested per lemma per batch                          |
| `POLYGLOT_ACTIVE_TARGET`          | `3`         | Min reviewable sentences per active lemma (warm-cache threshold) |
| `POLYGLOT_PAGES_AHEAD_BUFFER`     | `5`         | Verified pages the cron keeps ahead of the user's last view      |
| `POLYGLOT_PAGES_AHEAD_MAX_PER_RUN`| `5`         | Cap on pages warmed per story per cron pass (safety valve)        |
| `POLYGLOT_PAGES_AHEAD_TIMEOUT_SECONDS`| `1200`  | Cron-pass timeout for the page-warm phase                         |
| `POLYGLOT_DETECT_COGNATES`        | `0`         | Enable external L1 cognate detection                            |
| `POLYGLOT_AUTO_MARK_COGNATES`     | `0`         | Auto-mark high-confidence L1 cognates as `known`                |
| `TESTING`                         | `0`         | Disable interaction logger and similar test-aware paths          |
| `HF_HOME`                         | (auto)      | HuggingFace cache root — `polyglot/data/hf_cache/`               |

### 15.2 Tunable constants (in code)

These mirror Alif verbatim per § 4. Change only with a documented reason.

```python
# acquisition_service.py
BOX_INTERVALS = {1: 4h, 2: 1d, 3: 3d}
GRADUATION_MIN_REVIEWS = 5
GRADUATION_MIN_ACCURACY = 0.60
GRADUATION_MIN_CALENDAR_DAYS = 2
FAST_GRAD_INTRO_GAP = 10 min
FAST_INTRO_RETRY_INTERVAL = 30 min
DAILY_INTRO_CAP = 30

# leech_service.py (Alif's constants)
LEECH_WINDOW = 8 reviews
LEECH_THRESHOLD = 0.50
LEECH_COOLDOWNS = [3d, 7d, 14d]
LEECH_RARE_MULTIPLIER = 4
LEECH_RARE_RANK_THRESHOLD = 5000

# sentence_selector.py
INTRO_NEW_CARDS_PER_SESSION = 6
INTRO_CARDS_BASE = 4
INTRO_CARDS_MAX = 6
RESCUE_MIN_SEEN = 4
RESCUE_MAX_ACCURACY = 0.50
RESCUE_COOLDOWN_DAYS = 7
PAGE_FIRST_BONUS = 3.0
DEFAULT_SESSION_LIMIT = 15
```

---

## 16. Glossary

- **ULK** — UserLemmaKnowledge. One row per (user, canonical lemma).
- **Canonical lemma** — the head of a variant chain. Always carries the
  ULK and scheduling state.
- **Variant lemma** — `Lemma.canonical_lemma_id NOT NULL`. Never gets its
  own ULK. Counts as a `distinct_context` on the canonical.
- **Comprehensibility** — fraction of content scaffold lemmas in a
  sentence that are known/learning. Drives sentence-picker scoring.
- **Intro card** — a teaching-card displayed before a lemma's first review,
  or a re-teach for stuck words. Stamps `experiment_intro_shown_at`.
- **Working-memory gate** — the 10-minute window after an intro card during
  which fast-grad paths are blocked.
- **Reading-as-mapping** — the intake mode where the user reads, taps only
  unknowns, and the rest is presumed known.
- **Acquisition phase** — Leitner Box 1/2/3 between `encountered` and
  FSRS `learning`. Encoding before consolidation.
- **Tier 0/1/2/3** — graduation paths from acquisition to FSRS. Tier 0 is
  instant on first-correct; Tier 3 is the standard ≥ 2-day path.
- **Page-first** — the picker's preference for sentences from textbook
  pages the user has already read, when all scaffold lemmas are known.
- **Rescue card** — intro-card variant for stuck words (≥ 4 reviews, < 50%
  accuracy). Re-teaches with a 7-day cooldown.

---

## 17. Open design questions

Things we haven't decided and probably won't until they bite.

1. **Audio cost discipline**. ElevenLabs Greek TTS is straightforward
   (same model as Alif). Cost discipline: only generate audio for
   sentences that will be shown. Open: do we pre-generate for the next
   session at warm-cache time, or strictly on demand?
2. **Ancient Greek FSRS retention**. Alif's 0.95 was fit against Arabic
   data. Greek will get its own fit after enough reviews; Ancient may want
   a different number (lower frequency of exposure → maybe lower retention).
3. **Quality gate per-language model selection**. Sonnet works for Modern
   Greek. Open: does OdyCy + Sonnet for Ancient Greek, or Sonnet alone
   handle the Ancient Greek context? OdyCy adds ~500MB and a model load
   step. Decision: defer until OdyCy wires up.
4. **Multi-user readiness**. Polyglot is single-user; the schema has no
   `user_id`. If ever ships to one or two beta users via the Hetzner host,
   each gets a separate DB file. Open: at what point does a shared schema
   pay off vs the per-user file proliferation? Probably never in current
   scope.
5. **Phase-2 trigger**. § 14.3 says "no Greek-driven divergence for ~6
   weeks." Open: how do we measure that? Probably by Stian noticing he
   hasn't had to change a shared constant.
