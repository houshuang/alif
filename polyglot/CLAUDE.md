# Polyglot — Project Rules

This is the **second backend** in the `alif/` monorepo. It is *not* Alif. It serves Modern Greek (primary), Ancient Greek, and Latin via a separate process, database, and dependency set. Future Claude sessions: please read this file BEFORE editing anything under `polyglot/`.

## The two-backend distinction

| | `backend/` (Alif) | `polyglot/` (this) |
|---|---|---|
| Languages | Arabic (MSA) | Modern Greek, Ancient Greek, Latin |
| Primary use case | FSRS-driven sentence review | Reading-as-mapping (intermediate learner) |
| Port (dev) | 8000 | 3001 |
| Port (prod) | 3000 | not yet deployed |
| DB file | `backend/alif.db` | `polyglot/polyglot.db` |
| systemd service | `alif-backend` | (not yet) |
| Python package | `alif-backend` | `polyglot-backend` |
| Heavy NLP | CAMeL Tools (Arabic morphology) | simplemma + GR-NLP-TOOLKIT + Claude verifier |
| Frontend tab | "Reading" / "Stories" / "Listening" etc. | "Reading" (polyglot.tsx) |

**Same monorepo, totally separate code.** `backend/` does not import from `polyglot/` and vice versa. The frontend (`frontend/`) talks to *both* — the active backend is chosen by the user's language selection (see `frontend/lib/language-context.tsx`).

## When working in polyglot/

- **Always run `polyglot/.venv/bin/python`**, not `backend/.venv/bin/python`. The venvs have incompatible binary deps (Arabic uses arm64 torch, polyglot has its own setup).
- **Don't reach into `backend/`** for "I'll just copy this Alif service." Most of Alif's code carries Arabic-specific assumptions (clitic stripping, awzān, tashkeel, Hindawi corpus). When you need a piece of FSRS / acquisition / session-building, port it deliberately as part of the Phase-2 `alif_core` extraction — don't copy verbatim.
- **Don't apply Arabic invariants to Greek.** The "no auto-create lemmas" rule from Alif's CLAUDE.md was tuned for the bookCorpus + LLM-generated-sentence pipeline. In polyglot, the LLM quality gate is *allowed* to create new Lemma rows (with `source='quality_gate'`) because the source of truth is an authentic imported text and the correct lemma must be representable. See `app/services/lemma_quality.py` — there's a comment block making this explicit.
- **Use `simplemma` for lemmatization, not GR-NLP-TOOLKIT.** GR-NLP-TOOLKIT 0.3.0 does not include a lemmatizer — only POS, NER, morphology, dependency parsing. We learned this the hard way (see commit history). For Modern Greek lemmas: simplemma. For richer POS/morph (when needed): GR-NLP-TOOLKIT.
- **The HuggingFace cache is project-local.** `app/services/languages/el.py` sets `HF_HOME` to `polyglot/data/hf_cache/` *at module import time* — must run before any transformers/torch import. Don't move it out of that file or you'll re-trigger the sandbox-write permission error.

## Hard invariants (polyglot-specific)

These should be preserved across changes:

1. **Lazy page processing.** Pages are tokenized + LLM-verified only when first viewed (`GET /api/texts/{sid}/pages/{n}`). Importing a 300-page textbook must be fast — the cost is paid per-page-view, not upfront. Implemented in `app/services/reading_intake.py:process_page`.

2. **Quality gate is the lemmatization safety net.** simplemma misclassifies homographs (χώρα → χωρώ), proper nouns (Τίγρης → τίγρη), POS confusions (adj↔noun). The LLM-in-context gate (`app/services/lemma_quality.py`) catches these. Enabled by `POLYGLOT_QUALITY_GATE=1`. **Do not skip this for "speed" — Alif spent months retroactively fixing bad mappings; we don't redo that mistake here.** Cost per page on Sonnet: ~$0.30-0.50, ~2-3 minutes (gated by Claude Max plan so it's free to the user). Model is switchable via `POLYGLOT_QG_MODEL=haiku` (~10x cheaper) once homograph quality is validated.

