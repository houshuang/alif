---
name: bookify_arabic reading-aid tool
description: Ingest an Arabic chapter → fully-vocalized reader PDF with preface-vocab, two-tier word highlights, sentence-paired bilingual view, and optional import of top-N lemmas into Alif as "encountered".
type: project
originSessionId: a085cfcc-b102-4713-9c1d-549d50aaaad7
---

## Purpose
Take an Arabic chapter, identify lemmas not already in Alif's vocabulary, render:
- **Preface page**: top-N most-frequent new lemmas with glosses.
- **Body**: running text with two-tier highlighting — saffron solid underline for preface-keyed words, faint gray dotted underline for other unfamiliar words to infer from context.
- Optionally: side-by-side AR|EN sentence-pair bilingual view.

Three formats via `--format`: `glossary` (A5 portrait, preface + Arabic-only body), `bilingual` (A4 landscape, sentence-pair rows), `footnotes` (A5 portrait, body + per-page first-occurrence footnote for each new lemma — empirically 30s for 432 footnotes/37 pages via WeasyPrint, beautiful Scheherazade output).

Fourth subcommand: `introduce` — imports top-N preface lemmas into Alif as scaffold lemmas + ULK rows (state=encountered).

## Files
- Script: `backend/scripts/bookify_arabic.py` (~1200 lines after 2026-04-22 redesign)
- Bundled font: `backend/data/fonts/ScheherazadeNew/*.ttf` (SIL Scheherazade New v4.500) — loaded via `url(file://…)`, no system install needed
- Prod DB copy: `backend/data/alif.prod.db` (pulled via `scp alif:/opt/alif/backend/data/alif.db data/alif.prod.db`)
- Kalila pilot source: `backend/data/kalila_dove.txt` + vocalized `kalila_dove.vocalized.txt`
- Output: `backend/data/kalila_dove.{bilingual,glossary}.pdf` and `.html` debug

## Key design decisions (hard-won)
- **Must use prod DB** — local stale DB has ~259 known lemmas vs prod's 1525+. Pass `--db-path data/alif.prod.db`.
- **"Known" detection** is lenient, combining: (a) any ULK state (encountered, acquiring, learning, known, lapsed, suspended), (b) bare-form match dodges homographs ("ملك" known as "angel", text means "king" — still counts as known), (c) `frequency_rank ≤ 1000` assumed-known fallback.
- **Punctuation must be stripped before `lookup_lemma_id`** — tokens like `نفسه.`, `نفسك،` skipped lookup entirely. Pass `bare` not `token`.
- **Compound function-word prefix check**: `فلم` = ف+لم homograph-collides with فِلْمٌ (film). Generalizes to ف+إن, و+لا, etc.
- **Clitic folding for unknown surfaces** — surface forms الجرذ, للجرذ, والجرذ fold to one canonical `جرذ`.
- **Claude CLI needs `cli_only=True`** — API fallback chain is broken (openai rejects no-"json", anthropic/opus return unparseable). Pair `cli_only=True` with `json_schema=` (NOT `json_mode=True`) for constrained decoding.
- **Scheherazade New over Amiri** for vocalized text. Amiri's ligature set makes ḥarakāt positioning unpredictable on dense vocalization; Scheherazade New is SIL's pedagogy-grade Arabic face. Bundled in-repo via `url(file://…)` — no network fetch (which would hang WeasyPrint indefinitely).
- **A4 landscape for bilingual, A5 portrait for glossary** — page size branched in `_build_css(fmt)`. Bilingual needs width for both AR/EN columns + line-height 2.0 for tashkeel.
- **Two-tier highlight is the right default** — saffron solid underline for preface-keyed words, faint gray dotted for other unfamiliar words. Showing all 479 new words the same way was noise; only-preface left 400+ invisible.
- **Sentence-pair alignment (not whole-paragraph)** — for bilingual: split each paragraph into `pairs: [{ar, en}, …]` and render each pair as a table row with `break-inside: avoid`. Both languages visible on every page; no half-empty pages.

## In-session vs subprocess CLI (CRITICAL)
- **For vocalization, translation, sentence alignment in-session:** DO IT DIRECTLY. I am Claude; I have the text in context. Writing the vocalized/translated text straight to a file takes minutes, not hours. See /tmp/claude/pairs_p{0..4}.json from 2026-04-22 session (177 aligned pairs produced in-session).
- **Do NOT reach for `claude -p` subprocesses in-session** for tasks I can do directly. This cost hours on 2026-04-22: Sonnet CLI 240s timeout on 2.5K-char classical paragraphs; Haiku at 2-3 min/batch × 34 batches; sandbox blocks `/Users/stian/.claude.json` writes unless `dangerouslyDisableSandbox: true`.
- **If you must use `claude -p` in-session:** pass `dangerouslyDisableSandbox: true`, and do a direct probe first (`echo hi | timeout 30 claude -p --output-format text`) to confirm CLI works. The generic error `Claude CLI failed for claude_sonnet and cli_only=True` hides the real cause (often `EPERM open /Users/stian/.claude.json`).
- **`translate_paragraphs` is now per-paragraph** (not the original batched all-in-one) — smaller, retryable, partial-success tolerant. Still fails on paragraphs >2500 chars; falls back to in-session direct alignment.

