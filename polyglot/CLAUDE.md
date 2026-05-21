# Polyglot — Project Rules

This is the **second backend** in the `alif/` monorepo. It is *not* Alif. It serves Modern Greek (primary), Ancient Greek, and Latin via a separate process, database, and dependency set. Future Claude sessions: please read this file BEFORE editing anything under `polyglot/`.

## The two-backend distinction

| | `backend/` (Alif) | `polyglot/` (this) |
|---|---|---|
| Languages | Arabic (MSA) | Modern Greek, Ancient Greek, Latin |
| Primary use case | FSRS-driven sentence review | Reading-as-mapping (intermediate learner) |
| Port (dev) | 8000 | 3001 |
| Port (prod) | 3000 | 3002 (port 3001 is occupied by an unrelated Next.js service on the shared host) |
| DB file | `backend/alif.db` | `polyglot/polyglot.db` |
| systemd service | `alif-backend` | (not yet) |
| Python package | `alif-backend` | `polyglot-backend` |
| Heavy NLP | CAMeL Tools (Arabic morphology) | simplemma + GR-NLP-TOOLKIT + Claude verifier |
| Frontend tab | "Reading" / "Stories" / "Listening" etc. | "Reading" (polyglot.tsx) |

**Same monorepo, totally separate code.** `backend/` does not import from `polyglot/` and vice versa. The frontend (`frontend/`) talks to *both* — the active backend is chosen by the user's language selection (see `frontend/lib/language-context.tsx`).

## Ground design and code in Alif

**Alif is 100+ days of iteration and daily real-user testing.** Every UI affordance, scheduling constant, gate, review semantic, payload shape, button label, and empty-state copy line in Alif has a history — a bug, a confusion, a feature request, a thing that worked after several that didn't. Polyglot is not a fresh design exercise. It is a port. **Mirror Alif by default. Divergence requires a specific Greek/Latin-driven reason, recorded in the change itself (PR body, CLAUDE.md note, or experiment log).**

Concretely, this applies to:

- **UI / UX.** Before designing or porting any screen, read Alif's equivalent file (the actual TSX, not just the docs). Sentence card layout, two-stage reveal, per-word tap semantics, action-row positioning, button labels (including the toggling label trick — see Alif's "Know All" → "Continue" middle button), tashkeel-toggle analogues, empty-state copy, session-end framing. Do not invent a "cleaner" rating model, a "simpler" partial flow, or a "more modern" gesture. Real users have already filtered Alif's choices.
- **Scheduling / SRS values.** FSRS desired-retention, Leitner intervals (4h / 1d / 3d), daily intro cap (30), tier graduation thresholds, leech detection window (8 reviews, <50%), comprehension-aware cooldowns (7d / 2d / 4h) — copy verbatim. Polyglot doesn't have its own data yet to justify a refit.
- **Data model + API shape.** Field names, payload structure, idempotency keys (`client_review_id`), enum string values (`understood` / `partial` / `no_idea`, not `understood` / `partial` / `none`). Phase-2 `alif_core` extraction will be vastly easier if both backends already speak the same dialect.
- **Code structure.** Where reasonable, mirror file layout, function names, and helper boundaries. `submit_sentence_review`, `start_acquisition`, `_intro_shown_recently` — same names. Future diffing into shared code expects this.

**Specific, defensible reasons to diverge — language-driven, not preference-driven:**

- Arabic-specific machinery doesn't apply: clitic stripping, awzān, tashkeel toggle, Hindawi-tier seed selection, CAMeL Tools morphology, root-pattern reasoning, mappings against an Arabic frequency core. **Cut, don't port.**
- Greek/Latin-specific needs that Alif doesn't have: simplemma lemmatization, accent restoration on all-caps headings, Modern↔Ancient cognate auto-linking, L1 cognate detection (φιλοσοφία → philosophy). **Add, don't try to retrofit into Alif's shape.**
- Endpoints polyglot has intentionally not built yet (no TTS → no `audio_play_count`, no confusion-help service → no `confusion_candidate_lemma_ids`, no intro cards → no `experiment_intro_shown_at`). When porting an Alif client call, **drop the fields the polyglot backend doesn't accept** rather than inventing a stub.