3. **Bulk-mark presumes content lemmas only.** `bulk_mark_remaining_known()` skips lemmas where `word_category='function_word'` OR `lemma_bare` is in `FUNCTION_WORD_SETS`. This list is intentionally conservative; add to it if real-world reading surfaces false positives. Heading sentences (≥80% all-caps tokens, ≤10 words) are marked `quality_note='heading'` by the quality gate and should be excluded from review eligibility — they're meta-text, not vocabulary.

4. **Cognate propagation is 'encountered', not 'known'.** When user marks Modern φιλία as known, the Ancient cognate (via `cognate_lemma_id`) becomes `encountered` (NOT `known`). Semantic drift between Modern↔Ancient Greek is real (Modern άλογο "horse" ↔ Ancient ἄλογος "irrational"). Auto-promoting would create silent errors.

5. **Modern↔Ancient bare-form auto-linking is bidirectional and idempotent.** `link_intra_greek_cognates()` runs on every Lemma creation. It's cheap (DB lookup), no LLM call.

6. **External L1 cognate detection is opt-in.** Gated by `POLYGLOT_DETECT_COGNATES=1` and `POLYGLOT_AUTO_MARK_COGNATES=1`. Off by default until prompt quality is dialed in. When on, marks high-transparency cognates (Greek φιλοσοφία → English "philosophy") as `known`.

7. **`structured_output` field of Claude CLI output, not `result`.** When using `--json-schema`, the Claude CLI puts structured data in a *separate* field. The `result` field is left empty. See `_call_claude` parsing in `lemma_quality.py` — don't regress this.

8. **Verification failure ≠ success.** If the quality gate's Claude call fails (timeout, parse error), the function returns `None` and tokens stay unverified. We never silently treat an LLM failure as "all good." When *any* batch on a page returns `None`, `mappings_verified_at` is left NULL so the next page-view retries. Per-word `verified_at` is still set for tokens whose batch succeeded, so retries only re-send the failed batches.

9. **Canonical lemma is the unit of scheduling.** Variant lemmas (`Lemma.canonical_lemma_id NOT NULL`) must never get their own `UserLemmaKnowledge` row. Every code path that creates a ULK redirects through `canonical_resolution.resolve_canonical_lemma_id(db, lemma_id)` at entry. Currently the redirect lives in `start_acquisition`, `submit_review` (FSRS), `submit_acquisition_review`, `mark_lemma` (every state branch), `propagate_known_via_cognate`, `_auto_mark_known`, the `/api/reviews/submit` + `/api/reviews/introduce` router handlers, and `sentence_review_service.submit_sentence_review` (via `resolve_canonical_via_map` on a pre-loaded sentence-lemma map — hot-path pattern). **When adding a new ULK-creation path, redirect at function entry — don't trust callers.** Regression coverage in `tests/test_canonical_resolution.py` and `tests/test_sentence_review_service.py::test_variant_credit_goes_to_canonical` asserts variant-in → canonical-out on every entry point.

10. **Mark-unknown enters the SRS engine.** `reading_intake.mark_lemma(state='unknown')` calls `start_acquisition(due_immediately=True)`, so a tapped unknown word lands in Leitner Box 1 with the first review due now. Daily intro cap still binds; cap-exceeded marks stay `encountered` and promote on a future day. Don't reintroduce a standalone `unknown` state in this flow — the whole point of the port is that unknown-flagged words actually flow into review.

11. **Review log idempotency via `client_review_id`.** When the frontend retries a submission after offline reconciliation, the second call must observe the post-state from the first, not apply a second FSRS step. The unique index on `ReviewLog.client_review_id` enforces this at the DB level; the service code reads it first and returns `duplicate=True`.

