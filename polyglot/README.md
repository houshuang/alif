# Polyglot — Reading-Comprehension SRS

Sister app to [Alif](../backend). Modern Greek (primary), Ancient Greek, and Latin
in one backend; reading-as-mapping as the primary intake flow.

## Why this exists separately from Alif

The Arabic app's data model carries Arabic-specific machinery (roots, awzān,
tashkeel, Quranic verses, clitic stripping, CAMeL Tools) that doesn't transfer
to Indo-European languages. Building those languages in the same codebase
would either pollute Alif's schema or require a from-scratch abstraction
designed against only one working example. The fork-then-converge plan:

1. **Phase 1 (now)**: Polyglot is a sibling backend with multilingual schema
   from day one. Modern Greek ships first; Ancient Greek and Latin are
   scaffolded but stubbed. No code in `backend/` is touched.
2. **Phase 2 (after ~6 weeks of dogfooding)**: extract `alif_core/` package
   from the algorithms that are demonstrably identical across Alif and
   Polyglot — FSRS scheduling, acquisition Leitner, session building, ULK
   lifecycle. Both backends import from it.

The frontend stays a single Expo app with a language switcher that picks
backend URL.

## Architecture

```
polyglot/
  app/
    main.py
    config.py
    database.py
    models.py                  # multilingual schema
    schemas.py                 # Pydantic request/response
    routers/
      languages.py             # GET /api/languages
      texts.py                 # POST/GET/PATCH /api/texts
    services/
      core/                    # language-agnostic (FSRS, sessions — TODO)
      languages/
        base.py                # NLPProvider protocol + registry
        el.py                  # Modern Greek (GR-NLP-TOOLKIT)
        grc.py                 # Ancient Greek (OdyCy — stub)
        la.py                  # Latin (LatinCy — stub)
      reading_intake.py        # the core import/mark loop
  data/
    polyglot.db
    frequency/                 # SUBTLEX-GR, Perseus, Dickinson Core
  alembic/                     # ready for first autogenerate
  tests/
```

## NLP toolkits per language

| Language       | Toolkit              | Install                                       | Quality                                  |
|----------------|----------------------|-----------------------------------------------|------------------------------------------|
| Modern Greek   | GR-NLP-TOOLKIT (2024)| `pip install gr-nlp-toolkit`                  | BERT-based, SOTA, ~500 MB                |
| Ancient Greek  | OdyCy (2023)         | spaCy + HuggingFace model                     | 94.4% UD-PROIEL, 83.2% Perseus           |
| Latin          | LatinCy (2023)       | spaCy + `la_core_web_*` from HuggingFace      | POS 97.4%, lemma 94.7%                   |

Heavy deps are optional extras (`pip install -e ".[el]"` etc.) so the base
install stays lean. Providers degrade gracefully — the regex tokenizer always
works; lemmatization raises `ProviderUnavailable` until the toolkit is loaded.

## Reading-as-mapping flow

1. **POST `/api/texts`** with `language_code` + `body` (paste or upload).
   - Tokenize via language provider.
   - Lemmatize each unique surface form (in context where the toolkit supports it).
   - Create `Lemma` rows for new lemmas with `source='reading_intake'`.
   - Create `Story` + `StoryWord` rows.
2. **GET `/api/texts/{id}`** returns the text with per-token `is_known` /
   `is_acquiring` / `is_encountered` / `is_new` / `is_oov` flags driven by
   `UserLemmaKnowledge`.
3. **PATCH `/api/texts/{id}/mark`** with `{lemma_id, state}` updates ULK.
   States: `known` (skip from review), `unknown` (enter acquisition queue),
   `encountered` (seen but not claimed), `ignore` (proper names, mistakes).
4. **(Later)** sentence generation for words marked `unknown` reuses the
   `material_generator` pipeline pattern from Alif.
5. **(Later)** FSRS-driven session loop, copied from Alif's `session_builder`.

## Running

