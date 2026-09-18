# Issue #3 — Rare-word warning + per-word suspend (handoff)

## What the user asked for

When the system introduces a new word that is **outside the top 3000
frequency rank** (or has no frequency data at all), the intro card should
show:

1. A **warning banner** indicating the word is rare, with the actual
   **frequency data** displayed (rank, and raw count if available).
2. A **"Suspend this word" button**. On press:
   - Mark the word's ULK `knowledge_state="suspended"`.
   - **Cascade-cancel all pending sentences with that word**: any active
     Sentence whose `target_lemma_id` equals this lemma (or whose word list
     contains this lemma) should be deactivated so they don't appear in
     future sessions.
   - Skip the current intro card and move to the next item in the session.

The motivation: the user was seeing very rare words (e.g. a plant used as
hair dye — likely حِنَّاء "henna" or similar) introduced from OCR'd
textbook scans, Quran imports, and book imports. Most are legitimate but
some are too obscure to justify entering FSRS rotation. The user wants
agency at the moment of introduction, not after.

This is a **user-facing feature**, not a bug fix. No retroactive cleanup
of already-introduced rare words is needed — just the new gate.

## Settled decisions (already collected from the user)

| Question | Decision |
|----------|----------|
| Threshold | Not in top 3000 — matches `ALIF_FREQ_CORE_INTAKE_MAX_RANK=3000` |
| Warning location | On the intro card itself, before "Continue" |
| Show frequency data? | Yes — rank, and raw count when available |
| Auto-suspend rare words? | No — warning only; user clicks button to suspend |
| Cascade behavior | Suspend ULK + deactivate active sentences with that word |
| Out of scope | Cleanup of historic rare-word intros; bulk audit; settings UI |

## Where to start reading

Don't trust this prompt's references blindly — codebase shifts. Verify
each file exists and the function names match before editing.

### Backend
- `backend/app/models.py` — `FrequencyCoreEntry` model (the top-3000
  curriculum table populated by `build_frequency_core.py`).
- `backend/app/services/word_selector.py` — where new words are picked
  for intro. Likely the right place to also fetch frequency rank.
- `backend/app/services/sentence_selector.py` — `TEXTBOOK_PRESERVE_INTRO_KIND`,
  `_build_reintro_cards`, `select_new_intro_cards` etc. The intro-card
  payload that the frontend renders comes from somewhere in here.
- `backend/app/routers/review.py` (or similar) — where the
  `/api/review/next-sentences` endpoint lives. Cards come back from there
  with `intro_kind`, `lemma_id`, etc.
- `backend/app/schemas.py` — pydantic IntroCard schema needs a new
  `frequency_rank: int | None` field (and maybe `frequency_source_count`).
- `backend/scripts/identify_leeches.py` — has a `--suspend` mode. Cross-
  reference its suspension logic so you don't reinvent the cascade.

### Frontend
- `frontend/app/index.tsx` around line 1995–2035 — the intro card
  rendering path, where `isTextbookPreserveCard` / `isRescueCard` /
  "New word" labels are decided. Add the rare-word banner there.
- `frontend/lib/types.ts` — IntroCard type must mirror the new
  `frequency_rank` field.