## Current state (2026-04-22)
- Kalila dove pilot DONE: 5 paragraphs (cleaned down from 6 with Wikisource artefacts), 3162 tokens, 432 distinct new lemmas, 177 aligned sentence pairs, 100% glossed, 100% vocalized.
- 19 new lemmas imported to prod (`#3120–#3138`, source=scaffold) + 23 ULK rows seeded (state=encountered, source=book). Backup at `/opt/alif-backups/alif_pre_bookify_intro_20260422_110908.db`. Logged via `log_activity.py`.
- Three PDFs rendered: `kalila_dove.bilingual.pdf` (A4 landscape, ~240KB), `kalila_dove.glossary.pdf` (A5, ~150KB), `kalila_dove.footnotes.pdf` (A5, 37 pages, 432 first-occurrence footnotes — generated via paged.js spike 2026-04-22 after confirming WeasyPrint handles it in 30s).
- HTML session report at `research/bookify-kalila-dove-2026-04-22.html`. Renderer-spike report at `research/bookify-renderer-spike-2026-04-22.html`.

## Known follow-ups
- **POS tag per-surface drift** — e.g., `طَوَّقَ` glossed as "passive participle" because the surface in text was the passive participle form; gloss should be per-lemma not per-surface. Paper over with `run_quality_gates` grammar enrichment.
- **Large-paragraph translation** (>2500 chars) still fails via in-ingest CLI path; falls back to in-session direct alignment.
- **`introduce` ghosts: `source=scaffold` + no Story → invisible to word_selector** (diagnosed + partially fixed 2026-04-23). The 19 Kalila lemmas #3120–3138 were `encountered`/times_seen=0 for a full day. `word_selector._SOURCE_TIER_BONUS` has no `scaffold` entry (→ 0.0 priority_bonus) and bookify's `introduce` doesn't create `Story`+`StoryWord` rows. They were outranked by active `book_ocr` stories (Rosie+Prince of Physicians = 814 candidate word-slots at priority≈200).
  - **One-time fix applied 2026-04-23**: manually registered Kalila as Story #31 (`source=book_ocr`, `status=active`, page_count=5) with 19 StoryWord rows tagged to first-occurrence paragraph. Script: `/tmp/claude/register_kalila_story.py`. Verified: all 19 jumped to ranks 1–19 in `select_next_words()` with scores 192–199. Next Step C cron (~:30 every 3h) will generate sentences.
  - **Code follow-up (NOT done)**: teach `bookify_arabic.py introduce` to create a `Story + StoryWord` graph as part of the import. Mirror the pattern in `book_import_service.py:491-502`. Until then, every new bookify chapter needs manual Story registration.

## Renderer choice — settled 2026-04-22
**WeasyPrint stays.** Don't switch to paged.js / Chromium-based pagers without restructuring HTML first.
- WeasyPrint handles full Kalila chapter (432 first-occurrence footnotes) in 30s → 37 A5 pages, ~12 footnotes/page, beautiful shaping. Earlier "SLOW >100 lemmas" warning was a defensive guess.
- paged.js spike 2026-04-22: silently truncates 85-95% of body content on our long-paragraph layout (5 paragraphs × 500-800 tokens, no internal breaks). 4 pages emitted vs WeasyPrint's 37. Same truncation on bilingual HTML (4 vs 28 pages) and even on no-footnote baseline. paged.js's content-flow handler can't satisfy `break-inside: avoid` on too-tall blocks and gives up — emits colophon, exit 0, no warning. Scariest renderer bug class.
- Local pagedjs-cli quirk if we ever revisit: needs `--browserArgs "--no-sandbox,--disable-setuid-sandbox"`, otherwise WS endpoint timeout at 30s with no useful error. Spike report: `research/bookify-renderer-spike-2026-04-22.html`.

## How to resume (same chapter)
```
cd /Users/stian/src/alif/backend
# optional: refresh prod DB
scp alif:/opt/alif/backend/data/alif.db data/alif.prod.db
# re-render only
python3 scripts/bookify_arabic.py --db-path data/alif.prod.db render \
  data/kalila_dove.json data/kalila_dove.bilingual.pdf --format bilingual
python3 scripts/bookify_arabic.py --db-path data/alif.prod.db render \
  data/kalila_dove.json data/kalila_dove.glossary.pdf --format glossary
python3 scripts/bookify_arabic.py --db-path data/alif.prod.db render \
  data/kalila_dove.json data/kalila_dove.footnotes.pdf --format footnotes
```

## How to run a new chapter
1. Clean + vocalize source (DIRECTLY in session, not via CLI subprocess). Save to `data/{book}.vocalized.txt`.
2. `python3 scripts/bookify_arabic.py --db-path data/alif.prod.db ingest data/{book}.vocalized.txt data/{book}.json --title "…" --author "…" --no-translate`
3. Align each paragraph in-session to `/tmp/claude/pairs_p{n}.json`; patch into JSON with a small script.
4. Render bilingual + glossary.
5. Preview via `pdftoppm -png -r 200 -f 1 -l 4 <pdf> /tmp/claude/preview` before calling it done.
6. Introduce top-N to prod (always with backup + dry-run first + activity log).
