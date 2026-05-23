# Next session — Polyglot continuation

Self-contained briefing for the future Claude session that picks up this work.
Read this FIRST before touching anything under `polyglot/`. Also read
`polyglot/CLAUDE.md` (project rules + gates audit) and the root
`/Users/stian/src/alif/CLAUDE.md` (especially the new "Polyglot" bullet).

## Current production status — 2026-05-23

- Production no longer depends on Claude Max. `codex-cli 0.133.0` is installed
  on the VM and Polyglot uses `POLYGLOT_LLM_PROVIDER=codex`,
  `POLYGLOT_CODEX_MODEL=gpt-5.5`, `CODEX_HOME=/opt/alif/.codex`. Token
  material lives in `/opt/alif/.codex` + `/opt/alif/.env`; do not print it.
- All structured LLM calls route through `app/services/llm_cli.py`: body clean,
  glossing, citation repair, quality gate, sentence generation/verification,
  sentence translation, philology enrichment, cognates, and lemma audits.
- Lemma cleanup is applied in production: 5,073 lemmas, 0 ungated lemmas,
  150 function words, 170 proper names, foreign-key check clean. New lemmas are
  citation-audited before study state can attach (`POLYGLOT_LEMMA_REPAIR=1`).
- Material cron runs every 3 hours via `/opt/polyglot-update-material.sh`:
  warm pages, warm sentence cache, translate harvested textbook sentences,
  enrich philology. A bounded Codex smoke pass on 2026-05-23 generated 1
  sentence, translated 1 textbook sentence, and enriched 1 lemma successfully.
- Current coverage after that pass: 231 LLM sentences, 208 harvested textbook
  sentences (198 translated), 137 enriched lemmas. Urgent acquiring lemmas all
  have at least one sentence; 29 due acquiring lemmas are still below the
  3-sentence target, so sentence generation should keep running for catch-up.

## Done in the 2026-05-21 session

- **PR #6 (this session) — intro-card working-memory gate (Hard Invariant #12)**.
  Ported the field + guard from Alif and wired the full end-to-end intro-card
  surface. Schema: `UserLemmaKnowledge.experiment_intro_shown_at` nullable
  DATETIME, added to `_ADDITIVE_COLUMN_DELTAS` for idempotent migration.
  Acquisition: `FAST_GRAD_INTRO_GAP=10min` + `FAST_INTRO_RETRY_INTERVAL=30min`
  constants, `_intro_shown_recently(ulk, now)` helper, and the four guards in
  `submit_acquisition_review` (Tier 0 first-correct, Box 1→2 transition,
  Tier 1 perfect, Tier 2 ≥80%). Correct reviews inside the window count for
  exposure but keep the word in Box 1 on the 30min retry. Session builder:
  `sentence_selector._build_intro_cards` emits "new" + "rescue" cards for
  acquiring lemmas in the picked sentences, ordered by their target
  sentence's position so the frontend can splice linearly. `build_session`
  now returns a `SessionBundle` (sentences + intro_cards) instead of a flat
  list. Endpoint: new `POST /api/reviews/experiment-intro-ack` stamps the
  field and is variant-redirected. Frontend: `polyglot-api.ts` gains
  `IntroCard` + `ReviewSessionBundle` types and `ackExperimentIntro`.
  `polyglot-review-helpers.ts` gains `buildInterleavedSlots(sentences,
  introCards, alreadyShownLemmaIds)` — trimmed port of Alif's
  `buildInterleavedSession`: intro card before its target sentence, no
  re-emission, orphans flushed at the front, in-session dedup via the
  already-shown set ref. `polyglot-review.tsx` now consumes the bundle,
  builds slots, renders `IntroCardView` for intro slots (form + gloss + pos
  + optional cognate + rescue hint), and posts the ack on display via
  `useEffect` keyed on `currentIntro`. Backend tests: **152 passing** (was
  144; +4 acquisition-gate, +3 ack-endpoint, +1 session-skip-already-shown).
  Frontend tests: **101 passing** (was 96; +5 `buildInterleavedSlots`).
  Gates audit + Hard Invariant #12 updated in `polyglot/CLAUDE.md`.

## Done in the 2026-05-20 session (cumulative)

Six PRs landed (squash-merged, branches deleted):

