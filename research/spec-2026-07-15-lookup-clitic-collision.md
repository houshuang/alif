# Spec: investigate lemma-lookup clitic-parse collisions (the لاحظ→حَظّ bug)

Date: 2026-07-15 · Status: INVESTIGATION SPEC (no fix designed yet)
Evidence: 17 documented collisions during the Momo vocab intake (2026-07-14/15) —
full table in `momo-vocab-queue-2026-07-15.md`; IDEAS.md entry same date.

## 1. Problem statement

`lookup_lemma()` (in `sentence_validator.py`, the production-hardened text→lemma
path) resolves an isolated bare form by, among other fallbacks, stripping clitic
prefixes. For **citation forms that are morphologically ambiguous with a
clitic+word parse**, it returns the wrong existing lemma instead of "not found":

| submitted (intended) | resolved to | parse taken |
|---|---|---|
| كناس "street sweeper" | نَاس "people" | كَـ (prep) + ناس |
| لاحظ "to notice" | حَظّ "luck" | لا (neg) + حظ |
| سيجار "cigar" | جَار "neighbor" | ? + جار |
| رمادي "gray" | رَمَاد "ash" | رماد + ي (poss./nisba) |
| تالي "next" | أَلَا (interjection) | ? |
| اصبح "to become" | صُبْح "morning" | ا? + صبح |
| امير "prince" | مَارّ "passer-by" | ? |
| + 10 more | | |

Two contexts, different severity:

- **`/api/discover/add`** (`_create_and_introduce`): uses `lookup_lemma` as its
  find-or-create existence check. A false match either silently no-ops (matched
  lemma already known — 15/17 cases) or **introduces the wrong lemma**
  (تالي→أَلَا, حقيقي→حَقِيق). New-word adds silently fail either way.
- **Running-text mapping** (corpus/book import, `remap_unmapped_sentence_words`,
  readiness scans): the same function maps sentence tokens. The Momo full-book
  tokenmap shows *mixed* behavior — some surface forms of كناس resolved (→ناس,
  counted "known"), others fell through to unmapped. If any reviewable sentence
  maps a كناس-class token to the clitic parse, **review credit and glosses go to
  the wrong lemma** — a data-integrity issue, not just an import annoyance.

Design tension to respect: clitic stripping is *the point* of this lookup (user
adds بالمكتبة → مكتبة must keep working), and this path is load-bearing and
heavily hardened (cf. the same_lemma-gate lesson: do not casually weaken it).

## 2. Prior-work check (mandatory before coding — Rule #14)

- `git log --since="3 months ago" --oneline -- backend/app/services/sentence_validator.py`
- `grep -i "clitic\|collision\|lookup" IDEAS.md research/experiment-log.md docs/nlp-pipeline.md`
- `ls backend/scripts/ | grep -i "clitic\|remap\|lookup"` — the clitic-leftover
  audit (2026-04, `cleanup_clitic_leftovers.py`) and mapping-rescue work are
  adjacent; check whether collision handling was already considered and scoped out.

## 3. Investigation questions (ordered)

### Q1 — Where exactly does each collision happen?
Instrument `lookup_lemma` (locally, on a prod snapshot): for each of the 17 pairs,
log which fallback layer produced the match (exact bare | variant map | clitic
strip combo | alef-normalized | other). Deliverable: per-pair trace table.
Hypothesis to confirm/refute: all 17 come from the clitic-strip layer with
single-letter prefixes (ك، ل، لا، و، ب، س) and/or suffix ي.

### Q2 — Vocabulary-wide census: how common is self-resolution failure?
Invariant: **every lemma's own bare form must resolve to itself** (or its
canonical). Run over all ~3,970 lemmas:
`lookup_lemma(L.lemma_ar_bare) ∈ {L.lemma_id, canonical(L)}`?
Every violation is a latent collision. Also run over the ~1,880 unlinked
FrequencyCoreEntry display forms (words we'll import someday) to find *future*
collisions before they bite. Deliverable: census list with parse traces —
this doubles as the regression-test fixture.

### Q3 — Blast radius in stored data: are any sentences mis-mapped?
For each collision pair (query, wrong_target): count `SentenceWord` rows where
`lemma_id = wrong_target.id` and the surface form's bare equals the *query* (e.g.
surface كناس mapped to ناس). Extend to census violations from Q2. Classify by
sentence reviewability (active + `mappings_verified_at` current). Deliverable:
count of live mis-mapped sentence words; if >0, they need remap + reverify (the
existing `fix_null_lemma_ids` / `reverify_all_active_sentences` machinery applies,
but only after the lookup is fixed — remapping with the buggy lookup is a no-op).
Note: generation-time LLM mapping verification may have caught many of these —
measure, don't assume, in either direction.

