# Running-text resolver: why 50 of 196 book tokens matched the wrong word

Investigation date: 2026-11-18. Code read from `origin/main` at `022b0414`.
Reproduction ran against a production snapshot taken 2026-11-18 08:21 UTC (4,583 lemmas; copy deleted afterwards). Status: Rank 1 (resolver side) and Rank 2 shipped the same day, see §7; Ranks 3–5 are open.

Regression fixture: `backend/tests/fixtures/resolver_collisions_2026_11_18.json` (all 188 scored rows; the 50 wrong ones carry mechanism class, deciding layer, the word the text uses, and the result after the repairs in §7). Tests: `backend/tests/test_resolver_collisions.py`. The tracer and fix-variant simulator were session scratch and are not kept; §7 describes how the replay is run.

The input file has 196 lines; 8 have a null verdict, so 188 rows are scored: 138 fits/broader/name, 50 different. All 188 reproduce exactly on the snapshot.

---

## 1. Headline findings

1. **Only 10 of the 50 are cases where the right lemma exists and the resolver picked another one.** In the other 39 the right lemma is missing from the vocabulary (or the row that owns the skeleton carries the wrong sense), and one (حلقي) is a false alarm from the sense check. For those 39 the best a resolver can do is return "no match" or "uncertain" instead of a confident wrong sibling.
2. **The error rate depends on the layer that produced the match.** On these book pages:

   | Layer that produced the match | Rows | Wrong | Wrong share |
   |---|---|---|---|
   | CAMeL last resort | 35 | 20 | 57% |
   | Clitic strip, several stems, CAMeL picks | 39 | 11 | 28% |
   | Clitic strip, one stem | 6 | 2 | 33% |
   | `ال`-add layer | 1 | 1 | — |
   | Direct key (incl. collisions) | 107 | 16 | 15% |
   | Function-word direct | 1 | 0 | — |

   On verified app sentences (LLM-generated, vocalized, in-vocabulary) the same CAMeL last-resort layer agrees with the stored mapping 96% of the time (2,656 of 2,773 distinct surface–lemma pairs). The layer is tuned for text whose words are all in the vocabulary; real book text with many out-of-vocabulary words breaks it.
3. **The resolver discards its own provenance.** It knows whether a match was an exact lemma key, a `forms_json` key, a generated conjugation, a clitic strip or a CAMeL guess, and returns only a lemma id. Snap, `/words`, OCR textbook scans and story imports then show or persist a 57%-wrong guess with the same confidence as an exact match.
4. **The LLM mapping verifier leaks.** The replay set of "verified" mappings contains the same errors: ابنه → أَب (father), لفتاة → لَفَتَ, والشجرة → شَجَرَ "to occur", وعلى → عَلِيّ (Ali), كتابًا → تَابَ, وبدأ → بُدّ, وقرأ → قَرّ. Any replay metric that treats stored mappings as ground truth undercounts the benefit of a fix and overcounts regressions.

---

## 2. The 50 wrong matches by mechanism

"Exists" = the lemma the text actually uses is in the vocabulary.

### M1 — Agreeing clitic stems overridden by CAMeL (4 rows; right lemma exists in all 4)

