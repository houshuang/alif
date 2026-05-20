# Next session — Polyglot continuation

Self-contained briefing for the future Claude session that picks up this work.
Read this FIRST before touching anything under `polyglot/`. Also read
`polyglot/CLAUDE.md` (project rules + gates audit) and the root
`/Users/stian/src/alif/CLAUDE.md` (especially the new "Polyglot" bullet).

## Done in the 2026-05-20 follow-up

- Plugged 5 ULK-creation sites that bypassed `resolve_canonical_lemma_id` —
  the Alif "36 variant ULKs" 2026-05-06 incident in waiting. Sites fixed:
  `fsrs_service.submit_review`, `acquisition_service.submit_acquisition_review`,
  `reading_intake.mark_lemma` (all non-unknown branches), and the two cognate
  paths (`propagate_known_via_cognate`, `_auto_mark_known`). Regression suite
  in `tests/test_canonical_resolution.py` asserts variant-in → canonical-out
  on every entry point.
- Reconciled doc drift: `polyglot/CLAUDE.md` gates table previously said
  "resolver not wired" (was already wired in PR #85). Now accurately lists
  all 7 redirect sites.

Backend tests: **83 passing** (was 78).

## Done in the 2026-05-19 overnight pass

Three back-to-back PRs landed on main (all squash-merged, branches deleted):

- **PR #84** — Quality-gate fixes: caps-heading identity-skip bypass,
  partial-failure idempotency (`mappings_verified_at` stays NULL when any
  batch fails), heading detection (≥80% all-caps + ≤10 tokens), Sonnet/
  Haiku alias routing via `POLYGLOT_QG_MODEL`.
- **PR #85** — FSRS + acquisition + leech engine ported. Files:
  `services/{canonical_resolution,interaction_logger,activity_log,
  fsrs_service,acquisition_service,leech_service}.py` + new `reviews`
  router. New schema columns on `ReviewLog`: `client_review_id` (unique,
  for offline idempotency), `comprehension_signal`. Schema deltas applied
  idempotently by `database.ensure_schema()` on startup.
- **PR #86** — Review tab frontend: transitional bare-word UX over the
  SRS engine. New screen `app/polyglot-review.tsx`, API client gains
  `getDueLemmas`/`submitReview`/`getReviewStats`. Tab gated to Modern
  Greek mode.

End-to-end loop now works without sentence generation: mark unknown →
Box 1 → review (rate Again/Hard/Good/Easy) → graduates via Tier 0/1/2/3
→ enters FSRS. Leech detection runs after every review and on bulk sweep.

Test counts after this overnight pass:
- Polyglot backend: **78 tests passing** (was 35 at start)
- Frontend: **84 tests passing** (unchanged — typecheck covers the new screen)

Files modified at `polyglot/CLAUDE.md` — gates audit table now lists
FSRS/Acquisition/Leech/Variant-resolver/Daily-cap as **Ported**, with
inline notes on the intentional intro-card-gate omission (see Hard
Invariant #12). `polyglot/README.md` now has a full lifecycle diagram +
endpoint table + tier graduation table + leech description.

What's still deferred from this briefing (open scope below):

## Context

Stian has been building Alif (Arabic learning) for 100 days. On 2026-05-19 we
forked the architecture to add Modern Greek (+ later Ancient Greek + Latin).
The new backend lives at `polyglot/` — separate Python package, separate venv,
separate SQLite, FastAPI on port 3001. **Do not confuse `backend/` (Alif) and
`polyglot/` (Greek).** Frontend is shared; the Globe tab in the bottom bar
switches active language.

The MVP reading-as-mapping loop works end-to-end:
- Import the Greek history textbook (298 pages, lazy).
- Tap unknown words while reading; mark known/unknown/encountered/ignore.
- Next-page presumes everything you didn't tap is known.
- LLM quality gate verifies simplemma's lemma assignments in context (catches
  homographs like χώρα/χωρώ).
- Tiny English gloss fetched on-demand when marked unknown.
- Modern↔Ancient Greek cognate auto-linking + propagation.

## What the user wants next

Verbatim from the user's message that prompted this file:

> we need to continue on all the quality issues and sentence review,
> fsrf with all the bells and whistles of alif (leitner, long term etc).

Concrete priorities:

### 1. Port Alif's sentence-review pipeline to polyglot

**Status: SRS engine landed (PR #85). Sentence-side pipeline still open.**

Remaining files to port from `backend/app/services/`:

- `material_generator.py` (~2.5 KLOC) — sentence generation for `unknown`
  lemmas. Will need a Greek-tuned prompt (different sentence rules,
  different difficulty signals from Arabic).
- `sentence_selector.py` (~2.9 KLOC) — pick the next-best sentence for
  review. Honor function-word exclusion, comprehensibility, scaffold count.
- `session_builder.py` — assemble a review session (intro cards +
  sentences + reintro). Replaces the polyglot `GET /api/reviews/due`
  flat list with structured session payloads.
- `sentence_review_service.py` (~574 lines) — apply review results to
  ULK + FSRS + the broader sentence ledger. Required when sentences carry
  multiple lemmas (collateral credit semantics).

When this lands, also flip Hard Invariant #12 in `polyglot/CLAUDE.md`
(intro-card working-memory gate) from "intentionally omitted" to "ported"
by adding `UserLemmaKnowledge.experiment_intro_shown_at` and porting
`_intro_shown_recently` from Alif's acquisition_service. Without it,
Tier 0/1/2 graduation can fire on tight time scales — fine for solo
dogfood, dangerous if polyglot ships to others.

Important: see `polyglot/CLAUDE.md` "Hard invariants" — keep the **quality
gate is mandatory** discipline. When sentence generation lands, every
generated sentence must go through verify-and-correct (the same way Alif's
`generate_material_for_word` requires verification). Don't add a separate
generation path that skips it — that's how Alif accumulated 29 bad mappings
in March (see CLAUDE.md root, "Hard Invariants").

The **canonical lemma scheduling invariant** is fully wired as of the
2026-05-20 follow-up (see Done section above) — all current ULK-creation
sites redirect through `resolve_canonical_lemma_id` at function entry, with
regression tests. **When the sentence-review pipeline lands, any new
ULK-creation sites it introduces must add the same redirect at entry —
don't trust callers** (Hard Invariant #9). Sentence review will additionally
need `resolve_canonical_via_map` for batch session-builder hot paths.

### 2. Quality gate improvements

The current gate (`lemma_quality.py`) catches homographs + POS errors well
but has gaps:

- **All-caps Greek headings.** PDFs typeset headings in caps without accents,
  so "ΠΟΛΙΤΙΣΜΟΙ" doesn't match simplemma's lowercase-accented dictionary.
  The gate currently leaves these as the lowercased-no-accent form. Either:
  (a) restore accents pre-lookup using a frequency-list match, or
  (b) include the original sentence-case in the verify prompt and let
  Claude propose the citation form. Probably (b) — cheaper.
- **All-caps eligibility filtering.** Should we even try to lemmatize Greek
  all-caps headings? They're often section-numbering or chapter titles
  that don't contribute to vocabulary learning. Could mark headings as
  `is_function_word=True` heuristically (e.g. > 80% uppercase + ≤ 10 words
  on the line).
- **Cost discipline.** Sonnet runs ~$0.30-0.50/page. Max plan covers it but
  consider whether Haiku could handle the "verify if simplemma got it
  right" task. Haiku is 10x cheaper and the task isn't super hard.
- **Idempotency on partial failure.** If Claude times out mid-batch,
  the function logs and continues. Currently `mappings_verified_at` still
  gets stamped — should it? Consider not stamping if any batch returned
  None; force a re-verify next time.

### 3. Sentence review for the Modern Greek learner

The user's reading flow lets them mark words as `unknown`. Those should then
flow into a sentence-review queue. Concrete UX needed:

- **New tab** in Modern-Greek mode: "Review" (next to "Reading" and the
  Globe).
- **Session loop**: pull next sentence for due ULKs, show it, accept
  comprehension signal (understood / partial / no_idea), apply FSRS update,
  next.
- **Where sentences come from**: must include the textbook page where the
  lemma was first marked unknown (zero-friction — the user already read
  that page). Then LLM-generated sentences using `unknown` + adjacent
  `acquiring` words.
- **Cognate-rich sentences**: when generating, prefer sentences that mix
  the target word with words the user has marked known via L1 cognate.
  This is unique to polyglot — Alif doesn't have an equivalent because
  Arabic has fewer transparent cognates.

### 4. Ancient Greek + Latin paths

OdyCy (spaCy-based, 94.4% lemmatization on UD-PROIEL) for Ancient Greek.
LatinCy for richer Latin morphology. Both are stubbed in
`app/services/languages/grc.py` and `la.py` with `ProviderUnavailable`
fallback. Install + smoke-test on a real text:

- Ancient Greek: `~/Library/CloudStorage/Dropbox/Greek/Textbooks/22-0244-02_Sofokleous-Antigoni_Thoukydidi-Perikleous-Epitafios_B-Lykeiou_Vivlio-Mathiti.pdf`
  (Sophocles + Thucydides). Or any other AG text in the same folder.
- Latin: `~/Library/CloudStorage/Dropbox/Greek/Textbooks/Λατινικά-Γ-Λυκείου-Σημειώσεις.pdf`
  (Latin notes for Greek high schoolers).

simplemma handles both languages too, so the GR-grade fallback is the same
pattern as Modern Greek.

### 5. Audio (deferred but plan it)

Greek TTS via ElevenLabs `eleven_multilingual_v2` should just work (it's the
same model Alif uses for Arabic). Voice IDs different. Cost discipline like
Alif: only generate audio for sentences that will be shown. Add a
"Listening" mode for Greek after reading + review are solid.

## Deployment status

Polyglot deployed to Hetzner alongside Alif:
- Code: `/opt/alif/polyglot/`
- venv: `/opt/alif/polyglot/.venv/`
- systemd: `polyglot-backend` on port 3001 (managed by `polyglot-backend.service`)
- DB: `/opt/alif/polyglot/polyglot.db`
- Frontend's `app.json` has `polyglotApiUrl` pointing to the server.

Deploy command (mirrors Alif's pattern):
```bash
ssh alif "cd /opt/alif && git pull && cd polyglot && .venv/bin/pip install -e . --no-deps -q && systemctl restart polyglot-backend"
```

## Working knowledge

Helpful context for the next session:

- **simplemma is enough for the common path.** GR-NLP-TOOLKIT downloads ~500
  MB at first use and adds POS/morphology/dep/NER — useful for future
  grammar features but NOT for lemmas (it doesn't have a lemmatizer).
- **HuggingFace cache redirect.** `app/services/languages/el.py` sets
  `HF_HOME` to project-local at module-import time. Critical: must run
  before any transformers import. Don't move it.
- **Claude CLI `--json-schema` puts output in `structured_output`,
  not `result`.** Easy to regress. Look at `_call_claude` in
  `lemma_quality.py` for the parsing pattern.
- **The DB has been wiped many times during this session.** When you start,
  expect either an empty `polyglot.db` or a textbook with page 11
  processed + bulk-marked. Re-importing the PDF is fast (~1s).

## Useful one-liners

```bash
# Run polyglot dev server with quality gate enabled
cd /Users/stian/src/alif/polyglot
POLYGLOT_QUALITY_GATE=1 .venv/bin/uvicorn app.main:app --port 3001

# Run tests
.venv/bin/python -m pytest                    # fast (30 tests)
.venv/bin/python -m pytest -m slow            # slow (real Claude calls)

# Smoke test the full reading flow (no UI)
.venv/bin/python -c "from fastapi.testclient import TestClient; from app.main import app; ..."
# See polyglot/CLAUDE.md "Development workflow" for full examples.

# Reset DB (lose all work; useful when testing schema changes)
rm polyglot.db polyglot.db-shm polyglot.db-wal
```

## Don't get confused

If you find yourself thinking "I'll add this to the backend", check:
- `pwd` — am I in `backend/` or `polyglot/`?
- The function name — is it called from Alif's pipeline or Polyglot's?
- The data model — `Lemma.lemma_ar` is Arabic-only; `Lemma.language_code +
  lemma_form` is polyglot's pattern.

Each backend has its own venv and pyproject.toml. If you `pip install`
something, do it in the polyglot venv. If you edit a service in `backend/`,
that's Arabic-only.

The frontend is shared. Edits to `frontend/app/_layout.tsx` and
`frontend/lib/language-context.tsx` affect both languages. Polyglot-specific
frontend code lives in `frontend/app/polyglot*.tsx` and
`frontend/lib/polyglot-api.ts`.
