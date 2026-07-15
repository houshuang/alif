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

---

# §7 FINDINGS (investigation executed 2026-07-15, prod snapshot 09:28)

Machine-readable traces, census lists, blast-radius rows, and the fix-shape
scores (incl. the ~800-surface good-set fixtures) are in
`lookup-collision-findings-2026-07-15.json`. Harness scripts lived in the
session scratchpad; they mirror `lookup_lemma()` layer-by-layer using the
production helpers and were sanity-checked against the real function
(`real_lookup_agrees=true` on all traces).

## Q1 — Layer traces: the §1 hypothesis is REFUTED

The clitic-strip layer is (almost) innocent. Per-pair traces on the
pre-intake vocabulary (the 16 workaround-created lemmas #4247–#4262 excluded):

- **16/18 pairs come from the CAMeL last-resort layer** (`_camel_disambiguate`
  → `find_best_db_match`), incl. لاحظ→حظ and سيجار→جار — لا and س are not in
  `PROCLITICS` at all, so these were never clitic strips.
- **2/18 involve the clitic layer**: كناس→ناس (ك stripped as preposition) and
  ادرك→دار (ك stripped as *enclitic* "your", remainder ادر hit a generated
  form of دَارّ).

Root cause in `morphology.find_best_db_match()`: it iterates the analyzer's
**unranked** analysis list and returns the first analysis whose lex is any
known bare form. For a citation form not in the vocabulary, the correct
top parse (lex = the word itself) is not in the DB, so the loop descends
into junk parses until something matches. It has no "the best analysis says
this is a word you don't know — stop" signal. Additionally, a layer-4 match
reports **no `out_alternatives` and `via_clitic=False`**, so downstream LLM
disambiguation treats it as a confident direct match; only mapping
verification can catch it.

## Q2 — Census

**Part A (self-resolution, all 3,994 lemmas against the current lookup):**
71 raw violations → classified: 9 intentional function-form overrides (إنّ/أنّ
family — by design), 16 resolve-to-nothing (single-letter clitic lemmas لـ/سـ,
junk variant rows), 18 variant-lemma mis-resolutions, and **28 canonical
lemmas whose citation form resolves to a different lemma**. Most of the 28
are the already-documented structural bare-form homograph issue (IDEAS.md
"bare-form homograph collision"; قدم/نزل families, مثل/مَثَلَ, قط/قَطَّ …) where
first-wins + hamza/CAMeL tiebreak picks the sibling. Genuinely new datapoints:
نَباتِيّ→نَبَّأ (forms_json-registered key), فَضْلَة→ضَلَّ (CAMeL), قَاضٍ→قَضَى.

**Part B (1,879 unlinked FrequencyCoreEntry display forms = future /add
inputs):** 123 resolve to some lemma today. 60 are the *same word*
(missing FCE link — /add would correctly no-op; separate small cleanup) and
**63 are true future collisions**: 30 CAMeL last-resort (فستان "dress"→سِتّ
"six", براعة→رَعي, غرامة→جْرَام, سارع→رَعي…), 27 direct hits on registered
derived/generated forms (masdar/participle skeleton homographs: تفوق→فَاقَ,
خشية→خَشِيَ, لجوء→لَجَأَ…), 6 clitic (فقه→فَاقَ, حبك→حُبّ, جاك→لَجَأَ…). ≈3.4% of
future frequency-core imports would silently mis-resolve.

**Bonus finding:** a strict resolver surfaces **62 canonical lemmas whose own
citation form only self-resolves via the CAMeL layer** — i.e. corrupt/
inconsistent `lemma_ar_bare` fields the fuzzy layer currently papers over:
مِقْلَمَة/مقلم (truncated ة), مَقْلِيّ/مقل, زَبَادِي/زباد, شَوارْمَة/شوارمه (ه↔ة),
سَنْدَوِيتْش/ساندويتش. These need a bare-field repair pass alongside the fix
(else strict /add would duplicate them instead of no-op).

## Q3 — Blast radius in stored data

Counting SentenceWord rows where surface-bare == collision query and
lemma_id == wrong target, across momo pairs + census A + census B:

- **This bug class (momo + census B): 121 rows, of which 5 reviewable** —
  1× توقف→وَقَفَ (sentence 51442, active + current verification; the LLM
  verifier passed it because the glosses are adjacent), 3× ستين→سِتّ,
  1× طلوع→طَلَعَ. The other 116 rows sit in **inactive** book/corpus sentences
  (Momo, Hindawi: أَصْبَحَ→صُبْح, تَوَقَّفَ→وَقَفَ, صَبِيّ→صَبّ …) — invisible to review
  today, but **the healing pass only revisits NULL mappings, never wrong
  non-NULL ones**, so they persist until a remap sweep.
- Census A pairs show 1,700 rows / 50 reviewable, but these are skeleton
  homographs where the surface legitimately matches both lemmas — ambiguity
  *exposure* of the known structural issue, not confirmed damage from this
  bug. Excluded from this fix's remediation scope.

Post-fix remediation is therefore small: remap + reverify the 5 reviewable
rows' sentences, plus a corpus-wide remap sweep for the 116 inactive ones.

## Q4 — Caller matrix

| Caller | Fuzzy layers reached | Downstream protection |
|---|---|---|
| `/api/discover/add` (`_create_and_introduce`) | all (clitic + CAMeL L4) | **none** — introduces immediately (the bug) |
| `/api/discover/words` | all, per token | none; wrong "already known" silently suppresses suggestions (why كناس never appeared) |
| generation `map_tokens_to_lemmas` | all | LLM disambiguation (but L4 reports no alternatives) + mapping verification + `mappings_verified_at` gate — mostly protected; توقف slipped once |
| book/corpus import | all | reviewability gate defers to later verification; wrong mappings persist in inactive rows |
| remap healer `fix_null_lemma_ids` | all | only touches NULL rows; can *write* wrong mappings that verification must later catch |
| readiness scans (`reading_readiness.py`) | all | analysis only — silently inflates "known" counts |

Answer to "why doesn't CAMeL context save the text path": CAMeL is only ever
used to *pick among already-found candidates* (collision sets, multi-clitic)
or as an even fuzzier last resort. **No caller runs a whole-word plausibility
gate.** The only real protection anywhere is generation-time LLM verification,
which /add doesn't have.

## Q5 — Fix-shape decision matrix

Variants scored on: BAD-momo (18 pairs, pre-intake lookup; pass = None),
BAD-FCE (63 future collisions; pass = None), GOOD retention (distinct
verified surface→lemma pairs from active sentences where the current lookup
agrees with the stored mapping: 60 direct / 400 clitic-layer / 400
CAMeL-layer surfaces).

| Variant | BAD-momo | BAD-FCE | good clitic | good CAMeL |
|---|---|---|---|---|
| BASE (current) | 0/18 | 0/63 | 400/400 | 400/400 |
| V1 drop CAMeL L4 | 16/18 | 30/63 | 400/400 | **0/400** |
| V2 MLE-gated L4 (accept only if MLE top lex ∈ vocab) | 13/18 | 20/63 | 400/400 | 377/400 |
| V4 MLE whole-word gate + drop L4 | 17/18 | 34/63 | 395/400 | 0/400 |
| V6 **citation-strict**: gate + clitic restricted to ال-bearing prefixes (بال/وال/فال/كال/لل) + no L4 | **18/18** | **36/63** | all بالمكتبة-class pass | n/a for /add |

Key facts driving the decision:

1. **The CAMeL last resort is load-bearing for running text** — 400 verified
   good mappings (tanwin accusatives عَامًا→عام, conjugations يَسْتَطِعْ→استطاع)
   resolve *only* via layer 4. Dropping it globally (V1) is catastrophic.
   So the fix must split by context — exactly the spec's design tension.
2. For **citation forms**, nothing below layer 2 is trustworthy: the MLE
   whole-word gate alone misses كناس (CAMeL's own top pick for كناس *is* the
   ك+ناس parse), but restricting citation-mode clitic stripping to
   orthographically unambiguous ال-bearing prefixes catches it while keeping
   بالمكتبة→مكتبة, للأطفال→طفل working. V6 zeroes the momo set.
3. V6's 27 residual FCE hits are all **direct matches on keys the vocabulary
   itself registered** (forms_json masdars, generated conjugations —
   خشية→خَشِيَ, تفوق→فَاقَ). That is a *different, defensible* behavior ("this
   form is claimed by a known lemma") and cannot be fixed by fallback gating;
   flagging them is a form-registration question, out of scope here.
4. For the **shared text path**, V2 (replace the greedy scan with
   MLE-pick-only) retains 94% of good layer-4 rescues; of the 23 lost, most
   go to None (token stays unmapped → existing healing/verification machinery)
   rather than to a wrong lemma, and the text path keeps its LLM-verification
   backstop.

**Recommendation (two independent changes):**

- **Fix A (/add and any citation-form entry, e.g. FCE intake):** add a
  citation-strict mode to the lookup (V6): layers 0–2 unchanged + MLE
  whole-word gate + ال-prefix-only clitic stripping + no CAMeL last resort.
  Smallest blast radius, zeroes the observed bug class, and turns the 62
  corrupt-bare lemmas into visible repair work instead of silent no-ops.
- **Fix B (shared path, lower urgency):** make `find_best_db_match`'s
  last-resort role MLE-gated (V2) — accept only the analysis the MLE
  disambiguator actually ranks first. Also make layer 4 report
  `out_alternatives`/a `via_camel` flag so downstream disambiguation stops
  treating fuzzy rescues as confident matches. Ship separately with an
  experiment-log entry; watch unmapped-token and verification-failure rates.

Sequencing per §6: Fix A on `sh/lookup-clitic-collision` with the §4 test set
(fixtures in the findings JSON: 18 momo pairs, 63 FCE collisions, 28 census-A
violations, 400+400 good surfaces) → deploy → remap+reverify the 5 reviewable
rows + corpus sweep for the 116 inactive ones → re-add تالي/حقيقي via /add →
bare-field repair for the 62 corrupt lemmas → then evaluate Fix B.