**Before introducing a feature or making a design choice without an Alif analogue**: search Alif first. If Alif doesn't have it, ask which case applies:
1. Alif doesn't need it (e.g. cognate auto-linking — Arabic has fewer transparent cognates). Adding it in polyglot is fine.
2. Alif hasn't implemented it yet but plans to (suspect). Mirror Alif's absence — don't preempt Alif's eventual design, because when Alif does add it, you'll either have to retrofit polyglot to match or fork the design permanently. Neither is what we want.

The Phase-2 `alif_core` extraction (see bottom of this file) is the eventual payoff for this discipline. The more divergent polyglot becomes from Alif, the smaller the shared surface, and the more we end up maintaining two divergent systems forever.

## When working in polyglot/

- **Always run `polyglot/.venv/bin/python`**, not `backend/.venv/bin/python`. The venvs have incompatible binary deps (Arabic uses arm64 torch, polyglot has its own setup).
- **Code-port discipline (complements the design rule above).** When porting an Alif service, read it before you write the polyglot version, then strip Arabic-specific assumptions (clitic stripping, awzān, tashkeel, Hindawi corpus) and keep the rest. Don't copy verbatim — port deliberately, mindful of Phase-2 `alif_core` extraction — but don't reinvent either.
- **Don't apply Arabic invariants to Greek.** The "no auto-create lemmas" rule from Alif's CLAUDE.md was tuned for the bookCorpus + LLM-generated-sentence pipeline. In polyglot, the LLM quality gate is *allowed* to create new Lemma rows (with `source='quality_gate'`) because the source of truth is an authentic imported text and the correct lemma must be representable. See `app/services/lemma_quality.py` — there's a comment block making this explicit.
- **Use `simplemma` for lemmatization, not GR-NLP-TOOLKIT.** GR-NLP-TOOLKIT 0.3.0 does not include a lemmatizer — only POS, NER, morphology, dependency parsing. We learned this the hard way (see commit history). For Modern Greek lemmas: simplemma. For richer POS/morph (when needed): GR-NLP-TOOLKIT.
- **The HuggingFace cache is project-local.** `app/services/languages/el.py` sets `HF_HOME` to `polyglot/data/hf_cache/` *at module import time* — must run before any transformers/torch import. Don't move it out of that file or you'll re-trigger the sandbox-write permission error.
- **New frontend screens must live at `app/polyglot-*.tsx`** (or under `app/polyglot/`). That URL prefix is what makes `frontend/app/_layout.tsx` isolate Greek screens from the Arabic tab bar via `routeLanguage()` in `frontend/lib/language-routes.ts`. A non-prefixed filename will silently be classified as Arabic and fail the `language-context.test.ts` manifest check. See *App shell & language routing* in `docs/frontend-files.md` for the full rule.

## Hard invariants (polyglot-specific)

These should be preserved across changes:

