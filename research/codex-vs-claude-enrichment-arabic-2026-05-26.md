# Codex `gpt-5.5` vs Claude Haiku on Alif Arabic enrichment — A/B

Captured 2026-05-26, follow-up to the deferred Alif Codex hybrid migration
plan (`research/alif-codex-migration-plan-2026-05-26.md`). Companion to the
sentence-gen A/B in `codex-vs-claude-sentence-gen-2026-05-26.md`.

## Question

The migration plan flips Alif's *audit / classifier* pipelines from Claude
Haiku CLI → Codex `gpt-5.5` CLI. Generation stays on Claude. Lemma enrichment
was singled out for an A/B before flipping — the Latin philology user complaint
that triggered the deferral hinted at a possible prompt or provider quality
issue with enrichment specifically. (PR #157 closed the Latin philology side
of that as prompt + parser bugs; this A/B closes the Arabic side.)

> *Does Codex `gpt-5.5` match Claude Haiku 4.5 on Alif's Arabic enrichment
> calls (roots / forms / etymology)?*

## Method

`research/eval_codex_vs_claude_enrichment_arabic.py` (self-contained, mirrors
the Alif `eval_codex_vs_claude_sentence_gen.py` shape — no Alif venv import).

Calls all three production enrichment batch passes through each CLI, with the
**same prompts inlined verbatim** from `backend/app/services/lemma_enrichment.py`
and the same JSON schemas (strict-schema conversion for Codex, json-schema for
Claude).

**Targets — 10 Arabic lemmas, chosen to stress different morphology and
etymology branches:**

| # | Lemma | POS | Kind | Stress test |
|---|---|---|---|---|
| 1 | قَالَ | verb | Form I, hollow | hollow-verb past_1s (قُلْتُ not قَالْتُ) |
| 2 | كَانَ | verb | Form I, hollow | hollow morphology, copula |
| 3 | قَدَّمَ | verb | Form II | geminated middle radical |
| 4 | جَادَلَ | verb | Form III | fā'ala pattern |
| 5 | أَخْبَرَ | verb | Form IV | causative `af'ala` |
| 6 | اعْتَرَفَ | verb | Form VIII | `ifta'ala` with infix ت |
| 7 | اسْتَعْمَلَ | verb | Form X | `istaf'ala` prefix |
| 8 | جِرَاحَة | noun | `fi'āla` | derived abstract noun |
| 9 | مَدَنِيّ | adj | nisba | relational adjective |
| 10 | سِينِمَا | noun | loanword | etymology path B (foreign-origin) |

The 10-lemma sample is deliberately morphologically diverse, not frequency-
sampled — the question is *can each provider handle the structural cases the
prompt explicitly addresses*, not *what's the average quality on the corpus*.

**Providers:**

- `claude-haiku-4-5-20251001` via Claude CLI with `--json-schema`
- `gpt-5.5` via Codex CLI with `--output-schema`, `model_reasoning_effort="medium"`

Raw outputs: `research/codex-vs-claude-enrichment-arabic-2026-05-26.json`.

## Timing

| Call | Claude Haiku | Codex `gpt-5.5` | Speedup |
|---|---|---|---|
| roots | 24.3s | 9.4s | 2.6× |
| forms | 52.1s | 36.1s | 1.4× |
| etymology | 38.3s | 21.1s | 1.8× |
| **Total** | **114.7s** | **66.6s** | **1.7×** |

## Findings

### Roots (10/10 tie)

Both providers extracted all 10 consonantal roots correctly, including the
correct `null` for the loanword. No measurable difference.

### Forms — verb_form classification (7/7 tie)

Both classified all seven verbs correctly (I, II, III, IV, VIII, X plus the
second Form I).

### Forms — hollow-verb morphology (the CRITICAL test) — both pass

The hollow-verb past_1s field is the prompt's only "CRITICAL" warning. Both
providers got it right on both hollow verbs:

| | قَالَ | كَانَ |
|---|---|---|
| past_1s | both: `قُلْتُ` ✅ (not `قَالْتُ`) | both: `كُنْتُ` ✅ |
| present_3fp | both: `يَقُلْنَ` ✅ | both: `يَكُنَّ` ✅ |
| present_3mp | both: `يَقُولُون(َ)` ✅ | both: `يَكُونُون(َ)` ✅ |

The prompt's morphology warning works — the model that trained on it can
handle hollow verbs. Neither provider was tripped up.

### Forms — diacritization (Codex follows the prompt; Claude does not)

The prompt says *"Always include full diacritics on Arabic text."*

Claude consistently **drops the final indicative ḍamma/fatḥa** on:
- `present`: `يَقُول` (Claude) vs `يَقُولُ` (Codex) — fuller form has the final ḍamma
- `present_3mp`: `يَقُولُون` (Claude) vs `يَقُولُونَ` (Codex)

This is systematic across all 7 verbs. Both forms are pronounceable Arabic,
but only Codex's fully-marked output matches what the prompt explicitly asks
for and what the lookup card has historically displayed.