```bash
cd polyglot
pip install -e ".[dev]"
pip install -e ".[el]"          # if you want Modern Greek lemmatization
uvicorn app.main:app --port 3001
```

Tests:
```bash
python3 -m pytest               # fast (regex + DB), skips slow toolkit tests
python3 -m pytest -m slow       # requires gr-nlp-toolkit installed
```

## Deployment (later)

Hetzner VM (same host as Alif), separate systemd service `polyglot-backend`
on port 3001, separate SQLite at `/opt/polyglot/data/polyglot.db`. No
overlap with Alif's data, services, or process tree.

## SRS engine (FSRS + Acquisition)

Polyglot's review pipeline mirrors Alif's, stripped of Arabic-specific
machinery. See `app/services/{fsrs_service,acquisition_service,leech_service,canonical_resolution}.py`
and the `/api/reviews/*` endpoints.

**Lifecycle** (per lemma):

```
                              ┌──────────────────┐
                              │  encountered     │ ← daily intro cap hit;
                              │  (no scheduling) │   stays parked
                              └────────┬─────────┘
                                       │ (cap opens on a future day)
                                       ▼
   mark unknown                 ┌────────────┐    Tier 0 first correct
   POST /reviews/introduce ───▶ │ acquiring  │ ─────────┐
                                │  Box 1     │          │
                                │  4h        │          ▼
                                └─────┬──────┘     ┌──────────┐
                                  Good│ (when due) │ learning │
                                      ▼            │  (FSRS)  │
                                ┌────────────┐     └────┬─────┘
                                │ acquiring  │          │ Good × N
                                │  Box 2     │          ▼
                                │  1d        │     ┌──────────┐
                                └─────┬──────┘     │  known   │
                                  Good│            └────┬─────┘
                                      ▼                 │ Again
                                ┌────────────┐          ▼
                                │ acquiring  │     ┌──────────┐
                                │  Box 3     │     │  lapsed  │
                                │  3d        │     └──────────┘
                                └─────┬──────┘
                                      │ Tier 3 graduation
                                      ▼
                                  learning (FSRS)

   (any state with low rolling accuracy) ──▶ suspended (leech)
                                                  │ cooldown elapses
                                                  ▼
                                              acquiring Box 1
```

**Endpoints:**

| Method | Path                       | Purpose                                          |
|--------|----------------------------|--------------------------------------------------|
| POST   | `/api/reviews/introduce`   | Enrol a lemma into acquisition (Box 1)           |
| POST   | `/api/reviews/submit`      | Apply a review (auto-routes acquisition vs FSRS) |
| GET    | `/api/reviews/due`         | Lemmas whose next review is due                  |
| GET    | `/api/reviews/stats`       | Box distribution + due-count                     |

**Tiered graduation** (acquisition → FSRS):

| Tier | Trigger                                                    | Notes                            |
|------|------------------------------------------------------------|----------------------------------|
| 0    | First review is correct (rating ≥ 3, times_seen was 0)     | Instant graduation               |
| 1    | 100% accuracy across ≥ 3 reviews                           | Graduate from any box            |
| 2    | ≥ 80% accuracy across ≥ 4 reviews, currently in Box ≥ 2    | Graduate from Box 2 or 3         |
| 3    | Box 3, ≥ 5 reviews, ≥ 60% accuracy, ≥ 2 distinct UTC days  | Standard path                    |

**Leech management:**

A word becomes a leech when the sliding window over the last 8 reviews
drops below 50% accuracy (requires ≥ 5 reviews to fire). Suspended leeches
have graduated cooldowns (3d → 7d → 14d on repeated suspensions), with a
4× multiplier for low-priority lemmas (`frequency_rank > 5000`). Stats are
preserved across cycles — the word must genuinely improve recent
performance to escape.

**Daily intro cap:** 30 net-new acquisitions per UTC day. Under acquisition
overload (Box 1/2 debt), recovery-mode reduces this to 0 / 4 / 8 based on
same-day review practice and accuracy. `leech_reintro` bypasses the cap.

