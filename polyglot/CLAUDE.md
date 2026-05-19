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

## Gates audit — ported from Alif vs deferred

| Alif gate | Polyglot status | File / notes |
|---|---|---|
| Lemma quality verification (sentence-context LLM check) | **Ported** | `app/services/lemma_quality.py` |
| Mapping correction pipeline (`apply_corrections`) | **Ported** | `_apply_verdict()` in lemma_quality.py |
| Verification failure ≠ success | **Ported** | `_call_claude` returns `None` on failure |
| Function-word exclusion | **Ported** | `FUNCTION_WORD_SETS` per language |
| Canonical lemma chain (`canonical_lemma_id`) | **Schema in place, resolver not wired** | Field exists; no `resolve_canonical_via_map` helper yet — add when scheduling lands |
| Cross-language cognate links (`cognate_lemma_id`) | **Ported + extended** | Unique to polyglot — Alif doesn't have this concept |
| External L1 cognates | **Ported** (opt-in) | `cognate_detector.py`; auto-mark gated by env var |
| Gloss-on-demand | **Ported** | `lemma_gloss.py` runs only when user marks unknown |
| `--json-schema` for constrained CLI decoding | **Ported** | Uses `structured_output` field |
| Two-phase commit (NLP work outside DB transaction) | **Ported** | `process_page` does compute-then-write |
| No bare word cards (sentences only) | N/A (no FSRS yet) | will apply when sentence review lands |
| FSRS scheduling | **Deferred** | py-fsrs in pyproject but no service yet |
| Acquisition Leitner 3-box | **Deferred** | schema fields present; no scheduler |
| Session builder | **Deferred** | |
| Intro card filter | **Deferred** | |
| Comprehensibility gate | **Deferred** (only relevant for sentence generation) | |
| Variant chain resolution everywhere | **Partial** | `canonical_lemma_id` field exists; need explicit resolver helper before sentence generation |
| Proper-name handling (filter from review) | **Schema in place** | `Lemma.word_category='proper_name'`; resolver enforces it at filter sites |
| Listening readiness gate | N/A (no TTS yet) | |
| Audio cache by SHA256 | N/A | |
| Daily intro cap | **Deferred** | will apply when scheduling lands |

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