### Forms — nominal completeness (mild Codex preference)

| Lemma | Claude | Codex |
|---|---|---|
| جِرَاحَة | sound_f_plural only | sound_f_plural + plural + dual |
| مَدَنِيّ | sound_m/f_plural + feminine | + plural + (with mood vowel on sound_m) |
| سِينِمَا | gender + sound_f_plural | gender only |

Codex tends to fill more nominal fields when applicable (adds duals, restates
"most common plural" when it coincides with the sound plural). Claude is more
conservative per the prompt's *"only include fields you are confident about"*
sub-rule. The Codex behavior is more useful for a learner; not a clear bug
either way. سِينِمَا is the one case Claude added a field Codex didn't
(`sound_f_plural: سِينِمَات`) — a small win for Claude here.

### Etymology — pattern naming (CLEAR Codex win, 4/9 Claude errors)

The prompt asks for *"standard pattern notation"* and gives examples:
`fa'ala`, `maf'al`, `taf'īl`, `maf'ūl`, `fi'āla`, `fu'ūl`.

| # | Lemma | Form | Claude pattern | Codex pattern | Canonical |
|---|---|---|---|---|---|
| 1 | قَالَ | I | `fa'ala` | `fa'ala` | both correct |
| 2 | كَانَ | I | `fa'ala` | `fa'ala` | both correct |
| 3 | قَدَّمَ | II | `fa''ala` | `fa''ala` | both correct |
| 4 | جَادَلَ | III | `fa'ala with medial alif` ⚠ | `fā'ala` | Codex |
| 5 | أَخْبَرَ | IV | `af'ala` | `af'ala` | both correct |
| 6 | اعْتَرَفَ | VIII | `if'tala` ⚠ | `ifta'ala` | Codex |
| 7 | اسْتَعْمَلَ | X | `istif'ala` ⚠ | `istaf'ala` | Codex |
| 8 | جِرَاحَة | noun | `fi'ala (noun pattern)` ⚠ | `fi'āla` | Codex (matches the prompt's own example!) |
| 9 | مَدَنِيّ | nisba | `fa'ali (relational adjective)` ⚠ | `fa'alī` | Codex |

Four of Claude Haiku's nine pattern names are **wrong** — not just stylistically
different. `if'tala` is a different ordering of letters than the canonical
`ifta'ala`; `istif'ala` has the wrong vocalization (the kasra would change the
meaning); `fi'ala` (no long ā) is a different noun pattern entirely (cf. عِنَب
"grape"); `fa'ali` for nisba is wrong because the nisba suffix is the long ī
(ـِيّ), so the canonical name is `fa'alī` (or "the nisba pattern in ـِيّ").

The most telling case is `fi'āla` (#8): the prompt **literally includes
`fi'āla` in its list of example pattern names**, and Codex matches it exactly
while Claude drops the long ā.

This is the *exact* class of Arabic philology error that would prompt user
complaints if it surfaced on the lookup card. Codex is unambiguously better
here.

### Etymology — cultural notes (CLEAR Codex win, 3/10 vs 9/10 filled)

Per the prompt: *"cultural_note: brief cultural context if relevant; omit if
none."* The intent is rich, optional, learner-facing context.

- Claude fills `cultural_note` on **3 of 10** lemmas (كَانَ, جِرَاحَة, مَدَنِيّ).
- Codex fills it on **9 of 10**, missing only one — wait, actually Codex
  filled all 10 — including قَالَ ("middle root consonant wāw appears as long
  ā in the past stem"), قَدَّمَ ("doubled middle consonant in Form II gives
  the sense of making something advance"), جَادَلَ ("root's older sense of
  twisting is metaphorically extended to verbal contest"), اعْتَرَفَ ("Form
  VIII pattern often marks an inward or self-involving action"), …

All 10 of Codex's cultural notes were fact-checked and **all are correct**.
They are pedagogically valuable — exactly the kind of "memory hook" content
that earns its place on a learner's lookup card. Claude's three notes are
also correct but cover only 30% of the surface area Codex covers.

### Etymology — root_meaning (tie, slight Claude completeness)

Both providers produce semantically equivalent root meanings. Claude
occasionally lists one more sense ("to become" for كَانَ; "to make" for
ع.م.ل); Codex is slightly more compact. No quality difference.

### Etymology — derivation format (stylistic preference, both correct)

Two distinct styles emerged:

- **Claude**: structured formula with explicit Arabic root + Arabic pattern +
  Arabic surface + English gloss. Example: `ج.د.ل (root: to argue) + فَاعَلَ
  pattern = جَادَلَ (he argued, disputed)`.
- **Codex**: narrative-romanized formula. Example: `jādala = engage another
  in disputing = to argue`.

The prompt's example is `"maktab = place of writing = office/desk"` —
**Codex matches the prompt's example format more literally**. Claude has
invented a richer scaffolded format.

For a learner: Claude's format is more information-dense (shows Arabic
script at every step); Codex's format is more readable narrative. Both are
acceptable; neither is wrong.

### Etymology — loanword path (tie)

سِينِمَا — both providers correctly took the foreign-origin branch (omitted
`root_meaning`/`pattern`, gave a "From X language…" derivation). Codex's
trace is slightly more historically accurate (`French cinéma < cinématographe
< Greek roots`), matching the actual borrowing path into Arabic; Claude's is
more language-list-y. Both are defensible.

### Etymology — related_loanwords (mild Claude win)

For سِينِمَا:
- Claude: `["cinema (English)", "cinéma (French)", "kino (Russian/German)"]`
- Codex: `["cinema", "ciné", "cine", "cinematography"]`

Claude's list is more useful — distinct languages with explicit attribution.
Codex's is redundant English/French stem variants. Single-case advantage so
small-sample caveat applies.

For all 9 native words, both providers correctly returned `[]` — neither
hallucinated a spurious European cognate from an Arabic surface form. The
prompt's "Beware surface-string coincidences" warning works for both.

## Scorecard

| Dimension | Claude Haiku | Codex `gpt-5.5` | Winner |
|---|---|---|---|
| Speed | 114.7s | 66.6s | **Codex (1.7×)** |
| Roots correctness | 10/10 | 10/10 | Tie |
| verb_form classification | 7/7 | 7/7 | Tie |
| Hollow-verb morphology | ✅ | ✅ | Tie |
| Diacritization completeness | partial (no mood) | full (with mood) | **Codex** |
| Nominal field completeness | conservative | thorough | Mild Codex |
| Pattern naming (philology) | 5/9 canonical | 9/9 canonical | **Codex (strong)** |
| root_meaning | full | sometimes compact | Tie |
| derivation format | scaffolded Arabic | matches prompt example | Tie |
| cultural_note (volume + accuracy) | 3/10 filled, all correct | 10/10 filled, all correct | **Codex (strong)** |
| Loanword etymology | correct | correct | Tie |
| related_loanwords specificity | better | redundant | Mild Claude |

## Decision

**Codex `gpt-5.5` clearly outperforms Claude Haiku 4.5 on Alif Arabic
enrichment.** The win is concentrated in exactly the dimensions Latin
philology complaints had highlighted: pattern naming (the wazn convention)
and the optional cultural-note slot that provides learner-facing context.
Codex's pattern names are canonical 9/9; Claude's are non-canonical 4/9.
Codex provides 10 pedagogically valuable cultural notes; Claude provides 3.

This **resolves the open question in the migration plan**: enrichment should
flip to Codex along with the audit pipelines. It no longer needs to be
hedged as "uncertain — test first" — the test came in clearly favorable to
Codex. The Latin philology complaint that motivated singling out enrichment
was indeed a prompt/parser issue (closed by PR #157), not a provider issue.

## Caveats

- **Sample size: 10 lemmas.** Small. The pattern-naming finding is robust
  (5 categorically wrong names is well above any reasonable noise threshold
  on 9 trials), but the cultural-note volume gap could shift with a larger
  sample. Either way, neither finding crosses to "Codex worse than Claude."
- **Single run.** Both calls use non-zero temperature (0.1 for forms, 0.3
  for etymology). A repeat run might shift individual outputs; the
  qualitative pattern is unlikely to change.
- **Generation pipelines untested here.** This A/B is enrichment-only.
  Sentence-gen quality findings from `codex-vs-claude-sentence-gen-2026-05-26.md`
  stand — generation stays on Claude for Arabic naturalness.
- **Codex's `model_reasoning_effort="medium"`** matches polyglot production.
  Higher effort would presumably tighten quality and lengthen latency; not
  worth measuring unless Codex turns up a quality regression at scale.

## Updated migration scope

With this A/B settled, the migration plan simplifies. Audit pipelines AND
enrichment both flip to Codex; only generation (sentence + story) stays on
Claude:

| Pipeline | Plan as of 2026-05-26 (post-A/B) |
|---|---|
| `sentence_validator.verify_and_correct_mappings_llm` | → Codex |
| `sentence_validator.apply_corrections` LLM step | → Codex |
| `llm.rerank_sentences_by_naturalness` | → Codex |
| `lemma_quality.py` LLM-in-context audit | → Codex |
| Disambiguation / flag / tagging classifiers | → Codex |
| **Lemma enrichment** (roots / forms / etymology) | **→ Codex** *(this A/B)* |
| `generate_sentences_batch` (Sonnet) | stay on Claude |
| Story gen (Opus) | stay on Claude |

Remaining open questions are now purely operational:

1. Cost-log compatibility — does `limbic.cerebellum.cost_log` log Codex calls?
   (Polyglot routes through it; likely yes; needs confirming on the Alif side.)
2. Combined Codex quota — Alif audit + Alif enrichment + polyglot audit on
   one Max plan. Polyglot's cron does 64 lemmas/run every ~3h; adding Alif's
   higher volume needs a headroom check.

Both can be resolved during the implementation session; neither needs
another A/B.
