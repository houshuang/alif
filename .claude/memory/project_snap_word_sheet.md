---
name: project-snap-word-sheet
description: "Snap word sheet = missed-word card + reasons (lookup_miss_v1), general glosses on Add, import gloss + root repair — LIVE (backend, OTA, web) 2026-11-26; parsing/translation-speed work open"
metadata:
  node_type: memory
  type: project
  originSessionId: 59f4453e-7296-48ef-b1cc-7bffe0a67a7f
  modified: 2026-11-26T12:56:45.656Z
---

Stian's Snap direction (2026-11-26): a tap on a studied word shows the review missed-word card
with its reason buttons; a new word shows only the sentence gloss + Add, and an Enrich button
(adds, enriches on the spot, turns into the card). Stored glosses must be general meanings:
"words with wrong glosses entering the general system is extremely problematic".

**Done (PRs #359–#361, backend deployed, migration dc3e4f5a6b78):** reasons write `lookup_misses`
and open a recovery miss for FSRS words only; `_gloss()` returns `gloss_en` (general) +
`gloss_here` (display) + idiom phrase; 24 of 280 imported lemmas repaired
(`scripts/fix_import_glosses_2026_11_26.py`, audit in `research/gloss-audit-2026-11-26/`,
backup `alif_pre_gloss_repair_20261126_080636.db`).

Roots of 4305/4307/4362/4367 fixed (`scripts/fix_roots_2026_11_26.py`); OTA
01a216f7 and web deployed 2026-11-26. Handoff: `research/handoffs/SESSION-HANDOFF-20261126-*-snap.md`.

**Page pre-match LIVE 2026-11-26 (PRs #363, #364).** Per-surface check stored in
`snap_pages.prematch_json`; taps read it (`card_shown`/`snap_tap`, `detail.source`, `detail.ms`).
v1 wrote "broader" widenings: Fable review found 14/113 bad + 80 hooks cleared → all 110 lemmas
restored from backup. Then the gloss gate (PR #365, `services/gloss_gate.py`, Opus judge + same-root
entries + Wiktionary index): all six writers write only approved widenings, hooks kept; on the
labelled 113: bad 0/14, good 62/78. Stian (26 Nov): "it's useless to ask me — i don't speak arabic
… you have to be [the judge]" — never ask him to rule on glosses; decide with models + dictionaries. `/snap` now returns after OCR (~6–12 s)
with `words_pending`; chips + pass run in background. Open from the review
(`research/snap-prematch-2026-11/review-2026-11-26.md`): per-tap and chip checks still write
widenings via the weak `_is_widening`; verdict for every line incl. fits/name; names offered as
vocabulary; repeated surfaces with different meanings; retry partial failures. Then: audit every
OCR intake surface for shared logic (Stian, 26 Nov).

**Later 26 Nov (PRs #366–#369, all deployed):** stale word links moved by cron Step 0c
(`services/lemma_remap.py`, Opus judge, verdict cache `lemma_remap_verdicts`): first run moved
3,091 links (sentence 1,623 / story 307 / Quran 1,161), 146 moves refused. "Neither" verdicts now
name the word (PR #371): 5 links repaired, ~28 named words missing from the vocabulary (ظَهْر
"back", عَالِم "scholar", ضِيق, لِين, حُلُول, بَرَكَة, عَرَب) listed in
`research/snap-prematch-2026-11/missing-lemmas-from-remap-2026-11-26.md`; 23 of them created
(PR #372, source `link_remap`, 4 seeded from verbs) and repairs applied to stored rows directly
(PR #373): 141 more links moved. PR #370: textbook scan one path + sense gate, shared
`ensure_material`. Local test venv: limbic must be an editable install of ~/src/limbic (an old
copy made the two confusion_learning tests fail); full suite 2,525 green 26 Nov. Story/book import glosses on CLI + general-meaning rule;
Snap chips and pre-read read the page pre-match; `name` verdict. OCR audit:
`research/snap-prematch-2026-11/ocr-surfaces-audit-2026-11-26.md` — all items closed 26 Nov
(failure policy table in docs/backend-services.md).

**Open:** splitting OCR from translation (snap Gemini = `gemini-3-flash-preview`, ~7 s median;
OCR-only ~3 s on short pages; not done); "Mixed" naming popover in Snap; resolver
picked عَيْن for عيّنت although عَيَّنَ exists (shadda should break the tie). Related:
[[project-word-cards]], [[project-missed-word-recovery]].
