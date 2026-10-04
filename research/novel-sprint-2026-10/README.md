# Novel sprint: *رجال في الشمس* by 31 December 2026

Started 4 October 2026. Decision record: experiment-log `2026-10-04 "Novel sprint v1"`.
Policy: `docs/scheduling-system.md` § Novel sprint v1.

## The goal

Read Ghassan Kanafani's *Men in the Sun* (1963) cover to cover before 2027, and show
every Sunday that the next chapter is read faster and with less help than the one before.
Stretch: finish *Momo* as well (page volume; coverage ~90%).

The book: seven chapters, about 15,400 running Arabic words (Rimal Books edition,
2013/2015, from the archive.org scan; a faithful chapter-split text is being prepared in
the bookifier input folder, not in this repo). Chapter 1 (أبو قيس) is already reviewed and
built as a bilingual EPUB (`rijal_abu_qays.v3.epub`). Chapter 1 coverage on 4 October:
86.9% now, 90.5% including in-progress words, 95.1% after the top 100 gap words.

Why this book: the best coverage curve of every text measured so far (June 2026: 84% →
96% after 150 words on chapter 1, where the long novels need 500 words for 91%), canonical
Arabic literature rather than a translation, and short enough that one page a day finishes it.

## Weekly rhythm

| Day | What | How much |
|---|---|---|
| Mon–Sat | Bilingual reading of the current chapter (Arabic first, English per paragraph on demand) | ~300 words/day, about one page; reread yesterday's page once without English before starting |
| Mon–Sat | Alif reviews | 60–90 cards; intros come from the book's own gap list (reading targets), 8/day max |
| Sunday | The test (below), ~10 minutes, then the week's row in `weekly.md` | one fresh passage from the chapter you start next |

Reading targets per chapter are filled in from the chapter token counts once the full text
manifest lands (`weekly.md` carries the live schedule). Working estimate: one chapter
every 10–12 days finishes the book around 20 December with slack for a missed week.

Vocabulary ahead of the reader: stage the next chapter's gap words one chapter ahead
(`scripts/novel_sprint_feed.py --text ch<N+1>.txt --program rijal_fi_al_shams --chapter N+1`),
so the words are in acquisition before the pages that need them. Out-of-vocabulary gaps need
a reviewed gloss file first (`--dry-run` lists them); proper names are never created.

## The Sunday test

Protocol from `recall-protocol-research.md` (Bernhardt L1 recall; Reed & Petscher "tell
everything"; NICHD prompt typology; QRI retell-then-questions order).

1. **Passage.** 150–200 words, unseen, unvocalized, from the chapter about to be started.
   Log word count, sentence count and Alif coverage of the passage.
2. **Prepared in advance (hidden from the reader).** 20–35 idea units tagged
   main/supporting/detail with accepted paraphrases; 2–3 probe questions on units a reader
   might plausibly miss (inference, referent, clause relation).
3. **Read.** Silent, timed, no lookups. Stop the timer at "done". wpm = words ÷ minutes.
4. **Hide the text. Record.** Fixed instruction, never varied:
   *"Tell me everything you remember from the text, in any order. Take your time."*
   (Norwegian: *"Fortell alt du husker fra teksten, i den rekkefølgen du vil. Ta deg god tid."*)
   During free recall only facilitators ("mm", "ok") and silence.
5. **Neutral prompts**, at most three, only after a pause of 5+ seconds or "that's it", in
   this order: P1 *"Do you remember anything else?"*; P2 *"Did anything else happen, before
   or after that?"*; P3 (once, reusing only the reader's own words) *"You mentioned X. Tell
   me more about that."* Never name content the reader has not produced. Everything before
   P1 is free recall (F); everything after is prompted (P).
6. **Probes (Q).** The 2–3 pre-written questions, text visible, answered in English or
   Norwegian (translating one sentence into English is allowed as a probe).
7. **One word on willingness to continue.** yes / meh / no.

Scoring, per idea unit, first adequate mention only:

| Code | Rule | Credit |
|---|---|---|
| V/G | verbatim or faithful paraphrase; minor inaccuracy that keeps the idea | 1 |
| PART | core predicate right, one required element missing or vague | 0.5 |
| DIST | mentioned but wrong: agent/patient, negation, pronoun referent, time or causal link | 0, +1 D |
| — | not mentioned | 0 |

Intrusions (I) counted separately: I-elab (plausible inference) and I-false (contradicts or
invents). For each DIST and I-false, tag the likely cause when evident from the Arabic
(word recognition, graphemic, syntax, intratextual, prior knowledge); otherwise leave blank.

Weekly row: wpm · F% · P% · F+P% · main-idea share · D · I · Q · willingness · passage
coverage% · chapters finished this week · lookups per 100 words in weekday reading (from the
reader, when available) · targets introduced this week · old-word ≥7-day clean rate.

Primary trend: F+P% with D, read next to wpm (rising wpm, stable F+P%, falling D is growing
fluency). Expect task-wiseness drift in weeks 1–3: run two unscored practice recalls first;
week 1 is the baseline. Scoring is LLM first-pass against the fixed idea-unit list, quoting
the transcript span for every credited unit, then hand-checked for every DIST and I.

Tooling: `sunday_test.py` prepares a passage (picks it, builds the hidden idea-unit list and
probes, renders a local timer page) and scores a transcript into `weekly.jsonl` + `weekly.md`.
Recording for now: phone voice memo or dictation into the chat; transcription/scoring in
conversation. The in-app path (chapter-reader recorder → Soniox → scoring) is specified in
`petrarca-voice-audit.md` and is a follow-up, not a prerequisite.

## Success and stop rules

- **Success on 31 December:** the book finished (every chapter completed in the reader or
  on paper); the last three Sunday tests at ≥100 wpm with F+P ≥ 60% and D ≤ 2; willingness
  mostly "yes".
- **Mid-course check (Sunday 2 November):** at least three chapters finished and wpm above
  the baseline; otherwise shrink the daily portion rather than the goal, and re-examine the
  support level before touching the schedule.
- **Guardrails:** old-word ≥7-day clean rate <80% or ≥14-day <78% for two consecutive weeks
  → halve the intake cap (env off = back to maintenance) and keep reading; strict main FSRS
  due ≥1,000 → stop intake until it is under 750.
- **Honesty rules:** a prepared passage is not a completed one; reading in conversation
  counts as reading but is logged as such; nothing is "finished" by opening a file.

## Files

- `README.md` — this protocol.
- `weekly.md` / `weekly.jsonl` — the weekly log (created by the first scored test).
- `recall-protocol-research.md` — literature and rubric.
- `petrarca-voice-audit.md` — reusable voice-assessment pipeline and what Alif already has.
- `sunday_test.py` — prepare/score tooling.
- Book text and derived passages live outside the repo (no book content is stored here).