1. **Lazy page processing.** Pages are tokenized + LLM-verified only when first viewed (`GET /api/texts/{sid}/pages/{n}`). Importing a 300-page textbook must be fast — the cost is paid per-page-view, not upfront. Implemented in `app/services/reading_intake.py:process_page`.

   Three LLM passes happen per page, in order: (a) **body-clean** (Haiku, `body_clean.py`) — strips page numbers, headers, footers, bibliographies, footnote definitions; joins soft-hyphen line breaks; detaches footnote-marker digits fused to words. Cleaned prose lands in `page.body_clean` and the tokenizer reads from there. (b) **batch-gloss** (Haiku, `lemma_gloss.ensure_glosses_batch`) — fetches an English gloss for every content lemma on the page in chunks of `POLYGLOT_GLOSS_CHUNK=50`, caches in `Lemma.gloss_en`. Function words and proper names are filtered before the call. Runs synchronously between the tokenization commit and the quality gate so a tap in the reader renders instant English instead of "…". (c) **quality gate** (Sonnet, `lemma_quality.py`) — per-token lemma verification. The body-clean pass runs first because it dictates what the tokenizer sees; sending raw PyMuPDF text to the tokenizer creates phantom lemmas (`σιτηρών1`, `μη`, `χάνημα`, etc.) the user will never want to learn. Gated by `POLYGLOT_BODY_CLEAN=1` / `POLYGLOT_BATCH_GLOSS=1` (both default on); body-clean falls back to raw `body_src` on LLM failure, batch-gloss leaves NULL gloss for any failed chunk and the per-word `ensure_gloss` covers the cache miss when the user actually taps.

2. **Quality gate is the lemmatization safety net.** simplemma misclassifies homographs (χώρα → χωρώ), proper nouns (Τίγρης → τίγρη), POS confusions (adj↔noun). The LLM-in-context gate (`app/services/lemma_quality.py`) catches these. Enabled by `POLYGLOT_QUALITY_GATE=1`. **Do not skip this for "speed" — Alif spent months retroactively fixing bad mappings; we don't redo that mistake here.** Cost per page on Sonnet: ~$0.30-0.50, ~2-3 minutes (gated by Claude Max plan so it's free to the user). Model is switchable via `POLYGLOT_QG_MODEL=haiku` (~10x cheaper) once homograph quality is validated.

3. **Bulk-mark presumes content lemmas only.** `bulk_mark_remaining_known()` skips lemmas where `word_category='function_word'` OR `lemma_bare` is in `FUNCTION_WORD_SETS`. This list is intentionally conservative; add to it if real-world reading surfaces false positives. Heading sentences (≥80% all-caps tokens, ≤10 words) are marked `quality_note='heading'` by the quality gate and should be excluded from review eligibility — they're meta-text, not vocabulary.

4. **Cognate propagation is 'encountered', not 'known'.** When user marks Modern φιλία as known, the Ancient cognate (via `cognate_lemma_id`) becomes `encountered` (NOT `known`). Semantic drift between Modern↔Ancient Greek is real (Modern άλογο "horse" ↔ Ancient ἄλογος "irrational"). Auto-promoting would create silent errors.

5. **Modern↔Ancient bare-form auto-linking is bidirectional and idempotent.** `link_intra_greek_cognates()` runs on every Lemma creation. It's cheap (DB lookup), no LLM call.

6. **External L1 cognate detection is opt-in.** Gated by `POLYGLOT_DETECT_COGNATES=1` and `POLYGLOT_AUTO_MARK_COGNATES=1`; bulk-import scripts can pass `auto_mark=True` / `batch_size=N` directly to `detect_external_cognates` to override the env defaults. `UserProfile.cognate_auto_mark_threshold` accepts `high` / `medium` / `low` / `never`. As-of 2026-05-21 the threshold is `low` (set by the SUBTLEX-GR seed import — see `scripts/import_subtlex_gr.py`). For the `low` tier the LLM reaches for rare medical/botanical/theological loanwords; pair it with `scripts/recheck_low_cognates.py`, which re-judges low-best marks with a tighter "would a multilingual European reader actually recognize this" prompt and drops the rejects' cognate-source ULKs. Without the recheck pass, expect ~50% false positives in the `low` tier. The detector's `--json-schema` MUST be `type: "object"` at the top level (Anthropic API constraint); regression covered in `tests/test_cognates.py::test_external_cognate_parser_rejects_bare_array`.

7. **`structured_output` field of Claude CLI output, not `result`.** When using `--json-schema`, the Claude CLI puts structured data in a *separate* field. The `result` field is left empty. See `_call_claude` parsing in `lemma_quality.py` — don't regress this.