- `frontend/lib/mock-data.ts` — keep mock data in sync (CLAUDE.md rule
  #11 — frontend/backend type sync).

## Suggested implementation outline

This is one path, not the only one — feel free to deviate.

### Backend

1. **Extend the intro-card response schema** (`schemas.py`):
   - Add `frequency_rank: int | None` to the IntroCard / NewWordCard
     pydantic model.
   - Add `frequency_source_count: int | None` (number of sources in
     `FrequencyCoreEntry.source_set_csv`) if you want the "how confident
     is this rank" signal. Otherwise just `frequency_rank`.

2. **Populate it where intro cards are built**. The simplest pattern:
   left-join `FrequencyCoreEntry` on `lemma_id` when assembling the
   IntroCard payload. `None` rank = not in the top frequency curriculum
   at all (the strongest "rare" signal).

3. **Add a suspend endpoint**:
   - `POST /api/words/{lemma_id}/suspend`
   - Resolves canonical via `canonical_resolution.resolve_canonical_lemma_id`
     (don't trust caller).
   - Sets `UserLemmaKnowledge.knowledge_state = "suspended"`.
   - Deactivates pending sentences: `UPDATE sentences SET is_active = 0
     WHERE target_lemma_id = :lemma_id AND is_active = 1`.
   - Logs an `interaction_logger` event: `{"event": "rare_word_suspended",
     "lemma_id": ..., "frequency_rank": ...}` so we can audit how often
     users hit suspend.

4. **Don't filter rare words out of the intro pool upstream.** The user
   wants visibility + choice, not a silent skip. Keep `word_selector`
   logic unchanged; only the UI decides whether to warn.

### Frontend

1. Read `frequency_rank` off the IntroCard prop in `index.tsx`.
2. Branch:
   - `frequency_rank == null` → "Not in the top-3000 frequency core" banner.
   - `frequency_rank > 3000` → "Rank #{rank} — outside the top 3000" banner.
   - Otherwise → no banner.
3. The banner sits between the existing card body and the "Continue"
   button. Style consistent with the existing card chrome.
4. The "Suspend this word" button calls the new endpoint, optimistically
   marks the card as skipped in local state, advances to the next card.
5. Surface a brief inline confirmation: "Suspended {word}. Any pending
   sentences containing it have been removed."

## Things to avoid (mistakes I'd predict)

- **Don't conflate "rare" with "bad import."** Some words flagged as rare
  are legitimately niche but worth keeping (poetic terms, classical
  vocabulary). The warning is informational; the user decides.
- **Don't auto-suspend.** The whole point is user agency.
- **Don't use `lemma_lookup`-style dedup for frequency lookup.** Query
  `FrequencyCoreEntry` directly by `lemma_id`.
- **Don't forget canonical resolution.** Variants point at canonicals.
  The frequency entry lives on the canonical. Always resolve first.
- **Don't add a "rare word" filter to `select_next_words`.** That would
  hide rare words entirely. Surface them; let the user decide.
- **Don't try to also cancel SentenceWord rows.** They don't drive
  scheduling — `Sentence.is_active` + the eligibility gate do.
- Per the user's CLAUDE.md preferences: no test plans in PR descriptions,
  prefix branches with `sh/`, never amend commits (always create new
  ones).

## Hard invariants to respect (from CLAUDE.md)

- **All sentence generation must go through `generate_material_for_word()`** —
  don't bypass for this feature.
- **Canonical lemma is the unit of scheduling** — resolve before any ULK
  mutation.
- **SQLite write-lock discipline** — the suspend endpoint must not hold a
  DB session open across LLM calls. Pure DB write, no LLM in the path.

## Reference: today's related work

Three related cleanup-runs landed in main today (2026-05-15), context for
why this issue surfaced:

- **PR #76** — Quran imports now lemmatize via CAMeL before creating
  canonicals, so future imports don't produce inflected-form intro cards
  like نَزَّلْنَا.
- **PR #77** (bundled) — MLE shadda-preservation override + lemma
  vocalization service for transliteration display.
- **PR #78** — OCR textbook-scan path uses CAMeL vocalized lex for
  `lemma_ar`; data cleanup for 114 al-display rows, 3 cross-root chimera
  lemmas (incl. #2307 آنِسَة/نسي/Miss that caused the "Anisa for forget"
  bug in stories), and #1527 إلزامي corrupted bare.

The frequency-rank field on the IntroCard, once exposed, becomes useful
for an eventual audit of "intros from below rank 3000" too — but that's
out of scope here.

## How to scope a clarifying question if you need one

If you hit ambiguity, ask the user one focused question with concrete
options (use the AskUserQuestion tool with 2-4 alternatives), then
proceed. Don't pause for permission on routine implementation choices —
the user prefers terse, end-to-end ownership over consensus-building.