## What's built (end of session 2026-05-19)

- **PDF intake** — multi-page Greek textbook (`Istoria tou Archaiou Kosmou`,
  298 pages) imports in <1s. Pages tokenize lazily on first view.
- **Lemmatization** — `simplemma` for Modern Greek + Latin. Pure-Python, no
  ML deps required for the common path. `gr-nlp-toolkit` available for
  richer POS/morphology when needed; loads lazily.
- **LLM quality gate** — `lemma_quality.py`. After simplemma assigns lemmas,
  Claude verifies each non-trivial mapping in sentence context. On test page
  11 of the textbook, made 10 real corrections (χώρα/χωρώ homograph,
  Τίγρης proper-noun recognition, adj/noun POS disambiguation,
  bibliographic citation parsing). Gated by `POLYGLOT_QUALITY_GATE=1`.
- **Tiny gloss on demand** — `lemma_gloss.py`. When user marks a word unknown,
  a short English gloss is fetched (Haiku, ~2s). Other words stay glossless
  until the user cares.
- **Modern↔Ancient cognate linking** — bare-form match auto-links, propagation
  marks the cognate as `encountered` (not `known`, due to semantic drift).
- **External L1 cognates** — `cognate_detector.py`. Opt-in via
  `POLYGLOT_DETECT_COGNATES=1`. Detects transparent cognates between Greek
  lemmas and the user's L1s (English/Norwegian/German/French/Italian/Spanish).
- **Bulk-mark remaining** — `POST /api/texts/{sid}/pages/{n}/mark_remaining`.
  Next-page presumes user knew everything they didn't tap. Function words
  excluded.
- **Stats endpoint** — `GET /api/stats?language_code=el`. One round-trip
  payload covering knowledge breakdown by state, Leitner box distribution,
  FSRS stability histogram, today's activity (reviews, pages read, new
  lemmas, graduated, streak), last-14-day activity, frequency-rank coverage
  bands (when a frequency list is loaded), enriched story progress, and the
  most recent `ActivityLog` entries.
- **Expo frontend** — `frontend/app/polyglot.tsx`. Tap-to-lookup with a
  single-line bottom bar (preserves reading flow), four mark actions
  (known/unknown/encountered/ignore), next-page button that triggers
  bulk-mark.
- **Language switcher** — Globe tab in the Expo tab bar. `LanguageContext`
  persists the active language via AsyncStorage; tab visibility flips
  between Arabic-mode and Modern-Greek-mode based on selection.
- **Stats screen** — `polyglot-stats.tsx`. Mirrors Alif's Today / Vocabulary
  / Activity layout: today's hero tiles, lifecycle funnel
  (Seen → Acq → Learn → Known), Leitner boxes, FSRS stability bar, frequency
  core bands, 14-day activity chart, story progress, and recent activity
  feed. Every section is gated on having data so an early-stage DB renders
  cleanly. Reached as a tab — no in-page back button.

## What's deliberately missing (Phase 2+)

- **Sentence generation** (need to copy/adapt `material_generator` from Alif —
  ~2.5 KLOC with Greek-specific prompt tuning)
- **Session builder** (sentence picking + intro cards interleaving — depends
  on sentence generation landing first)
- **Audio** (Greek TTS — ElevenLabs supports it; decide cost discipline first)
- **Ancient Greek lemmatization** (OdyCy model wiring; stub exists)
- **Latin lemmatization** (LatinCy or stick with simplemma; stub exists)
- **Intro-card working-memory gate** (Alif's `_intro_shown_recently` —
  ports when intro cards land in the UI)
- **Mnemonic generation on failure** (Alif regenerates memory hooks for
  lapsed/struggling words; deferred)

See `CLAUDE.md` (this directory) for the full gates audit comparing what's
ported from Alif vs deferred.
