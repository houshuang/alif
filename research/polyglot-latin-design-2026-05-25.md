# Polyglot — Adding Latin (design + audit), 2026-05-25

Adding Latin as a second Polyglot language alongside Modern Greek. The learner
has finished **LLPSI Part 1 (Familia Romana)** and wants to (1) verify which of
those ~1,800 words they still know, (2) grow vocabulary gradually with low daily
effort, and (3) read an interesting text. This doc records the architecture
audit, the two risk areas the user flagged (LatinCy solidity; cross-language DB
leakage), and the implementation plan + status.

## Decisions (locked)

- **Reading text:** Eutropius, *Breviarium ab urbe condita* (public-domain;
  DCC has an annotated edition that pairs with the core list). Gentlest genuine
  post-LLPSI narrative history.
- **Growth ("to-learn") set:** DCC Core (~1,000, frequency rank backbone) **and**
  Roma Aeterna (LLPSI 2) vocabulary, both frequency-ranked.
- **Assumed-known seed:** LLPSI Familia Romana ~1,800 lemmas, marked
  `UserLemmaKnowledge(state='known', source='llpsi_known')` with no FSRS card —
  verified by collateral exposure via the existing scaffold-confirmation engine.
- **Lemmatizer:** LatinCy `la_core_web_lg` primary, simplemma fallback, behind
  the existing LLM lemma-quality safety net.

## 1. LatinCy — empirical evaluation + quality-gate design

The user's concern: "problematic lemmatization has been a huge recurring issue."
So we ran `la_core_web_lg` 3.9.0 (spaCy 3.8.14) over deliberately hard cases
rather than trusting the paper's 94.7% (probe: `tests/test_la_provider.py`
slow tests + the one-off probe script). Findings:

**Solid (context-disambiguated, where simplemma can't):**
- `est`→`sum`; `cum` prep (ADP) vs subordinator (SCONJ) **correctly split by
  context**; `modo` adv vs `modus` noun by context; deponents `secuti`→`sequor`,
  `loquitur`→`loquor`; `-que` auto-split (`populusque`→`populus`+`que`); proper
  nouns mid-sentence (`Romulus`, `Caesar`, `Hannibal`, `Carthago`, `Cannae`);
  `librum`→`liber` (macron homograph resolved by context); medieval `celum`→
  `caelum`. Eutropius opening + Hannibal sentences: ~all correct.

**Failure modes (the safety net must cover — same classes Greek already handles):**
1. **Sentence-initial capitalization → false PROPN.** `Malus`, `Uita`, `Ianua`,
   `Liber`, `Os`, `Amasse` all flipped to PROPN purely from the leading capital;
   their lowercase/mid-sentence forms tag correctly. Highest-impact: every
   sentence's first word is capitalized, so raw use would drop ~1 content
   word/sentence from review. → Mitigations: pass full-sentence `context` to the
   lemmatizer (only the genuine sentence-initial token is at risk, not all
   tokens); the LLM quality gate is the designated catcher (it already does this
   for Greek `Τίγρης`→`τίγρη`); **closed-vocabulary anchoring** (below) covers it
   for seeded vocab.
2. **Homographs** `malum` (apple/evil) vs `malus` (bad) still flip. → LLM gate.
3. **`-ne`/`-ve` enclitics fuse** (`estne`→junk lemma `estne`). We do **not**
   suffix-strip them: a `-ne` strip can't be distinguished from 3rd-declension
   ablatives `homine`/`ordine`/`ratione` (which it would mangle), and `-ue`
   collides with `-que`. Left to the LLM gate, like Greek σε-crasis. (LatinCy
   handles `-que` itself.)
4. **u/i orthography**: LatinCy emits lemmas in u/i form (`uenio`, `uir`).
   `normalize_bare` folds v→u, j→i, so the lookup key reconciles with v/j-spelled
   seed vocab. Verified: `normalize_bare("venio") == normalize_bare("uenio")`.

**Verdict:** LatinCy is solid enough **as primary, but only behind the same
safety net Greek already has** — it is not trusted raw. Required gates:
- **Pre-normalization** (macrons, v→u, j→i) for the lookup key — done in `la.py`.
- **LLM lemma-quality gate** (`lemma_quality.py`) with a Latin-specific warning
  about sentence-initial→PROPN and homograph flips (mirrors the Greek homograph
  rule and the Arabic feminine-ة CAMeL lesson). [pending — task #5]
- **Citation repair** (`lemma_integrity.py`) — already language-agnostic.
- **Closed-vocabulary anchoring**: a learner working a known curriculum (LLPSI +
  DCC + RA = ~3k seeded lemmas) maps most reading tokens to an existing Lemma.
  `reading_intake._lookup_lemma(language_code, lemma_bare)` is already
  language-scoped; the normalized bare key resolves `uenit`→seeded `venio`. The
  lemmatizer only has to be solid on the long tail.

## 2. Cross-language DB leakage audit

The user's concern: "make 100% sure data is stored properly and no risk of
leakage between languages." Audit of every table + every aggregate query:

**Tables with `language_code`** (clean): `Language`, `Lemma`, `FrequencyEntry`,
`Sentence`, `Story`, `MaterialJob`, `ActivityLog`.

**Tables WITHOUT `language_code`** (language flows via FK): `UserLemmaKnowledge`
(keyed by `lemma_id`), `ReviewLog`, `SentenceReviewLog`, `SentenceWord`, `Page`,
`PageWord`, `ContentFlag`. These are the leakage risk surface — an aggregate over
ULK/ReviewLog without joining `Lemma` mixes languages.

**Verified clean (already language-scoped):**
- Lemma lookup/dedup: `reading_intake._lookup_lemma` filters
  `language_code AND lemma_bare` (index `ix_lemmas_lang_bare`). A Latin import
  cannot match a Greek lemma.
- Due selection: `sentence_selector._fsrs_due_lemmas` and `_acquisition_due_lemmas`
  both `JOIN Lemma` + filter `language_code`. No content leakage into sessions.
- Stats: every `get_stats` aggregate joins `Lemma`/`Story`/`FrequencyEntry` and
  filters `language_code`.
- Review credit (`sentence_review_service`, `fsrs_service`): keyed by `lemma_id`,
  inherently single-language.

**Found + fixed — cross-language *pacing* coupling** (no data corruption, but
two active languages would share budgets): the daily-intro cap, recovery-mode
signals, and dynamic intro-card budget queried ULK/ReviewLog with **no language
filter**. Greek-only never exposed it. Fixed by joining `Lemma` + filtering
`language_code` in `acquisition_service._daily_intro_count`,
`_recovery_backlog_counts`, `_recovery_mode_intro_budget`, and
`sentence_selector._dynamic_intro_cap`, threading the language through
`start_acquisition`/`build_session`. Regression: `test_daily_cap_is_per_language`.
Committed (commit "language-scope the acquisition pacing aggregates").

**Noted, low-priority:** leech sweeps (`check_and_manage_leeches`,
`check_leech_reintroductions`) are global but process per-lemma correctly (no
mis-credit); a per-language cron could scope them later. `UserProfile` is a
shared singleton (cognate threshold, known_languages) — acceptable for a
single-user app.

**Process/storage guardrails already in place:** separate `polyglot.db`; the
`.env` load-order fix (2026-05-20) that prevented `alif.db` contamination;
`_seed_languages()` seeds the `la` row at startup.

## 3. Seeding design — "which words do I know?"

The verification mechanism already exists and is language-agnostic: the
scaffold-confirmation engine (`fsrs_service.record_scaffold_confirmation`,
PR #138). We seed it for Latin:

1. **DCC core** → `FrequencyEntry(source='dickinson_core')` for `frequency_rank`.
2. **LLPSI Familia Romana** → `Lemma` rows (gloss from the list), marked
   `ULK(state='known', source='llpsi_known')`, **no FSRS card** — assumed-known
   scaffold, identical to Greek's cognate pool.
3. **DCC-beyond-LLPSI + Roma Aeterna** → `Lemma` rows as the learn-frontier
   (frequency-ranked; surfaced by the picker/warm-cache as they appear).

Then, with low daily effort: green collateral exposure stamps `confirmed_at`
("still know it"); a red miss lapses into Box-1 acquisition ("forgot it"). Word
info / philology cards fire only for actively-acquired words (the enrichment
selection policy already excludes `state='known'`), matching the user's "word
info only for words being acquired."

## 4. Architecture readiness

Multi-language was designed in from day one: provider registry
(`services/languages/`), `language_code` columns, `Language` table with the `la`
row seeded, `LA_FUNCTION_WORDS`, a Latin branch in the generation register
prompt, `LANGUAGE_NAMES`/`isGreekLatin()` in the frontend, graceful era-color
fallback. Remaining Latin-specific seams: lemmatizer wiring (done), philology era
taxonomy (Greek-hardcoded), generation function-word/scaffold lists, the vocab
importer, and the frontend `"ar"|"el"` → `+"la"` generalization.

## 5. Implementation status

- [x] Cross-language pacing leakage fixed + tested (commit 1).
- [x] LatinCy wired into `la.py` (primary + simplemma fallback, normalization,
      enclitics-to-gate), fast + slow tests green (commit 2).
- [ ] Philology era taxonomy language-keyed (Latin eras).
- [ ] Latin generation seams (function words, scaffold, validator, gate prompt).
- [ ] Latin vocab importer (DCC + LLPSI + RA).
- [ ] Eutropius reading-text import.
- [ ] Frontend Latin enablement.

## Display + canonicalization policy (decided 2026-05-25, with real data)

Real data loaded: LLPSI Familia Romana PDF (1,866 entries, all 35 capitula) and
Roma Aeterna Anki deck (2,675 entries). Parsers committed:
`scripts/parse_llpsi_pdf.py` (column-X parsing of the Latin|English|Derivative
table) and `scripts/parse_roma_aeterna_apkg.py` (apkg → SQLite notes, HTML/entity
strip). Source TSVs stay gitignored (LLPSI/RA copyright). Eutropius Book 1 fetched
from the Latin Library as the first reading text.

Two policies, both driven by the data:
- **Unified display (no macrons, u/i, 1sg/nominative lemma)** — per user: "unify
  all display, no macrons, always `facio` etc." So for Latin `lemma_form ==
  lemma_bare` (the normalized key). Macrons never affect lemmatization anyway
  (the key strips them; LatinCy is macron-agnostic); this is purely display
  consistency across the macron'd RA list, the macron-free LLPSI/Eutropius, and
  LatinCy output.
- **Canonicalize seed citation forms through LatinCy** — LLPSI lists verbs by
  infinitive (`facere`, `capere`, `posse`) but reading text lemmatizes to 1sg
  (`facio`, `capio`, `possum`). Without canonicalization the learner's known
  verbs never match what they read. The importer runs each citation form through
  LatinCy (`_USE_LEMMATIZER`, on by default).

**Validation (temp DB, real data):** LLPSI → 1,585 canonical assumed-known
lemmas (no FSRS card); Roma Aeterna → 2,518 learn-frontier lemmas. Eutropius
Book 1 content-word coverage by LLPSI: **67%** (rose from 60% once
canonicalization fixed the infinitive mismatch). Of 180 distinct new content
lemmas, 105 are already in the Roma Aeterna learning pool. Real reading
comprehension is higher still — proper nouns (168 tokens, recognizable names)
and function words (all known) push unknown running-text words under ~20%, a
good comprehensible-input level for an LLPSI graduate. Confirms Eutropius is
well-matched and the seed mechanism works end-to-end.

## Sources
- DCC Latin Core Vocabulary: https://dcc.dickinson.edu/vocab/core-vocabulary (CC BY-SA 3.0)
- LLPSI Familia Romana official vocab PDF: https://hackettpublishing.com/pdfs/Familia_Romana_Latin-English_Vocabulary.pdf (~1,800 lemmas; chapter tags via Anki decks)
- LatinCy: https://huggingface.co/latincy/la_core_web_lg ; paper https://arxiv.org/abs/2305.04365
- Eutropius (DCC annotated): https://dcc.dickinson.edu/eutropius/intro/full-text