8. **Verification failure ≠ success.** If the quality gate's Claude call fails (timeout, parse error), the function returns `None` and tokens stay unverified. We never silently treat an LLM failure as "all good." When *any* batch on a page returns `None`, `mappings_verified_at` is left NULL so the next page-view retries. Per-word `verified_at` is still set for tokens whose batch succeeded, so retries only re-send the failed batches.

9. **Canonical lemma is the unit of scheduling.** Variant lemmas (`Lemma.canonical_lemma_id NOT NULL`) must never get their own `UserLemmaKnowledge` row. Every code path that creates a ULK redirects through `canonical_resolution.resolve_canonical_lemma_id(db, lemma_id)` at entry. Currently the redirect lives in `start_acquisition`, `submit_review` (FSRS), `submit_acquisition_review`, `mark_lemma` (every state branch), `propagate_known_via_cognate`, `_auto_mark_known`, the `/api/reviews/submit` + `/api/reviews/introduce` router handlers, and `sentence_review_service.submit_sentence_review` (via `resolve_canonical_via_map` on a pre-loaded sentence-lemma map — hot-path pattern). **When adding a new ULK-creation path, redirect at function entry — don't trust callers.** Regression coverage in `tests/test_canonical_resolution.py` and `tests/test_sentence_review_service.py::test_variant_credit_goes_to_canonical` asserts variant-in → canonical-out on every entry point.

10. **Mark-unknown enters the SRS engine and bypasses the daily intro cap.** `reading_intake.mark_lemma(state='unknown')` calls `start_acquisition(due_immediately=True, source='reading_intake')`, so a tapped unknown word lands in Leitner Box 1 with the first review due now. `reading_intake` is in `acquisition_service.CAP_EXEMPT_SOURCES` alongside `leech_reintro`: a user's "I don't know this" tap is data the system must always honour (the data-capture logic) — separate from how the scheduler then paces practice (the scheduling logic). Recovery thresholds are also looser than Alif's (`RECOVERY_BOX1_UNREVIEWED_LIMIT=50`, `RECOVERY_MIN_REVIEWS_FOR_ANY_INTRO=5`) so other sources don't lock out on day 1. Diverges from Alif Hard Invariant FOUNDATIONAL where the cap binds inside `start_acquisition` itself; in polyglot the dominant intro source is the reading screen, where the user expresses intent explicitly. Don't reintroduce a standalone `unknown` state in this flow — the whole point of the port is that unknown-flagged words actually flow into review. **Production incident 2026-05-20**: prior to this exemption, 23 of the first 28 red taps on Greek textbook page 1 silently downgraded to `encountered` because `RECOVERY_BOX1_UNREVIEWED_LIMIT=5` + `RECOVERY_MIN_REVIEWS_FOR_ANY_INTRO=20` is unreachable on day 1.

11. **Review log idempotency via `client_review_id`.** When the frontend retries a submission after offline reconciliation, the second call must observe the post-state from the first, not apply a second FSRS step. The unique index on `ReviewLog.client_review_id` enforces this at the DB level; the service code reads it first and returns `duplicate=True`.

