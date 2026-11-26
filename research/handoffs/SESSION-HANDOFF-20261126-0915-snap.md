# Session handoff — Snap reading workflow, 26 November 2026

Record of Stian's messages (verbatim, with times; the extractor shortens long ones, the full text
is quoted below): `SESSION-HANDOFF-20261126-0915-snap.record.md`, next to this file.

## Front page

**Goal, in Stian's terms.** Reading real Arabic books with Snap should be fast and trustworthy:
tap a word and get the right meaning at once; for a word he studies, the same missed-word card as
in review, with its reason buttons; for a new word, a gloss for this sentence so he can keep
reading, plus Add (and Enrich if he wants depth). Nothing with a wrong gloss may enter the
general vocabulary. Show the translation as early as possible.

**Where things stand (all deployed 26 Nov: backend, iOS OTA `01a216f7`, web).**
- Reading dock fits an iPhone 16: reasons in one row under the word panel, panel sized from the
  space the sentence leaves (PR #359). `card_shown` now logs `detail.screen` (w, h, font scale).
- Snap word sheet (PR #360): studied word → `DockWordPanel` + reason row; reasons call
  `POST /api/discover/lookup/miss` (`lookup_miss_v1`, table `lookup_misses`): opens a
  missed-word recovery entry for FSRS words, evidence only for acquiring words, never touches
  FSRS/ReviewLog/leech run. New word → general gloss, "Here: …", idiom, Add, Enrich
  (`/add?enrich=true`). Only the tapped Arabic word is highlighted; its phrase is underlined.
- Gloss safety: `_gloss()` in `routers/discover.py` returns `gloss_en` (general, stored),
  `gloss_here` (display only), `phrase_ar/phrase_en` (saved to `word_add_contexts.phrase_json`,
  offered to the word card). Verified on prod with اعتلى.
- Repaired imports: 24 of 280 snap/preread/bookifier/dragoman lemmas (PR #361,
  `scripts/fix_import_glosses_2026_11_26.py`, audit in `research/gloss-audit-2026-11-26/`), and 4
  wrong roots (PR #362, `scripts/fix_roots_2026_11_26.py`). Backups
  `alif_pre_snap_card_*`, `alif_pre_gloss_repair_*`, `alif_pre_root_fix_*` in `/opt/alif-backups/`.
- Word-card backlog run from 25 Nov finished: 2,871 written, 0 failed batches.

**Next concrete step.** Snap page parsing on the pages already snapped (`snap_pages`, 16+ pages,
no photos stored): build a background pre-match of every page word after OCR, measure it
against the 38 logged per-tap sense checks (`discover_lookup_sense` events in
`data/logs/interactions_2026-11-*.jsonl`), then wire it in so taps read a cache. After that,
translation-first (see Open threads).

**What Stian is waiting for.** Faster, accurate taps and an earlier translation; his first use of
the new Snap sheet and the iPhone dock layout (feedback not yet given).

## Stian's instructions, verbatim

- **08:20** "we did a big refactor of the missed words card yesterday, and started a lot of data
  processing on the server. check in on the data processing, but also the cards look really bad
  on my phone, i guess it's smaller than you accounted for. we might need to adjust, but i'm also
  wondering if we could do something innovative with the 3 missed buttons . could we have some
  kind of interaction to bring them up, like swiping or ? but it has to be super easy, so maybe we
  shouldn't. Not sure. Either way we need to fix it. i have an iphone 16, can you use the emulator
  to check size, line wrapping etc? / also look at whether we want to include more info on the new
  intro card, just missed cards etc? / and finally we should look carefully at the snap ux and
  workflow - you should have interaciton data there for me (I hope we're logging it in detail) -
  it works pretty well but when I select a word it takes quite a long time... not sure how we can
  get around that, I do want it to be very accurate and we can't afford to do a full preprocessing
  of every single page i photo, i guess? or can we, in th ebackground while i'm reading? ... Also,
  we should probably show some of the distractors there as well - like if i click a word it's
  because i'm not sure, so when it comes up with "you knew this word", maybe I should be able to
  say "i didn't recognize it because of word form" etc? this needs to be prototyped..."
- **08:39** "note this error on a word captured through snap. words with wrong glosses entering
  the general system is extremely problematic." (with a pasted exchange about أَبْصَرَ stored as
  "was born")
- **08:46** "why not use the missed word card for snap as well, at least for words that are
  already known? for new words, we don't need to generate all that yet - and the most important
  is a contextual gloss so that i can keep reading. but once we add it, we need to do the full
  enrichment, and generalize. if it's an important idiomatical usage that can be added as an
  example sentence etc? / how much claude would be required to do the matching of every word of a
  page against the db reliably, and how much of it is mechanical and how much is claude? if it is
  reasonable to do it I think it would improve the experience a lot. Also the way clicking a word
  highlights several words is a bit weird"
- **08:52** "i just want the missed word card, with its reason buttons. for a brand new word, we
  just need an add button. we could also add an enrich button if we wanted to dig down and wnated
  to do the enrichment right there (it would be added and turned into a missed word card). / So
  we'll need to go back and fix all the ones imported (or double-check), and hten iterate on the
  pages iv'e already snapped and how fast and accurate we can do the parsing etc. We should show
  the translation as soon as possible, even starting with just the first paragraph if that is
  faster so i can start reading faster, and then do enrichment in the background"
  — **supersedes** the three design-explorer prototypes (look-alikes / reasons row / "did you
  recognise it?"); he wants exactly the missed-word card + reasons.
- **09:24** "fix wrong roots. fix main. deploy, write handoff"

Standing rules from CLAUDE.md that bit this session: never publish to Claude Artifacts; deploy
only from main; back up the DB before data changes; experiment-log entry before scheduling
changes; one reviewed PR per change, self-merged.

## Decisions and reasons

- **No swipe gestures for the reasons** (hidden, fight the panel's scroll; he asked for "super
  easy"). One visible full-width row instead.
- **Panel height floor = the old 32% cap, counted together with the reason row**, so long
  sentences never get less room than before; grows to 60% of the reading area when space allows.
- **Snap reasons do not write ReviewLog rows.** Open misses were ReviewLog-only; fake review rows
  would distort accuracy stats, leech counts and seen counters. A separate `lookup_misses` table
  is merged into `open_misses()`, rating-1 equivalent, `misses_in_run` not raised. This effect
  was chosen by the session and stated to Stian at 08:52; he has not objected. Acquiring words:
  evidence only.
- **Reasons hidden until the sense check finishes**, so a reason cannot land on a wrongly matched
  word.
- **"Mixed" is a plain toggle in Snap** (no confusion-naming popover yet) — a scope cut, named.
- **Duplicates merged as variants** (`canonical_lemma_id`), not FK repointing: 18 FK columns now
  reference lemmas and the old merge script covered 12.
- **The page pre-match cannot skip the model for "clean" matches**: against the 38 logged checks
  the model changed 6 of 17 clean unique matches and 15 of 24 risky ones.
- **Rejected:** "you knew this word" messaging (most known-word taps were a different word on a
  known root); look-alike/recognise-first prototypes (superseded by his 08:52 message).

## Working understanding (unconfirmed)

- Page pre-match estimate: ~220 tokens, ~178 unique per page; ~140 items need the model
  (~5k tokens in, ~3k out); 3–4 parallel chunks ≈ 15–20 s; free on the Claude CLI, ≈ $0.06/page
  on the API. Not built or measured.
- Batched checking (`preread_service.check_senses`) is assumed about as accurate as per-tap
  checks; test it on the 38 logged checks before switching.
- Snap OCR+translation+alignment is one Gemini call, median ~14 s (gemini-4-hydrogen; untagged
  in `llm_calls_*.jsonl`); discovery glosses (~7 s) and the chip check add more before the
  response. Streaming vs. paragraph-split not evaluated; RN `fetch` does not stream bodies, so
  plan on a background job + polling.
- The resolver matched عيّنت to عَيْن although عَيَّنَ exists; the shadda should break the tie
  (`docs/nlp-pipeline.md` § "Homograph keys"). Not investigated.
- 411 words without a word card are not in active sentences (suspended, variants), so the cron
  correctly selects none; 58 known words in sentences without a card were not explained.
- Open question for Stian at the end of the first reply, still unanswered: should the per-tap
  fallback move to the paid API (~2–3 s faster)?

## Open threads

1. **Background page pre-match + cached taps** — next session. Measure on stored `snap_pages`
   first.
2. **Translation first** — async snap job; show the first paragraph as soon as it exists, words
   and checks after. Today `POST /api/discover/snap` is synchronous.
3. **Richer intro and phrase-follow-up cards** — word cards (chain, set phrases) are not shown on
   intro cards or "Missed word" phrase cards; new intake words get a card only at the next cron.
   Raised by Stian at 08:20; not built.
4. **"Mixed" naming popover in Snap.**
5. **Resolver shadda tie** (above).
6. **Stian's feedback** on the iPhone dock layout and the Snap sheet — waiting on him.
7. Two `tests/test_confusion_learning.py` failures exist on main (not from this session).

## Where things are

- Code: `frontend/app/snap.tsx` (WordPanel), `frontend/app/index.tsx` (dock sizing),
  `frontend/lib/review/AssistedRecognitionCauses.tsx`, `backend/app/routers/discover.py`
  (`_gloss`, `/lookup/miss`, `/add?enrich`), `backend/app/services/missed_word_recovery.py`,
  `backend/app/services/word_card.py` (`enrich_word_cards_for`).
- Docs updated: experiment-log entry "2026-11-26: Snap word sheet…", `docs/scheduling-system.md`
  (lookup_miss_v1), `docs/data-model.md`, `docs/api-reference.md`, `docs/frontend-files.md`,
  CLAUDE.md hard invariants (two new bullets), `IDEAS.md` (two new entries at the top).
- Memory: `project_snap_word_sheet.md`.
- Local test harness used: copy of the newest `/opt/alif-backups/*.db`, backend run with
  `background_threads.ENABLED=False` and BackgroundTasks no-op'd, `expo start --web --port 8191`
  (8091 is used by another project), a 393×759 iframe page in `frontend/public/` (not committed).
  Metro in `.claude/worktrees` needs `--clear` after edits; jest needs `--no-watchman`. The local
  backend still reaches the `claude` binary despite a trimmed PATH.
- No worktrees, branches or processes left running.

## Links into the transcript

Look up with `python3 ~/src/limbic/skills/session-handoff/scripts/handoff_extract.py 59f4453e-7296-48ef-b1cc-7bffe0a67a7f --at "<time>"`:
08:20 (original request), 08:39 (أَبْصَرَ gloss error), 08:46 (missed-word card in Snap; how much
Claude per page), 08:52 (final Snap scope, translation first), 09:24 (roots, main, deploy, handoff).