12. **Intro-card working-memory gate is intentionally NOT ported.** Alif blocks fast-promotion within ~10 minutes of an intro card via `experiment_intro_shown_at`, because three correct answers seconds after an intro card is working memory, not learning. Polyglot has no intro cards yet, so the field is absent. When polyglot adds intro cards (likely as part of the sentence-review UI), port the field + `_intro_shown_recently` guard from Alif. Until then, Tier 0/1/2 graduation can fire on tight time scales — acceptable for a small dogfood user, dangerous for a production cohort.

## Gates audit — ported from Alif vs deferred

| Alif gate | Polyglot status | File / notes |
|---|---|---|
| Lemma quality verification (sentence-context LLM check) | **Ported** | `app/services/lemma_quality.py` |
| Mapping correction pipeline (`apply_corrections`) | **Ported** | `_apply_verdict()` in lemma_quality.py |
| Verification failure ≠ success | **Ported** | `_call_claude` returns `None` on failure |
| Function-word exclusion | **Ported** | `FUNCTION_WORD_SETS` per language |
| Canonical lemma chain (`canonical_lemma_id`) | **Ported** | `canonical_resolution.py` exposes `resolve_canonical_lemma_id` (DB-backed, multi-hop, cycle-safe) and `resolve_canonical_via_map` (pre-loaded map, hot path). Redirect at function entry on all 7 ULK-creation sites: `start_acquisition`, `submit_review`, `submit_acquisition_review`, `mark_lemma` (any state), `propagate_known_via_cognate`, `_auto_mark_known`, plus the reviews router for response payloads. `sentence_review_service.submit_sentence_review` resolves via `resolve_canonical_via_map` against a pre-loaded sentence-lemma map (hot-path pattern; sentence_harvest already canonicalised at storage, so this is defense-in-depth for imports that bypassed it). |
| Cross-language cognate links (`cognate_lemma_id`) | **Ported + extended** | Unique to polyglot — Alif doesn't have this concept |
| External L1 cognates | **Ported** (opt-in) | `cognate_detector.py`; auto-mark gated by env var |
| Gloss-on-demand | **Ported** | `lemma_gloss.py` runs only when user marks unknown |
| `--json-schema` for constrained CLI decoding | **Ported** | Uses `structured_output` field |
| Two-phase commit (NLP work outside DB transaction) | **Ported** | `process_page` does compute-then-write |
| No bare word cards (sentences only) | **Partially ported** | Write-side ready: `sentence_review_service.submit_sentence_review` + `POST /api/reviews/submit-sentence`. Read-side (session builder picks sentence over lemma) still pending PR #3. The bare-word `POST /api/reviews/submit` endpoint stays for now as the transitional UX path from PR #86. |
| Sentence review (per-word credit, collateral semantics) | **Ported** | `sentence_review_service.py` — distributes one comprehension signal across every content lemma in the sentence (target + collateral), honouring Hard Invariant FOUNDATIONAL. Function words and proper names skipped. Variant-in → canonical-out at function entry. Cap-deferred encountered words bump `total_encounters` without a review. New tables: `sentence_review_log`; new ReviewLog columns: `credit_type`, `was_confused`. Includes `undo_sentence_review` that restores pre-state from `fsrs_log_json` snapshots. |
| FSRS scheduling | **Ported** | `fsrs_service.py` — py-fsrs v6, desired_retention=0.95 (Alif's optimizer fit; refit when polyglot has its own data). Mnemonic regeneration deferred. |
| Acquisition Leitner 3-box | **Ported** | `acquisition_service.py` — 4h/1d/3d intervals + tiered graduation (Tier 0 first-correct, Tier 1 100%, Tier 2 ≥80%, Tier 3 standard). Intro-card working-memory gate NOT ported (polyglot has no intro cards yet — see note below). |
| Session builder | **Deferred** | reviews submit by lemma_id today; session loop ports later |
| Intro card filter | **Deferred** | |
| Comprehensibility gate | **Deferred** (only relevant for sentence generation) | |
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