### Q4 — Why does CAMeL context not always save the text path?
The comprehensive path is documented as "clitic stripping + CAMeL disambiguation +
collision handling". Establish for each caller (corpus import, book import,
warm-cache verification, remap healer, discover /words vs /add) whether CAMeL
disambiguation actually gates the clitic-strip fallback, or whether lookup order
lets the clitic parse win before CAMeL is consulted. Deliverable: caller matrix
(caller × protection layer).

### Q5 — Which fix shape survives the good cases?
Candidate fixes, to be evaluated against BOTH the 17 bad pairs AND a
protection set of good clitic resolutions (بالمكتبة→مكتبة, وكسها→كس, بعضهم→بعض,
للأطفال→طفل — pull ~50 real examples from existing SentenceWord mappings):
1. **Caller-declared strictness**: `/add` passes `citation_form=True` →
   exact-bare / variant / alef-normalized matches only, no clitic stripping.
   (Smallest blast radius; fixes imports but not text mapping.)
2. **Whole-word priority**: if CAMeL can analyze the full form as a standalone
   content word (كناس → كَنَّاس noun), prefer "no match" (or the whole-word
   lemma if in vocab) over a clitic parse. Fixes both contexts; depends on Q4.
3. **Clitic-parse plausibility checks**: stripped remainder must itself be
   CAMeL-analyzable with POS compatible with the clitic (لا only before verbs/
   nominals it can negate; ك not before an already-definite...). Highest
   precision, highest complexity.
4. Length/coverage heuristics (matched lemma ≥ ~60% of query length) — cheap
   guardrail, catches سيجار→جار but not لاحظ→حظ borderline cases; consider only
   as belt-and-braces on top of 1 or 2.
Deliverable: decision matrix scored on the 17 bad + ~50 good pairs; pick the
smallest fix that zeroes the bad set without touching the good set.

## 4. Regression tests to ship with any fix

- `test_lookup_no_citation_collisions`: the 17 pairs (+ Q2 census violations)
  must return None or the correct lemma once it exists — never the collision target.
- `test_lookup_self_resolution_census`: the Q2 invariant as a standing test
  against the seeded test vocabulary, so future lemma additions can't silently
  collide.
- `test_lookup_good_clitic_resolutions`: the ~50-pair protection set stays green.
- `/add` round-trip: adding كناس creates كناس (not a ناس no-op) and introduces it.

## 5. Out of scope

- Redesigning variant detection or canonical resolution.
- The `/words` ranking issue (frequency-list words crowd out text-carrying words
  at count=50) — separate, smaller; noted in the queue doc.
- Fixing تالي/حقيقي vocabulary entries — do after the lookup fix via direct
  create; tracked in the tranche-2 queue.

## 6. Deliverables & sequencing

1. Traces + census + blast-radius numbers (Q1–Q3) → append findings to this doc.
2. Caller matrix (Q4) → decides whether the fix is /add-only or shared-path.
3. Fix per Q5 decision matrix, on a branch (`sh/lookup-clitic-collision`), with
   the full test set; self-review per CLAUDE.md Rule 7.
4. If Q3 found live mis-mappings: post-deploy remap + reverify sweep of affected
   sentences, then re-run the Q3 count to confirm zero.
5. Then: re-add تالي + حقيقي properly; update `docs/nlp-pipeline.md` collision
   section; experiment-log entry if the shared path changed.

Effort guess: Q1–Q4 ≈ half a day (read-only, snapshot-driven); fix + tests ≈
half a day if outcome is fix-shape 1, more if shape 2/3.