12. **Intro-card working-memory gate is ported.** `UserLemmaKnowledge.experiment_intro_shown_at` is stamped by `POST /api/reviews/experiment-intro-ack` when the frontend displays an intro card. `_intro_shown_recently(ulk, now)` returns True while the gap is below `FAST_GRAD_INTRO_GAP=10min`, in which case `submit_acquisition_review` blocks Tier 0 / Tier 1 / Tier 2 fast-graduation paths AND the Box 1→2 advancement — three correct answers seconds after an intro is working memory, not learning. Correct reviews inside the window count for exposure but keep the word in Box 1 on `FAST_INTRO_RETRY_INTERVAL=30min` instead. The same field doubles as (a) the dedup key for `_build_intro_cards` (don't re-emit), and (b) the rescue-cooldown timestamp (≥4 reviews <50% accuracy → re-teach card, with a `RESCUE_COOLDOWN_DAYS=7` cooldown). Intro cards are emitted by `sentence_selector.build_session` for never-shown acquiring lemmas appearing in the picked sentences; the frontend interleaves them before the target sentence via `buildInterleavedSlots`. Suspended for lemmas with no ULK (no fabricated state).

## Gates audit — ported from Alif vs deferred

| Alif gate | Polyglot status | File / notes |
|---|---|---|
| PDF body cleanup (page numbers, headers, footnotes, bibliographies, soft-hyphens) | **Ported (polyglot-original)** | `app/services/body_clean.py` — Haiku via CLI, verbatim-audit on removed segments (soft-hyphen + footnote-digit + control-char tolerant), persisted as `Page.body_clean`. Alif doesn't have this because Alif imports from cleaned corpora rather than raw PDFs. |
| Lemma quality verification (sentence-context LLM check) | **Ported** | `app/services/lemma_quality.py` |
| Mapping correction pipeline (`apply_corrections`) | **Ported** | `_apply_verdict()` in lemma_quality.py |
| Verification failure ≠ success | **Ported** | `_call_claude` returns `None` on failure |
| Function-word exclusion | **Ported** | `FUNCTION_WORD_SETS` per language |
| Canonical lemma chain (`canonical_lemma_id`) | **Ported** | `canonical_resolution.py` exposes `resolve_canonical_lemma_id` (DB-backed, multi-hop, cycle-safe) and `resolve_canonical_via_map` (pre-loaded map, hot path). Redirect at function entry on all 7 ULK-creation sites: `start_acquisition`, `submit_review`, `submit_acquisition_review`, `mark_lemma` (any state), `propagate_known_via_cognate`, `_auto_mark_known`, plus the reviews router for response payloads. `sentence_review_service.submit_sentence_review` resolves via `resolve_canonical_via_map` against a pre-loaded sentence-lemma map (hot-path pattern; sentence_harvest already canonicalised at storage, so this is defense-in-depth for imports that bypassed it). |
| Cross-language cognate links (`cognate_lemma_id`) | **Ported + extended** | Unique to polyglot — Alif doesn't have this concept |
| External L1 cognates | **Ported + bulk-seeded** | `cognate_detector.py` (env-gated for incremental reading-intake); `scripts/import_subtlex_gr.py` ran 2026-05-21 against top-5000 SUBTLEX-GR lemmas at threshold=`low`, then `scripts/recheck_low_cognates.py` dropped low-tier false-positives. See Hard Invariant 6. |
| Gloss-on-demand (single-lemma fallback) | **Ported** | `lemma_gloss.ensure_gloss` — called from `mark_lemma(state='unknown')` when the batch cache missed. ~3s per word; surfaces a "…" placeholder during the round-trip. |
| Batch gloss cache (every lemma on page) | **Ported (polyglot-original)** | `lemma_gloss.ensure_glosses_batch` — chunked Haiku call at `POLYGLOT_GLOSS_CHUNK=50` per call, filters function words + proper names before the LLM, per-chunk commit (no SQLite write lock across calls). Wired into `process_page` Phase 2b. Alif doesn't have this because Alif's lemmas already arrive with glosses via the corpus pipeline. |
| `--json-schema` for constrained CLI decoding | **Ported** | Uses `structured_output` field |
| Two-phase commit (NLP work outside DB transaction) | **Ported** | `process_page` does compute-then-write |
| No bare word cards (sentences only) | **Ported** | Read- and write-side both wired and now served end-to-end. Read-side: `sentence_selector.pick_sentence_for_lemma` + `sentence_selector.build_session` (PR #3) → `GET /api/reviews/next-sentence` and `GET /api/reviews/session`. Write-side: `sentence_review_service.submit_sentence_review` + `POST /api/reviews/submit-sentence` (PR #2). Frontend: `frontend/app/polyglot-review.tsx` (PR #5) is now a sentence card — two-stage reveal, per-word tap cycling off → missed (red) → confused (yellow), 3-signal comprehension model, middle button label toggles "Know All"↔"Continue" based on marks. Mirrors Alif's `SentenceReadingCard` + `ReadingActions` design verbatim, with Arabic-specific bits cut (tashkeel toggle, transliteration, lookup panel, confusion analysis, intro cards, audio). The bare-word `POST /api/reviews/submit` endpoint remains for ad-hoc lookups but is no longer the primary review surface. |
| Sentence review (per-word credit, collateral semantics) | **Ported** | `sentence_review_service.py` — distributes one comprehension signal across every content lemma in the sentence (target + collateral), honouring Hard Invariant FOUNDATIONAL. Function words and proper names skipped. Variant-in → canonical-out at function entry. Cap-deferred encountered words bump `total_encounters` without a review. New tables: `sentence_review_log`; new ReviewLog columns: `credit_type`, `was_confused`. Includes `undo_sentence_review` that restores pre-state from `fsrs_log_json` snapshots. |
| FSRS scheduling | **Ported** | `fsrs_service.py` — py-fsrs v6, desired_retention=0.95 (Alif's optimizer fit; refit when polyglot has its own data). Mnemonic regeneration deferred. |
| Acquisition Leitner 3-box | **Ported** | `acquisition_service.py` — 4h/1d/3d intervals + tiered graduation (Tier 0 first-correct, Tier 1 100%, Tier 2 ≥80%, Tier 3 standard). Intro-card working-memory gate **ported** — `_intro_shown_recently` blocks Tier 0/1/2 + Box 1→2 advancement within `FAST_GRAD_INTRO_GAP=10min` of `experiment_intro_shown_at`; correct reviews inside the window count for exposure but reschedule via `FAST_INTRO_RETRY_INTERVAL=30min`. |
| Session builder | **Ported (minimal)** | `sentence_selector.build_session` walks acquisition-due (Box 1→2→3) then FSRS-due, picks one sentence per lemma via the picker, dedupes within session, respects `limit`. No intro cards, reintros, passages, or recovery-mode budget yet — those land in follow-up PRs. |
| Intro card filter | **Ported** | `sentence_selector._build_intro_cards` emits "new" cards for acquiring lemmas appearing in the session that have never been reviewed (`times_seen=0`, `experiment_intro_shown_at IS NULL`) and "rescue" cards for stuck lemmas (≥`RESCUE_MIN_SEEN=4` reviews, <`RESCUE_MAX_ACCURACY=50%`, last-shown >`RESCUE_COOLDOWN_DAYS=7` ago). Function words + proper names skipped. Dynamic cap scales with un-introed backlog (base 4, +1 per 15, max 6); `INTRO_NEW_CARDS_PER_SESSION=6` is the total session budget. The frontend (`frontend/lib/polyglot-review-helpers.ts:buildInterleavedSlots`) interleaves them before the target sentence and posts `/api/reviews/experiment-intro-ack` on display. |
| Comprehensibility gate | **Ported (picker-side)** | `sentence_selector._score_candidate` weights candidates by fraction-of-scaffold-known and applies a strong page-first bonus only when ALL content scaffold lemmas are in `known`/`learning` state. Generation-side prompt asks Sonnet to prefer scaffold-rich sentences but doesn't enforce a strict ratio — the read-side picker has the final say. |
| Material generation (LLM sentences for due lemmas) | **Ported** | `material_generator.py` — `batch_generate_material(language_code, lemma_ids)` runs one Sonnet generation call + one Haiku verification call per batch (defaults `POLYGLOT_GEN_MODEL=sonnet`, `POLYGLOT_VERIFY_MODEL=haiku`, batch size 4 via `POLYGLOT_BATCH_WORD_SIZE`). Three-phase read/LLM/write pattern; never holds the SQLite write lock across LLM calls. Mandatory verification gate (Hard Invariant): on total verifier failure the entire batch is discarded; per-sentence "wrong" verdicts drop that candidate. Glossless target check at function entry. Canonical resolution via `resolve_canonical_via_map` at write time. Function-word tokens may have `lemma_id=NULL` (matches `sentence_harvest`). Endpoint: `POST /api/materials/generate`. |
| Warm sentence cache | **Ported** | `material_generator.warm_sentence_cache(language_code, max_lemmas)` finds lemmas in active study (`acquiring`/`learning`/`known`/`lapsed`) with fewer than `POLYGLOT_ACTIVE_TARGET=3` reviewable sentences and fills them in `POLYGLOT_BATCH_WORD_SIZE` chunks. Acquiring lemmas sorted by `acquisition_next_due ASC` so the cache prioritises what the next session will pull. Variant / function-word / proper-name / glossless lemmas filtered out at the gap query. Threading lock prevents concurrent runs from double-spending Claude budget on the same gaps. Endpoint: `POST /api/materials/warm-cache`. CLI: `scripts/warm_sentence_cache.py`. Cron wrapper: `deploy/polyglot-update-material.sh`. |
| Variant chain resolution everywhere | **Ported** | `canonical_resolution.py` — multi-hop resolver with cycle protection. `start_acquisition` and the reviews router redirect at function entry. |
| Proper-name handling (filter from review) | **Schema in place** | `Lemma.word_category='proper_name'`; will be enforced at sentence-selector filter sites when those land |
| Leech auto-management | **Ported** | `leech_service.py` — sliding-window detection (last 8 reviews <50% accuracy), graduated cooldowns (3d/7d/14d), preserved stats across cycles, low-priority multiplier on rare lemmas (rank > 5000). |
| Listening readiness gate | N/A (no TTS yet) | |
| Audio cache by SHA256 | N/A | |
| Daily intro cap | **Ported** | `DAILY_INTRO_CAP=30` net-new acquisitions per UTC day, enforced inside `start_acquisition`. Recovery-mode budget kicks in under Box 1/2 overload (same thresholds as Alif). `leech_reintro` bypasses the cap. |
| Review log idempotency | **Ported** | `ReviewLog.client_review_id` is unique + indexed; submit returns `duplicate=True` for replay. Mirrors Alif's offline-queue contract. |
| Interaction log JSONL | **Ported** | `interaction_logger.py` — daily files at `polyglot/data/logs/interactions_YYYY-MM-DD.jsonl`. Disabled when `TESTING=1`. |
| Activity log (ActivityLog table) | **Ported** | `activity_log.py` — service-level events (batch jobs, leech sweeps). Used by leech_service. |

## Development workflow

```bash
# From polyglot/
.venv/bin/uvicorn app.main:app --port 3001              # run dev server
POLYGLOT_QUALITY_GATE=1 .venv/bin/uvicorn app.main:app --port 3001  # with quality gate
.venv/bin/python -m pytest                              # tests (fast)
.venv/bin/python -m pytest -m slow                      # tests requiring real NLP models
```

## Code style — same as Alif

- Python 3.11+, type hints, pydantic for API schemas, SQLAlchemy 2.x.
- No comments unless WHY is non-obvious (same rule as the root CLAUDE.md).
- Branch prefix `sh/` for any feature branches.
- No emoji unless explicitly requested.

## Phase-2 extraction commitment

The README's "fork-then-converge" plan says: after ~6 weeks of dogfooding polyglot alongside Alif, we extract a shared `alif_core/` package (FSRS, acquisition, session builder, ULK lifecycle). Both backends import from it. Don't extract before — abstracting from one working example is the premature-abstraction trap. **When the time comes**, this CLAUDE.md should be updated to point at `alif_core/` for shared logic.
