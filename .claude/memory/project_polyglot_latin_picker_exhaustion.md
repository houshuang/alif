---
name: polyglot-latin-picker-exhaustion
description: "Latin reader hit \"no sentences ready\" on 2026-05-26 — diagnosis is a coverage mismatch (right after fresh taps the picker has nothing the next Box-1 cycle can serve), not a learning-data deficit. Open: should we hook warm-on-intake, raise per-pass target, or lean on the Coverage Reader. Revisit when more Latin lemmas are due."
metadata: 
  node_type: memory
  type: project
  originSessionId: b082880e-223a-4e78-b598-a6edd0fd224c
---

# Latin sentence-picker exhaustion — diagnosis 2026-05-26

**Symptom:** Reader showed "no sentences ready" for Latin shortly after a flurry of word-taps. User assumed sentences had been consumed and that the LLPSI systematic-validation plan wasn't running. Both turned out wrong.

## What the DB actually showed (2026-05-26 ~12:15)

- **14 acquiring** Latin lemmas (all introduced *today* — Latin launched 2026-05-25)
- **21 LLM** + **96 textbook** active sentences for `language_code='la'`
- **10 sentence reviews** done (not 21 — 21 was the count *generated* by the 09:45 cron pass, not consumed)
- **11 LLM sentences still unshown** — but stacked on the 6 morning-introduced lemmas, NOT on the 7 lemmas about to come due next.
  - 7 lemmas due 12:01-12:15 had `unshown_llm=0` and `unshown_tb=0` each.
  - That's the "no sentences ready" symptom — picker can't return anything for those lemmas with `SENTENCE_RECENCY_HOURS=24` blocking the single shown LLM each.

## Collateral data WAS recorded (this part is working)

From the 10 sentence reviews:
- **72 ReviewLog rows** (62 collateral + 10 primary) — 7.2 lemmas credited per sentence.
- **46 `llpsi_known` rows** got `confirmed_at` stamped in the 11:55-12:11 review window — 4.6 LLPSI confirmations per sentence. `UNCONFIRMED_SCAFFOLD_BOOST=2.5` (Hard Invariant 6, polyglot/CLAUDE.md) is doing its job.
- **99 Latin ULK rows** with `clean_exposures > 0` for the day.

Page-sweep adds 35-46 confirmations per page-advance (Eutropius p1 — 2 advances → ~46 net confirms). Total `llpsi_known` confirmed: **82 / 1582 (5.2%)**.

## Structural cause of the picker exhaustion

Timing collision between three constants:

| Constant | Value | Where |
|---|---|---|
| Cron cadence | every 3h | `45 */3 * * *` |
| Box-1 acquiring due | every 4h | `acquisition_service.py` |
| `SENTENCE_RECENCY_HOURS` hard skip | 24h | `sentence_selector.py` (build_session) |
| `ACTIVE_TARGET` | 5 LLM sentences/lemma | `material_generator.py:118` |
| `POLYGLOT_WARM_SENTENCES_PER_TARGET` | 3 per pass | cron wrapper |

A freshly-tapped lemma reaches `ACTIVE_TARGET=5` only after **two** cron passes (3-6h). Its first Box-1 re-review happens after **4h**. So the second re-review reliably catches the picker with 1-3 LLM sentences total, all shown, all recency-blocked. Textbook fallback is also empty for freshly-tapped lemmas until later harvest passes touch the same page.

## The LLPSI systematic-validation question

User expected "tons of sentences to systematically validate the LLPSI vocab." Math:

- **Sentence path ceiling:** `14 acquiring × 5 ACTIVE_TARGET × 4.6 LLPSI confirms/sentence = ~320 confirms`. That caps at **~20% of 1582 LLPSI**. Sentence-driven LLPSI validation does not scale to full coverage. There is no dedicated "generate sentences whose primary purpose is LLPSI confirmation" pipeline — only the `UNCONFIRMED_SCAFFOLD_BOOST` weighting on sentences generated for the 13-14 acquiring targets.
- **Reader path ceiling:** much higher. Eutropius p1 alone confirmed 35-46 LLPSI words in one page-advance. The seed importer already created a `LLPSI Familia Romana — Coverage Reader` story (id=3) — **`last_viewed=0`** as of the diagnosis, 5 pages already warmed by today's cron. That is the scalable LLPSI-validation pathway and it has not been used yet.

## Open decisions (revisit when more Latin lemmas are due)

Three independent levers, listed cheap → expensive:

1. **Just read the Coverage Reader.** Story id=3 (LLPSI Familia Romana) is the scaling path. ~40-50 LLPSI confirms per page-advance, no code change. Should be the default thing tried before any structural fix.
2. **Manual warm now.** Run `scripts/warm_sentence_cache.py --language la --max-lemmas 32 --sentences-per-target 5` whenever the picker stalls. Unblocks the next session in ~2 min. Wasteful as a permanent pattern but fine as a recovery.
3. **Raise per-pass target 3 → 5** for fresh languages (set `POLYGLOT_WARM_SENTENCES_PER_TARGET=5` in the cron env, or add a per-language override). One cron pass then fills to `ACTIVE_TARGET` instead of needing two. Doesn't fix taps-just-after-cron-tick, but halves the window. Small change to the cron wrapper.
4. **Warm-on-intake hook.** Background-queue a `warm_sentence_cache(lemma_ids=[just_tapped_id])` call from `reading_intake.mark_lemma(state='unknown')`. Each new lemma gets its `ACTIVE_TARGET` sentences within seconds of the tap, not up to 3h later. Bigger change — touches `reading_intake.py`, needs LLM-cost spike accounting (a flurry of taps becomes a parallel LLM burst). Requires a worktree.

## Why not "build LLPSI batch validator"

Considered and rejected for now. A dedicated LLPSI-coverage generator (separate from the acquiring-target pipeline) would diverge from Alif's design — Alif has no equivalent because Arabic has no textbook-known scaffold tier. Per [[feedback_polyglot_mirror_alif]] divergence needs a specific Greek/Latin-driven reason; here the Coverage Reader story is the existing answer and just needs to be used.

## Notes for revisit

- Check `confirmed_at` count for `source='llpsi_known'` to gauge LLPSI progress.
- Check `unshown_llm` per acquiring lemma at the moment of complaint — that's the right diagnostic, not the bulk LLM-sentence count.
- Diagnostic queries used live in this file's git history (Bash tool calls 2026-05-26).
- Related: [[project_polyglot_latin_live]] (launch context), [[feedback_no_book_sentences_for_acquiring]] (why textbook can't paper over this), [[feedback_polyglot_mirror_alif]] (constraint on divergence).