ابنها → أَب (should be اِبْن #79), برأسه → رِئَاسَة (رَأْس #349), حياته → حَيّ (حَياة #354), بجدته → جَدّ (جَدّة #45).

Code path: `lookup_lemma()` in `backend/app/services/sentence_validator.py:1596-1624`. `_strip_clitics` returns both `X` and `ال+X` for each stem (`_add_with_al_variants`, line 644), and Pass 1 of `build_lemma_lookup` registers both keys for every lemma. So one clean parse (ابن + ها) yields `candidates = [79, 79]`. `len(candidates) > 1` is read as "ambiguous", and `_camel_disambiguate` is called on the whole word. That function (line 1633) matches CAMeL's analyses against **every key in the lookup** and may return a lemma that is not among the candidates. For ابنها CAMeL's first in-vocabulary analysis is the verb آب; normalized it is `اب`, which is أَب.

Two defects: the candidate list is not de-duplicated, and the tie-breaker is not restricted to the candidates.

### M2 — `ال`-add layer hits a generated conjugation (1 row; right lemma exists)

فحين → لَفَحَ "to scorch" (should be حِينَ #2068 via فـ + حين).

Code path: `_lookup_exact_layers`, lines 1476-1481: for a token of 3+ letters not starting with `ال`, try `"ال" + token`. The key `الفحين` exists because `_generate_verb_conjugations` (line 1792) takes the full cross product of present prefixes `ي ت ا ن` and suffixes `ون ان ين ن ي`, so لفح gets `ا + لفح + ين`. First-person prefixes never take those suffixes. The layer returns before clitic stripping can find ف + حين.

Pass 1 and Pass 2 register `ال+X` only together with `X`, so this layer can only ever fire on generated forms, ى/ي variants and function forms. In the replay it decides 34 distinct pairs; removing it changes 7, six of them from wrong to right.

### M3 — CAMeL last resort (20 rows; right lemma missing in 19, false alarm in 1)

Code path: `lookup_lemma()` lines 1626-1629 → `_camel_disambiguate` → `morphology.find_best_db_match` (`morphology.py:256-288`). It walks the **unranked** analyzer list and returns the first analysis whose `lex`, after alef normalization, is any lookup key.

- **M3a — the lex is a different lexeme whose citation form coincides with a derived or generated key of another lemma (11).**
  أمضت → ماضِي (lex أَمْضَى hits `forms_json.elative`), تتعرف → عَرَفَ and سيتقدم → قَدَمَ (lex تَعَرَّف / تَقَدَّم hit generated conjugations تعرف / تقدم), أنشئ → نَشَأَ and أُسقطا → سَقَطَ (Form IV lex hits the Form I imperative اُنْشَأْ / اُسْقُطْ), تقارير → قَرّ, خاطفة → خَطَف, باندفاعة → اِنْدَفَعَ, اعتدالاً → اِعْتَدَلَ, وإيماناً → آمَنَ, انتشاراً → اِنْتَشَرَ (masdar / participle keys).
  Cause: `known_bare_forms = set(lemma_lookup.keys())` (line 1642). A CAMeL `lex` is a dictionary headword; comparing it with conjugation and imperative keys is a category error. In 9 of the 11 the matching analysis is CAMeL's first, so an "MLE top pick only" gate does not help; the fault is the key set.
- **M3b — the lex matches a lemma only after hamza/alef normalization (4).**
  وزارة → زَارَ (analysis 8 of 29: زَأَر "to roar" → `زار`), الفتى → أَلْف (آلِف → `الف`), ويبدو → بَدَأَ (بَدَا → `بدا`), تأسف → آسِف (أَسِف → `اسف`).
- **M3c — CAMeL's parse is a neighbouring real lemma (5).**
  شخصية → شَخْص, بأسى → بَأْس (should be بـ + أسى), لإمرار → مُرّ, عشر → عَشْرَة (marginal), حلقي → حَلَق. The last is the same word; the check objected to the vocalization حَلَق for حَلْق. Count it as a false alarm and fix `lemma_ar` on #2074.

### M4 — Clitic stem is a homograph and the first-registered sibling wins (5 rows; right lemma exists in 3)

لأمر → آمَرَ #1185 (Form III; أَمْر #2501 and أَمَرَ #4600 share the key), والنقل → نَقَلَ verb #4339 (noun نَقْل #4612 exists), ترويها → رَيّ #194 "watering" (رَوَى #2640 exists), والقدر → قَدَّرَ #2781 (قَدَر missing; قَدْر #4605 exists), خصمه → خَصَم verb (noun missing).

Code path: lines 1596-1599 read `lemma_lookup[norm_stem]` and never consult `lemma_lookup.collisions`. The direct layer does consult it; the clitic layer does not. The winner is whichever lemma Pass 1 inserted first, i.e. the lowest lemma id. `out_alternatives` gets nothing, so LLM disambiguation is told the match is unambiguous. `وال`/`بال`/`لل` prefixes prove the stem is a noun, and that evidence is unused.

### M5 — Clitic strip lands on a derived form; right lemma missing (4 rows)

لإعداد → عَدَد (key أعداد, the plural; إ/أ collapse hides that إعداد is a different word), بالقضاء → قَضَى (masdar key), وأشبه → شَبَّ (و and ه both stripped, remainder `اشب` is a generated 1sg of شبّ), بالطبع → طَبْع (the expression "of course"; arguably acceptable).

### M6 — Direct key hits (16 rows; right lemma exists in 2)

- **M6a — skeleton homograph; the sibling the text uses is missing or the row has the wrong sense (8).**
  دار → #1206 `دَارٌّ` "flowing copiously" (no "house" row exists; this row looks like a mis-enriched دار), كاتب → #1384 كَاتَبَ "to correspond with" (no "writer" row), حملوا → حَمَّل and صنعوا → صَنَّع (only the Form II verb exists under the skeleton), ظلّ → ظِلّ "shadow" (verb ظَلَّ missing), خرّج → خَرَجَ (the printed shadda is thrown away by `strip_diacritics`), المدرسة → مَدْرَسَة (text means مُدَرِّسة), فقد → فَقْد "loss" (text is فـ + قد; #2054 exists, but the approved alias covers only fully vocalized فَقَدْ).
- **M6b — a `forms_json` key claims a distinct lexeme (6).**
  تنفيذ → نَفَّذ and زهو → زَهَا (masdar), مبارزة → مُبَارِز (`feminine` key collides with the masdar "duel"), المستهدف → اِسْتَهْدَفَ (participle), المصير → صَارَ (`passive_participle` = مصير is junk enrichment), رأيت → ياتُرَى #2735.
  رأيت is the sharpest: the adverb row #2735 "I wonder" carries the whole conjugation table of رَأَى in `forms_json`. Pass 2 registers `رايت` for #2735 first (lower id); #4576 رَأَى is recorded as a collision. `_resolve_collision` (line 1389) then (a) compares the bare surface with the stored **diacritized** form string, which can never be equal, and (b) asks CAMeL to match its lex against those form strings rather than against the candidates' lemma bares, which also cannot match. It falls through to first-wins.
- **M6c — speculative generated key (2).**
  فنان → فَنّ (Pass 3 dual `فن + ان`), أدري → دَرَّ (generated 1sg of درّ; درى missing).

### Tally

| Class | Rows | Right lemma exists |
|---|---|---|
| M1 unanimous clitic parse overridden | 4 | 4 |
| M2 `ال`-add onto generated form | 1 | 1 |
| M3a CAMeL lex vs derived keys | 11 | 0 |
| M3b CAMeL lex via hamza collapse | 4 | 0 |
| M3c CAMeL neighbouring parse | 5 | 0 (1 false alarm) |
| M4 clitic stem ignores collisions | 5 | 3 |
| M5 clitic onto derived form | 4 | 0 |
| M6a direct skeleton homograph | 8 | 1 (فقد) |
| M6b `forms_json` key claims other lexeme | 6 | 1 (رأيت) |
| M6c generated key | 2 | 0 |
| **Total** | **50** | **10** |

The working hypothesis in `IDEAS.md` (17 Nov) ("one-letter prefix stripping beats a whole-word identity") describes only فحين. As in July, single-letter clitic stripping is mostly innocent; the CAMeL tie-breaker and last resort, derived-form keys and missing lemmas carry the errors.

---

## 3. Prior work and what is load-bearing

`git log --since="6 months ago" -- sentence_validator.py`: #99, #102, #212, #231, #232, #236, #239, #242, #310. Relevant history:

- **`research/spec-2026-07-15-lookup-clitic-collision.md` (§7–§8), PR #212.** Same bug family, found through `/api/discover/add`. Findings then: 16 of 18 collisions came from the CAMeL last resort. Shipped "Fix A" = `lookup_lemma_citation` for `/add` only. **"Fix B" (gate the shared-path last resort, report alternatives from that layer) was deferred** with the note "verification gate is holding, measure first" (`IDEAS.md:591`). This investigation is that measurement: on book text the gate is not holding.
- **The July decision matrix already rejected dropping the CAMeL last resort globally** (400/400 good CAMeL-layer mappings lost). Confirmed here: removing it loses 2,656 verified pairs and 11 of the 138 good book rows. Do not re-propose.
- **An MLE-top-pick gate alone is weak.** It fixed 3 of 20 here and lost 141 verified pairs. July's V2 looked better only because its bad set was citation forms.
- **`IDEAS.md:1381` "Vocalized-aware lemma identity for homographs" (open since 2026-05-15)** is the structural issue behind M4/M6a (Form I/II verbs sharing a bare key). `scripts/split_nzl_homograph_2026_05_15.py` and `import_scaffold_lemmas.py:ALLOW_HOMOGRAPH` are the existing workarounds.
- **`IDEAS.md:15-19` (17 Nov)** already lists this problem and "run the sense check on Snap chips and textbook-scan matches" as open ideas.
- **Load-bearing guards to leave alone:**
  - Two-pass priority: lemma bares beat `forms_json` (2026-02-18).
  - Function words use direct-only lookup, no clitic stripping (`كانت` ≠ ك + أنت; 2026-04-09).
  - The `ال`-add minimum length of 3 (أن → الآن guard).
  - Exact particle identities and the two exact running-text aliases (#239, #242) — fail-closed by design; do not generalize فَقَدْ → قَدْ to unvocalized فقد.
  - Pass 1b ى/ي variant (#102).
  - No auto-created lemmas from corrections; canonical redirection.
  - The `same_lemma` rejection in generation (unrelated but adjacent; not touched by anything below).
  - `lookup_lemma_citation` stays strict.
- CAMeL feminine-ة → 3ms-possessive misreads (memory note) are part of why CAMeL output must not override a clean deterministic parse.

---

## 4. Callers and downstream protection

| Caller | Resolver | Result goes to | Check before it matters |
|---|---|---|---|
| `routers/discover.py:348,374` `discover_words_in_text` — serves `/words`, `/snap` chips (saved in `SnapPage.words_json`), Snap tap `/lookup`, pre-read `analyze_pages` | `lookup_lemma_id`, `lookup_lemma` | Display; "already learning" suppression | **None** for `/words` and Snap chips. Snap tap: separate sync `/lookup/sense` call (Sonnet, one token + sentence). Pre-read: one batched `check_senses` call over learn/shaky words in a background job; **if the call fails the unchecked matches are used** (learn words introduced, shaky words pulled forward). |
| `ocr_service.py:920,1353` textbook scan | `lookup_lemma` | ULK creation (`encountered`), encounter counts | **None** |
| `story_service.py:241-279` `_create_story_words` (story generate/import, `import_book`) | `lookup_lemma_id` + `find_best_db_match` | `StoryWord.lemma_id`; later review credit on story completion | Only words mapped to **newly created** lemmas are verified. Matches to existing lemmas: **none** |
| `quran_service.py:667-676` | `lookup_lemma_id`, `find_best_db_match`, `lookup_lemma` | `QuranicVerseWord.lemma_id` | None (feature suspended) |
| `frequency_core_intake.py:93,108` | `lookup_lemma_id`, `lookup_lemma` | frequency-core entry → lemma link | None (known open item (b) from July) |
| `scripts/fix_null_lemma_ids.py:95` (cron step 0b) | `lookup_lemma_id` | Fills NULL `SentenceWord.lemma_id` | None at write; sentence stays behind the verification stamp |
| `scripts/backfill_story_words.py`, `bookify_arabic.py`, `reading_readiness.py` | `lookup_lemma_id` | StoryWord / PDF glossary / coverage numbers | None |
| `passage_generator.py:1730` `store_maintenance_passage` | `map_tokens_to_lemmas` | `SentenceWord`; stamps `mappings_verified_at=now` | **No synchronous mapping check** despite the stamp; only the daily 30-sentence re-verification cron |
| `material_generator.py:714,1230,1489,1615` | `map_tokens_to_lemmas` | `SentenceWord` | `batch_verify_sentences` / `verify_and_correct_mappings_llm`, sync, fail-closed |
| `book_import_service.py:330,872`, `corpus_enrichment.py:2983,3023` | `map_tokens_to_lemmas` | `SentenceWord` | LLM verification, sync, fail-closed |
| `/api/discover/add`, `book_coverage.py` | `lookup_lemma_citation` | lemma find-or-create | Strict resolver + gloss/POS rule |

Two things weaken even the "protected" rows. The verifier is shown the resolver's answer and passes adjacent-looking errors (§1.4). And matches from the CAMeL last resort and from M1/M4 report no `alternative_lemma_ids`, so the disambiguation step never sees a second candidate.

---

## 5. Candidate fixes

Scoring per variant: effect on the 50 wrong and 138 good book rows, and a replay over 33,219 distinct verified (surface, lemma) pairs (90,378 tokens, all sentences with a current verification stamp). "Changed" replay pairs are split by whether the old and new lemma are related, because plural-as-own-lemma duplicates (دُرُوس vs دَرْس, طُلّاب vs طالِب, بَنات vs بِنْت) and wrong stored mappings make raw agreement misleading.

### Rank 1 — Expose match provenance and use it in the unverified callers (not simulated; structural)

**Change.** Have `lookup_lemma` / `lookup_lemma_id` optionally report the deciding layer and key type (exact lemma key; `forms_json:<key>`; generated; clitic; CAMeL) and, where a collision or second parse existed, the alternatives. Then:
- Snap chips and `/words`: send every non-exact match through the existing batched `check_senses` (already done for pre-read); never suppress a word as "already learning" on a CAMeL-layer match.
- Pre-read: on `check_senses` failure, do not introduce or pull forward words whose match is not an exact lemma key.
- OCR textbook scan and `_create_story_words`: do not create ULKs or StoryWord links from CAMeL-layer matches without a check (leave unmapped, as an unknown word).
- `map_tokens_to_lemmas`: fill `alternative_lemma_ids` for collision stems and flag CAMeL-layer matches so the verifier prompt can treat them as guesses.

**Fixes.** No row changes resolution, but all 50 stop being shown as confident, and the 39 missing-lemma rows get the only treatment that can help them (sense check proposes the real word). **Risk.** Low for learner state; more Sonnet calls on Snap (batched, one per page). **Test.** Unit tests on the reported layer for the fixture rows; endpoint tests that a CAMeL-layer match is not auto-introduced when the check fails.

### Rank 2 — Small deterministic resolver repairs (simulated together as "stage 1")

- **F1. Trust an agreeing clitic parse.** De-duplicate `candidates`; when every stem resolves to the same lemma, the stem is that lemma's own bare form (not a derived or generated key) and is at least 3 letters, return it without asking CAMeL. Fixes M1 (4 of 4, all to the right lemma).
- **F2. Drop the `ال`-add layer for generated keys** (equivalently: remove it; it only fires on generated/variant keys). Fixes M2.
- **F3. Generate only real present-tense combinations** (`ي`: ون/ان/ن; `ت`: ون/ان/ين/ن/ي; `ا`: none except `ي`, which the feminine imperative اجلسي/ارسمي relies on today; `ن`: none). Removes 11,290 of 71,674 lookup keys. Also fixes M2 independently.

**Book rows:** 5 wrong → right (ابنها, برأسه, حياته, بجدته, فحين); 0 good rows lost; 2 good rows move from the singular to the plural lemma (دروسه → دُرُوس, بالرسائل → رَسائِل), because those plurals exist as separate non-variant lemmas.
**Replay:** 354 pairs wrong → right. 152 pairs "right → different": 77 are singular/plural-style form-of pairs, 36 same-root, 39 unrelated. Reading the unrelated ones: about half are the stored mapping being wrong and the new one right (ابنه, لفتاة, وقرأ, وبدأ, وبقي, وعلى, وأنت), a quarter are duplicates (بنات, شوكولاتة), and the rest are real regressions concentrated in tanwin forms (لَيْلًا → يلّا, بُرْجًا → رَجا, فِنْجَانًا → نَجا), which F4 below removes (unrelated changes drop from 39 to 23).
**Risk.** F1 moves credit from singular to plural-lemma duplicates for ~80 surfaces; cleaner to mark those plurals as variants first (`merge_lemma_variants.py` exists) or let CAMeL keep the override when the two lemmas are form-of related. F2/F3 are near-zero risk (1 replay pair changed for the worse, and that stored mapping, وَحِينَ → لَوْح, is itself wrong).
**Test.** Fixture rows with `expect: lemma:<id>`; replay harness; a unit test that no generated key combines a 1st-person prefix with a plural suffix.

### Rank 3 — Tanwin-alif layer, then narrow the CAMeL last resort ("stage 2")

- **F4. Tanwin-alif layer.** When the surface ends in `ًا` / `اً` (book print keeps this mark: وزيراً, اختصاراً, واسعاً), try the form without the final alef through the normal layers before any clitic or CAMeL guess. In the replay this moves 1,718 pairs out of the fuzzy layers and cuts the CAMeL last-resort load from 2,773 pairs to 1,132, with 58 wrong → right and 23 right → different (several of those are stored errors such as كِتَابًا → تَابَ).
- **F5. Last resort matches CAMeL's lex against lemma headwords only**, not against `forms_json` and generated keys. After F4: 7 more book rows go from wrong to "no match" (أمضت, تتعرف, سيتقدم, تقارير, خاطفة, باندفاعة, أُسقطا), 0 good book rows lost; replay loses 124 verified pairs to "no match" (mostly participle → verb such as المرتفعة → ارتفع, النائمين → نام) and 12 to a different lemma.
- **F5b (optional). Require hamza-exact agreement between lex and headword.** Adds تأسف and الفتى → "no match"; costs 1 good book row and 2 more replay pairs. وزارة and ويبدو are fixed by neither F5 nor F5b, because another analysis still lands on a real headword; of the variants tried only MLE gating catches وزارة, and ويبدو needs the missing lemma بَدَا (its stored sibling بَدَأَ has the bare form `بدا`).

**Combined stage 1 + stage 2 on the book rows:** 12 of 50 fixed (5 right, 7 no-match), 136 of 138 good rows unchanged, 2 moved to plural duplicates.
**Risk.** Medium. The 124 lost pairs become unmapped tokens: in generation that means a rejected sentence or a mapping-rescue pass, in Snap an "unknown word" chip. This is the July "Fix B" trade-off with a better gate than MLE (MLE variant: 317 lost for 14 fixed). Requires an experiment-log entry and a watch on unmapped-token and verification-failure rates. Could be shipped for the unverified callers only (a `strict_fallback` flag) to avoid touching generation at all.
**Test.** Fixture `not:<id>` rows; replay harness with a budget on "right → no match"; adjudicate the changed pairs with the sense-check model rather than trusting stored mappings.

### Rank 4 — Collision-aware stems and a working collision tie-breaker

**Change.** In the clitic layer, when the stem key is in `lookup.collisions`, resolve it the way the direct layer does and always report the siblings as alternatives; use the stripped clitic as evidence (`وال/بال/لل` ⇒ noun). Fix `_resolve_collision` to compare CAMeL's lex with the candidates' **lemma bares**, not with the stored form strings.
**Fixes.** Simulated tie-breaker fix alone: ترويها → رَوَى, plus ابنها and برأسه; expected with the noun rule: والنقل, and لأمر needs the alternatives + sense check. رأيت is not fixed by code (CAMeL does not return رأى for it); it needs the data repair below.
**Risk.** High as an automatic picker: replay shows 168 wrong → right but 144 right → different, with real regressions (الأمّ → أَلَم, أهمّ → وَهَمَ, زرتُ → زَرَّ). **Recommend the reporting half only** (alternatives for the verifier and the sense check), not the automatic CAMeL pick.

### Rank 5 — Vocabulary repairs (the resolver cannot fix these)

- **Wrong-sense or mis-vocalized rows that own a common skeleton:** #1206 دار, #1384 كاتب, #2781 قَدَّرَ (pos noun), #1575 عَشْرَةَ (pos verb), #2074 حَلَق, #194 رَيّ carrying verb forms. Fix through the existing regloss / lemma-decomposition audit.
- **Junk `forms_json`:** #2735 ياتُرَى carries رَأَى's conjugations (fixes رأيت); 40 non-verb lemmas have verb conjugation keys; صار has `passive_participle` مصير; `feminine` keys that are really masdars (مبارزة).
- **Missing common lemmas**, found here: وزارة, شخصية, فتى, بدا, دار (house), كاتب (writer), حَمَلَ, صَنَعَ, ظَلَّ, خَرَّجَ, أَمْضَى, تَعَرَّفَ, تَقَدَّمَ, أَنْشَأَ, أَسْقَطَ, تقرير, إعداد, قضاء, قَدَر, خَصْم, مصير, فنّان, إيمان, أسى, درى, أشبه, تنفيذ, مبارزة, زهو, اندفاعة. Every Snap "different" verdict already names the missing word; logging those verdicts gives a standing intake queue and a growing regression set.
- **Plural-as-own-lemma duplicates** (دُرُوس, طُلّاب, أَطْفَال, أَوْلاد, بَنات, أَيّام, رَسائِل, أَصْحاب …): merge as variants so F1 does not shift credit.
- **Form I / Form II verbs sharing a bare key** (حمل, صنع, خرج, قدّر): the open "vocalized-aware lemma identity" idea. A cheap partial step: when the surface prints a shadda that the matched lemma's `lemma_ar` lacks on that letter (خرّج vs خَرَجَ), treat the match as uncertain. Fixes 1 of 50; low priority.

### Not recommended

- Removing the CAMeL last resort globally (rejected in July; confirmed).
- MLE-top-pick gating as the main fix (3 of 20 here).
- Letting the tie-breaker choose only among clitic candidates when they disagree (makes لَيْلًا → يلّا-type errors worse: 225 right → different).
- Generalizing the exact aliases to unvocalized forms (فقد); context is required, which is the sense check's job.

---

## 6. Suggested order

1. Rank 1 (provenance + sense check on unverified surfaces): protects all 50 and every future out-of-vocabulary word.
2. Rank 2 (F1–F3) with the fixture and replay harness; merge the plural duplicates first or accept the credit shift.
3. Rank 5 data repairs for the six wrong-sense rows and #2735, plus an intake pass for the missing lemmas.
4. Rank 3 (F4, then F5) as its own experiment-log entry, possibly limited to unverified callers.
5. Rank 4 reporting half.

Open point for the product owner: Rank 3 trades wrong matches for "no match" (124 verified pairs in generation). Whether that applies everywhere or only to Snap, `/words`, OCR and story import is a scope decision, not a technical one.

---

## 7. Outcome: what shipped on 2026-11-18

Shipped from this list: Rank 1, resolver side, and Rank 2. Experiment-log entry: `2026-11-18 "Running-text resolver reports how it matched"`.

**Match provenance.** `lookup_lemma` and `lookup_lemma_id` take an optional `LemmaMatchProvenance` and fill in the deciding layer (`alias`, `function`, `lemma`, `forms`, `generated`, `clitic`, `camel`), the kind of key that was hit, and the alternatives. Return values are unchanged. `map_tokens_to_lemmas` sets `TokenMapping.via_camel` and `match_layer`, reports the siblings of a homograph stem as `alternative_lemma_ids`, and the verifier prompt labels a CAMeL match as a guess. The textbook scan and `_create_story_words` no longer write a CAMeL-only match (the scan sends the word down its new-word path; story import leaves it for `_import_unknown_words`). Generated maintenance passages keep the old behaviour through `trust_morphological_guesses=True`.

**Repairs.**
- F1: stems that all agree on one lemma are one parse. When the stem is that lemma's own bare form and at least three letters long, CAMeL can no longer replace it with an unrelated lemma. CAMeL keeps its override in two cases: the two lemmas are rows of one lexeme (one's bare form is a `forms_json` value of the other, so credit does not move to plural duplicates), or the word is CAMeL's lemma plus the accusative alif (لَيْلًا stays لَيْل, not لـ + يلّا).
- F2: `ال`-add ignores generated keys, in the exact layer and in the clitic layer's nothing-stripped stem. The second half was added after the measurement below; it can only remove matches onto generated verb forms.
- F3: present-tense generation uses only real prefix–suffix pairs; the lookup shrinks from 71,674 to 62,075 keys on the snapshot.

**Measured on the same snapshot, old code against new.**

| | Before | After |
|---|---|---|
| Book rows wrong (of 50) | 50 | 45; the 5 fixed are ابنها, برأسه, حياته, بجدته, فحين, all now on the right lemma |
| Book rows right (of 138) changed | — | 0 |
| Wrong book rows labelled as a guess | none | 20 (all of M3); 15 of the 138 right rows are also labelled |
| Replay: distinct verified pairs agreeing with the stored mapping (of 33,219) | 31,601 | 31,815 |
| Replay: pairs that changed | — | 274 disagree → agree (525 tokens); 60 agree → disagree (80 tokens); 2 stay in disagreement |
| Sentences with at least one ambiguous token (of 13,199) | 7,740 | 8,350 |

Of the 60 pairs that moved away from the stored mapping, 24 land on an unrelated lemma. Read one by one, 13 of those are the stored mapping being wrong (ابنه, لفتاة, وقرأ, وبدأ, وبقي, وعلى, وأنت, وحين), 7 are duplicate rows (بنات, شوكولاتة), and about 3 are real regressions (بمعاني → مَعاً, لبناني → بِنا, فكأنّ → كَانَ). The 36 same-root moves are mostly the noun replacing a verb the verifier had accepted (لرحلةٍ → رِحْلَة, بسرعةٍ → سُرْعَة, بدقّةٍ → دِقَّة).

**How to rerun the replay.** Take a read-only copy of the production database. With the old and the new `backend/app` in turn, build `build_comprehensive_lemma_lookup`, resolve every distinct `SentenceWord.surface_form` from sentences with a current `mappings_verified_at` through `lookup_lemma_id`, and compare both results with the stored canonical lemma. Stored mappings are not ground truth (§1.4): judge the changed pairs, not the totals.

**Still open.** Rank 1 consumers outside the resolver (Snap chips, `/words`, pre-read failure fallback) are separate work. `_import_unknown_words` still links an unknown story word to an existing lemma from CAMeL's first analysis without a check. Ranks 3, 4 and 5 are untouched: 45 of the 50 rows still resolve to the wrong lemma, 39 of them because the vocabulary lacks the word.