- **PR #5 (this session) — frontend sentence-bearing review UI**.
  `frontend/app/polyglot-review.tsx` rewritten from the transitional bare-word
  card to a sentence card that mirrors Alif's `SentenceReadingCard` +
  `ReadingActions` (`frontend/app/index.tsx`). Two-stage reveal (front →
  "Show Translation" → back), per-word tap cycles off → missed (red) →
  confused (yellow) → off, 3-signal comprehension model, middle action
  button label toggles "Know All" ↔ "Continue" based on whether any words
  are marked. Content-lemma filter for marking (function words and proper
  names show gloss on tap but never accrue missed/confused state, mirroring
  `sentence_review_service`'s backend skip). API client gains
  `getReviewSession`, `submitSentenceReview`, `undoSentenceReview` (calling
  `GET /api/reviews/session` and `POST /api/reviews/submit-sentence`,
  `/undo-sentence`) plus the `SentencePayload` / `WordRender` /
  `SentenceReviewSubmission` / `WordReviewResult` shapes. Helpers extracted
  to `frontend/lib/polyglot-review-helpers.ts` (mark cycle, signal
  derivation, content-word filter, lemma_id extraction) with 12 new Jest
  tests. Cut vs Alif (all language-driven per `polyglot/CLAUDE.md` §
  "Ground design and code in Alif"): tashkeel toggle, transliteration,
  lookup panel with root/etymology/memory hooks, confusion-help fetch,
  intro cards, audio, wrap-up quiz, session-end journey. Frontend tests:
  **96 passing** (was 84). Backend tests unchanged at 144. Gates audit
  "No bare word cards" row updated to reflect end-to-end wiring.

  Codified a new project-level rule in this session: **polyglot mirrors
  Alif's design and code by default; divergence requires a specific
  Greek/Latin-driven reason**. Lives in `polyglot/CLAUDE.md` § "Ground
  design and code in Alif" + the root `CLAUDE.md` Polyglot bullet. Triggered
  by an early misstep in this session where I asked UX questions Alif had
  already answered before reading `index.tsx`.

- **PR #4 — Greek-tuned `material_generator` port**.
  LLM-driven sentence generation closing the loop with the picker. Files:
  `services/material_generator.py` (+`sentence_validator.py` for Greek-flavored
  tokenize / map / validate primitives). One Sonnet generation call + one Haiku
  verification call per batch (defaults: `POLYGLOT_GEN_MODEL=sonnet`,
  `POLYGLOT_VERIFY_MODEL=haiku`, `POLYGLOT_BATCH_WORD_SIZE=4`,
  `POLYGLOT_SENTENCES_PER_TARGET=2`). Three-phase read/LLM/write pattern; SQLite
  write lock never held across LLM calls. Mandatory verification gate (Hard
  Invariant): total verifier failure discards the entire batch; per-sentence
  "wrong" verdict drops that candidate. Glossless-target check at function
  entry. Canonical resolution at write time. Function-word tokens may carry
  `lemma_id=NULL` (matches `sentence_harvest`). `warm_sentence_cache(language,
  max_lemmas)` finds acquiring/learning/known/lapsed lemmas below
  `POLYGLOT_ACTIVE_TARGET=3` reviewable sentences and fills them in
  `BATCH_WORD_SIZE` chunks. Endpoints: `POST /api/materials/generate`,
  `POST /api/materials/warm-cache`. CLI: `scripts/warm_sentence_cache.py`.
  Cron wrapper: `deploy/polyglot-update-material.sh`. Backend tests:
  **144 passing** (was 134). Gates audit gains "Material generation" + "Warm
  sentence cache" rows, both **Ported**.

- **PR #3 — `sentence_selector` + minimal `build_session` port**.
  Read-side spine: `pick_sentence_for_lemma(db, lemma_id, language_code,
  exclude_sentence_ids=None)` returns the best Sentence for a due lemma, or
  `None` if no eligible row exists (defer to generation in PR #4). Source
  preference: (1) page-first when ALL content scaffold lemmas are
  `known`/`learning` (3× score bonus, `selection_reason="page_first_all_known"`),
  (2) any harvested sentence scored by `(0.3 + 0.7 × comprehensibility) ×
  source_bonus`, (3) `None`. Function words and proper names excluded from
  scaffold counting (via `FUNCTION_WORD_SETS[language_code]` AND
  `Lemma.word_category`). Canonical resolution at function entry (Hard
  Invariant #9). Respects `is_active` + `mappings_verified_at` (Hard
  Invariant #2). `build_session` walks acquisition-due (Box 1→2→3) then
  FSRS-due, picks one sentence per lemma, dedupes sentence_id within session,
  caps at `limit`. New endpoints `GET /api/reviews/next-sentence?lemma_id&
  language_code` (returns SentencePayload or null) and `GET /api/reviews/
  session?language_code&limit` (returns list[SentencePayload]). Backend
  tests: **134 passing** (was 113). Gates audit row "No bare word cards"
  flipped from Partial → Ported; "Session builder" and "Comprehensibility
  gate" updated.

Three PRs landed earlier in the session:

- **PR #2 (was #90) — `sentence_review_service` port**. Write-side spine of
  the sentence-review pipeline: one comprehension signal (understood /
  partial / no_idea) distributed across every content lemma in the sentence,
  honouring Hard Invariant FOUNDATIONAL ("every word earns review credit").
  Function words and proper names skipped, suspended skipped, variants
  redirected at entry, encountered/unknown auto-promoted via
  `start_acquisition`, daily-cap deferred path bumps `total_encounters`
  without a review, acquiring vs FSRS routing, post-submit ReviewLog tagging
  with `credit_type` + `was_confused` + `sentence_id`, sentence-level audit
  row in new `sentence_review_log` table, idempotency via composite
  `client_review_id`, undo restores pre-state from `fsrs_log_json`. New
  endpoints `POST /api/reviews/submit-sentence` and `POST /api/reviews/undo-sentence`.
  Schema deltas (additive) on `ReviewLog`: `credit_type`, `was_confused`.
  Backend tests: **113 passing** (was 92 at session start). Gates audit row
  "No bare word cards" moved to **Partially ported** (write-side ready,
  read-side pending PR #3).

Previously landed earlier in the session:

- **PR #88** — Canonical-resolver leak fix. Plugged 5 ULK-creation sites
  that bypassed `resolve_canonical_lemma_id` — the Alif "36 variant ULKs"
  2026-05-06 incident in waiting. Sites fixed: `fsrs_service.submit_review`,
  `acquisition_service.submit_acquisition_review`, `reading_intake.mark_lemma`
  (all non-unknown branches), and the two cognate paths
  (`propagate_known_via_cognate`, `_auto_mark_known`). Regression suite
  asserts variant-in → canonical-out on every entry point.
- **PR #89** — Sentence harvest from textbook pages. New service
  `sentence_harvest.py` walks PageWord rows and emits Sentence +
  SentenceWord rows grouped by `sentence_index`. New schema columns
  `Sentence.page_id` + `sentence_index_in_page` for provenance + idempotency
  (unique constraint). Auto-triggered from `process_page` after quality
  gate; bulk re-harvest via `POST /api/texts/{story_id}/extract-sentences`.
  SentenceWord stores canonical lemma_id (defense-in-depth at storage
  rather than read time). Caps-heading sentences and pure-punctuation
  artefacts are skipped.

Also reconciled doc drift: `polyglot/CLAUDE.md` gates table previously said
"resolver not wired" (was already wired in PR #85). Now accurately lists
all 7 redirect sites.

Backend tests: **92 passing** (was 78 at session start).

## What's next — backend loop is end-to-end; next is depth

PR #5 shipped this session. The user-facing loop is now complete: read
→ tap unknown → review session pulls sentences → tap-cycle marks
missed/confused → comprehension signal → FSRS scheduling. No further
PRs are required to make polyglot usable for daily Greek dogfooding.

Suggested next priorities (no specific PR order; pick by what surfaces
during dogfooding):

- **Intro-card working-memory gate** (Hard Invariant #12). Polyglot's
  Tier 0/1/2 graduation can currently fire on tight time scales because
  there's no `experiment_intro_shown_at` field gating it. Port the field
  + `_intro_shown_recently` guard from Alif's
  `app/services/acquisition_service.py`. Add the experiment-intro card
  to the session builder + a frontend intro-card screen interleaved with
  sentence cards (Alif's `buildInterleavedSession` is the reference,
  `frontend/app/index.tsx:99–198`).
- **Quality-gate Haiku cost-discipline experiment**. Sonnet runs
  ~$0.30-0.50/page; Haiku is 10× cheaper and the verify task is bounded.
  See "Quality gate improvements" below.
- **Ancient Greek + Latin language paths**. OdyCy + LatinCy smoke tests
  on the Sophocles/Thucydides PDF in
  `~/Library/CloudStorage/Dropbox/Greek/Textbooks/`.
- **Greek TTS via ElevenLabs**. Same model Alif uses
  (`eleven_multilingual_v2`). Cost discipline like Alif — only generate
  for sentences that will be shown.
- ~~**Cron deploy of `polyglot-update-material.sh`**~~ — done 2026-05-20.
  Running at `45 */3 * * *` on Hetzner with three phases (warm_pages_ahead
  → warm_sentence_cache → enrich_lemma_philology, the last with
  `--include-failed` so flagged enrichment doesn't linger).

Suggested PR #5 entry points to read first in the new session:
- `frontend/app/polyglot-review.tsx` — current bare-word screen.
- `frontend/lib/polyglot-api.ts` — API client; add `getReviewSession()` +
  `submitSentenceReview()`.
- `backend/.../sentence_selector.SentencePayload` — shape returned from
  `GET /api/reviews/session?language_code=el`.
- `polyglot/app/services/sentence_review_service.submit_sentence_review` —
  endpoint at `POST /api/reviews/submit-sentence`; takes `sentence_id`,
  comprehension, response_ms, client_review_id.

**Operational note (historical):** the cron wrapper at
`polyglot/deploy/polyglot-update-material.sh` is installed on Hetzner
at `45 */3 * * *` (45-minute offset from Alif's `30 */3 * * *` so they
don't hit Claude CLI at the same minute). Update procedure:

```bash
scp polyglot/deploy/polyglot-update-material.sh alif:/opt/polyglot-update-material.sh
ssh alif chmod +x /opt/polyglot-update-material.sh
```
Then restart the backend if the change touches a service the cron calls.

Stale: previously-suggested PR #4 entry points (now done):
- `backend/app/services/material_generator.py` — the generation pipeline
  (~2.5 KLOC). Single sentence and batch paths; the bounded legacy batch
  path (`batch_generate_material` / `generate_material_for_word`) is
  what production uses today.
- Skip Arabic-specifics: tashkeel, clitic stripping in verification,
  awzān-weighted prompts, Hindawi-tier seed selection, the
  `sentence_self_correct.py` tool-enabled session (it's gated off in
  Alif as of 2026-05-12 due to empty-result failures).
- New Greek prompt template: no tashkeel, simplemma to verify the
  generated sentence's lemma assignments, function-word exclusion via
  `FUNCTION_WORD_SETS[language_code]`.
- Mandatory verification gate (Hard Invariant #2): every generated
  sentence must pass `lemma_quality.verify_page_lemmas` (or equivalent
  per-sentence variant) before `mappings_verified_at` is stamped.
  Without it, the picker will reject the row.
- Cron warm: a background job that pre-generates for the top-N due
  lemmas missing material — mirrors Alif's `update_material.py`.

Open scope from the original 2026-05-19 briefing — quality gate
improvements (all-caps Greek headings via sentence-case in verify prompt,
Haiku cost-discipline experiment), Ancient Greek + Latin paths (OdyCy,
LatinCy), audio (Greek TTS via ElevenLabs) — remains untouched but is
lower priority than the sentence-review pipeline per Stian's direction.

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

**Status: SRS engine landed (PR #85). Sentence harvest landed (PR #89).
sentence_review_service landed (PR #2 of the 2026-05-20 sequence).
sentence_selector + minimal build_session landed (PR #3). Material generator +
warm cache landed (PR #4 — this session). Remaining: frontend sentence-review
UI (PR #5). See "What's next" section above for the concrete PR breakdown.**

No further backend services from `backend/app/services/` need straight ports
for the core review loop. Deferred-by-design pieces (intro cards, sentence
rotation/lifecycle, audio, multi-target story sentences) live behind explicit
gates in `polyglot/CLAUDE.md` and will be ported as their use cases emerge.

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
