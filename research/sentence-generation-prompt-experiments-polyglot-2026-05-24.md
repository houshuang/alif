# Polyglot Sentence Generation Stabilization - 2026-05-24

Status: deployed  
System: Polyglot Modern Greek (`polyglot/`)  
Primary goal: make forward review sessions use fresh, high-quality generated sentences rather than repeated textbook/book sentences, while keeping assumed-known cognates out of retrieval-material generation.

## Executive Summary

The Polyglot sentence pipeline was corrected from "generate material for anything that looks active" to "generate material for actual retrieval targets only." The large raw active-state number was misleading: it included bulk-known and cognate-known words that should act as scaffold vocabulary, not as flashcard/retrieval targets. After the cleanup, production still has 1960 active-state rows, but only 149 are retrieval-eligible targets. The rest are mostly assumed-known no-card scaffolds.

The review-session picker now strongly prefers fresh generated sentences and avoids recently shown repeats. The generation pipeline now overgenerates, validates against the learner's engaged vocabulary, verifies lemmatization, runs a fail-closed quality gate, and only then caps per-target storage. The prompt was also rewritten to ask for grounded, worthwhile Modern Greek sentences rather than dictionary-like fragments or forced combinations.

Final production check after deployment:

| Metric | Final value |
|---|---:|
| Raw active-state rows (`acquiring`, `learning`, `known`, `lapsed`) | 1960 |
| Active non-content rows | 0 |
| Retrieval-eligible targets | 149 |
| Assumed-known no-card scaffold rows | 1808 |
| Targets still below `POLYGLOT_ACTIVE_TARGET=5` generated sentences | 98 |
| Active verified quality-passing generated LLM sentences | 429 |
| Next 30-card session source mix | 29 LLM, 0 textbook |
| Recently-shown selected in next session | 0 |
| Current deployed Polyglot commit | `1fec5e4` |

## User Problem Statement

The user identified several connected failures:

- "It should not have 1968 active content" - many of these were cognates or presumed-known words and should not trigger retrieval material.
- `para` / `παρά` should be treated as a function word.
- Book/textbook sentences were appearing too often, sometimes the same sentence repeatedly.
- Generated sentences should be high-quality: meaningful, literary, poignant, funny, surprising, nostalgic, beautiful, or otherwise worth reading.
- Claude and Codex should both be usable on Alif/Polyglot, with Claude as a working fallback.
- The system should be deployed and a sentence-generation backfill triggered.

## Relevant Merged PR Context

Recent PRs before this pass laid the groundwork:

| PR | Merged | Relevance |
|---|---|---|
| [#133](https://github.com/houshuang/alif/pull/133) | 2026-05-22 | Strict generated-over-book picker tier, lazy book-sentence translation, refresh menu |
| [#134](https://github.com/houshuang/alif/pull/134) | 2026-05-22 | Raised sentence-generation cron throughput |
| [#131](https://github.com/houshuang/alif/pull/131) | 2026-05-22 | Alif-mirrored Polyglot review UI; supports sentence-card review UX |
| [#124](https://github.com/houshuang/alif/pull/124) | 2026-05-22 | In-flight review session restore; prevents accidental session replacement on detail navigation |
| [#121](https://github.com/houshuang/alif/pull/121) | 2026-05-21 | Enrichment cleanup loop, relevant to keeping background material tasks healthy |
| [#115](https://github.com/houshuang/alif/pull/115) | 2026-05-21 | Quality-gate stamp hardening |

Direct follow-up commits on `main` completed this stabilization:

| Commit | Purpose |
|---|---|
| `ac767a27` | Target retrieval material correctly |
| `6849afea` | Correct retrieval target handoff counts |
| `c449e54d` | Prefer fresh generated review sentences |
| `7960bef9` | Skip non-content generated verification |
| `2ad9c86d` | Skip surface function words in verification |
| `16c4d575` | Improve sentence generation quality |
| `d7d31ce7` | Align generation function words |
| `1fec5e4e` | Document generation deployment status |

## Diagnosis

### 1. "Active content" was the wrong unit

The alarming count was not actually "1968 words needing generated review material." It was mostly `known` rows created by bulk-known/cognate-known flows. Those rows are useful as scaffold vocabulary but should not get dedicated retrieval cards unless the learner later marks them missed and they enter acquisition/FSRS.

The generation target query now means:

- include all `acquiring` rows,
- include `learning` / `known` / `lapsed` only when they have an FSRS card,
- exclude canonical variants,
- exclude function words, proper names, not-word rows, and glossless lemmas,
- count only active, verified, quality-passing `source="llm"` sentences toward coverage.

Final production distinction:

| Count | Meaning |
|---|---:|
| 1960 | Raw active states, including assumed-known scaffolds |
| 1808 | Assumed-known no-card scaffolds (`pre_known` / `cognate_known`) |
| 149 | Actual retrieval targets |
| 98 | Retrieval targets still below five generated sentences |

### 2. Function-word handling was split across layers

`παρά` had already been backfilled as a function word, but generation and validation were not fully aligned:

- quality/intake function-word sets knew about many function-ish Greek forms;
- generated sentence verification needed to ignore function-word correction nitpicks;
- the generation prompt's explicit allowed-function-word list did not yet include `παρά` and several other closed-class scaffolds.

This mattered because the LLM was otherwise forced either to use awkward content-word substitutes or to produce valid Greek that deterministic validation later rejected.

### 3. Candidate generation was cut too early

The generator asked for extra candidates, but earlier candidates could consume the target cap before later, better candidates reached the quality gate. That made overgeneration much less useful than it looked.

The new funnel carries all deterministic-valid extra candidates through:

1. generation,
2. deterministic known-pool validation,
3. lemmatization verification,
4. sentence-quality review,
5. per-target cap applied only after quality approval.

### 4. Prompt quality and validator safety were pulling in different directions

The LLM can generate more beautiful sentences when unconstrained, but the validator only accepts sentences whose target and non-target content words can be mapped to known lemmas. The prompt needed to ask for worthwhile prose while still making morphology validator-safe.

The final prompt emphasizes:

- complete standalone thoughts with finite verbs,
- grounded micro-scenes rather than list fragments or dictionary definitions,
- emotionally legible, literary, funny, nostalgic, or surprising sentences when possible,
- exact target surface forms where possible,
- explicit Greek agreement checks for adjectives and `-ομαι` / `-ουμαι` verbs,
- no surreal personification or random props,
- common scaffold words first, rare words sparingly.

### 5. Claude adapter was broken by CLI argument order

Claude Code was installed and authenticated on the server, but the Polyglot wrapper put `-p` before options. The deployed working order is now:

```text
claude --output-format json --model ... --json-schema ... -p prompt
```

Production still defaults to Codex (`POLYGLOT_LLM_PROVIDER=codex`, `POLYGLOT_CODEX_MODEL=gpt-5.5`), but Claude provider smoke now works.

## Implementation Changes

### Picker and review-session behavior

Files:

- `polyglot/app/services/sentence_selector.py`
- `polyglot/tests/test_sentence_selector.py`

Behavior:

- generated LLM sentences are preferred over textbook/page-of-record fallbacks;
- recently shown sentences are hard-skipped for normal sessions;
- textbook fallback is capped;
- picker still falls back gracefully when no generated material exists.

Final production selector check:

```text
active_target=5
next_session_sentences=29 intro_cards=0 skipped=4
sources=Counter({'llm': 29})
recently_shown=0
textbook=0
remaining_gaps_first_1000=98
```

### Retrieval-target material generation

Files:

- `polyglot/app/services/material_generator.py`
- `polyglot/scripts/warm_sentence_cache.py`
- `polyglot/tests/test_material_generator.py`

Behavior:

- generation gaps are based on retrieval-eligible targets, not every `known` row;
- cognate-known and bulk-pre-known no-card rows are scaffolding only;
- `POLYGLOT_ACTIVE_TARGET=5` is the generated-sentence target per retrieval target;
- textbook sentences do not count toward generated-material target coverage.

### Prompt and quality funnel

Files:

- `polyglot/app/services/material_generator.py`
- `polyglot/tests/test_material_generator.py`

Important constants:

```python
GENERATION_MIN_CANDIDATES_PER_TARGET = 8
GENERATION_EXTRA_CANDIDATES_PER_TARGET = 5
COMMON_SCAFFOLD_SAMPLE_SIZE = 180
```

Behavior:

- ask for `max(sentences_per_target + 5, 8)` candidates;
- enforce non-target content words against the learner's engaged scaffold vocabulary;
- keep full DB lookup only for mapping IDs;
- run deterministic validation before verifier/quality calls;
- run fail-closed quality review before storage;
- store only the requested number per target after quality approval.

### Greek lemmatization and function-word handling

Files:

- `polyglot/app/services/sentence_validator.py`
- `polyglot/app/services/lemma_quality.py`
- `polyglot/app/services/material_generator.py`
- `polyglot/tests/test_lemma_quality.py`
- `polyglot/tests/test_material_generator.py`

Changes:

- added conservative Greek surface fallback keys for generated/review sentence validation;
- common adjective and present verb forms can map back to citation lemmas when simplemma fails;
- examples covered by tests include `στρωμένο -> στρωμένος` and `συντελείται -> συντελούμαι`;
- verifier `wrong` verdicts on function words are ignored when the correction is a function word;
- prompt allowed-function list now includes `παρά`, crasis forms, `μόνον`, `κάπου`, `γύρω`, etc.

Production cleanup:

- legacy `παρά` and `λοιπόν` are `word_category="function_word"`;
- final cleanup retired eight active function-ish rows to `ignore`: `μέσα`, `επάνω`, `κάπου`, `κοντά`, `γύρω`, `μακριά`, `πάντα`, `μόνον`;
- final `active_noncontent=0`.

### LLM provider adapter

Files:

- `polyglot/app/services/llm_cli.py`
- `polyglot/tests/test_llm_cli.py`

Changes:

- all structured calls still route through `llm_cli.py`;
- fixed Claude CLI option order;
- Codex remains production default;
- Claude provider tested on server with a structured JSON smoke.

Production smoke:

```text
POLYGLOT_LLM_PROVIDER=claude ... call_structured_json(...)
=> {'x': 'ok'}
```

## Prompt/Pipeline Evaluation

### Local production-snapshot eval

Command shape:

```bash
DATABASE_URL=sqlite:////tmp/polyglot-current-eval-4.db \
  scripts/warm_sentence_cache.py --language el --max-lemmas 8 --sentences-per-target 2
```

Result:

| Metric | Value |
|---|---:|
| Sentences stored | 14 |
| Targets covered | 7 / 8 |
| Failed target | 716 (`σχεδιαστικός`) |

The miss was a hard adjective/collocation target, not an adapter or validation failure.

### Production warm run 1

Run id: `73564479`  
Commit family: `16c4d575` generation-quality changes  
Scope: `--max-lemmas 32 --sentences-per-target 2`

Result:

| Metric | Value |
|---|---:|
| Gap count | 32 |
| Generated | 43 |
| Words covered | 25 |
| Words failed | 7 |

Failed lemma IDs: `716`, `736`, `752`, `1296`, `820`, `969`, `227`.

### Production warm run 2

Run id: `d9b16148`  
Commit family: `d7d31ce7` prompt/function-word alignment  
Scope: `--max-lemmas 16 --sentences-per-target 2`

Result:

| Metric | Value |
|---|---:|
| Gap count | 16 |
| Generated | 17 |
| Words covered | 10 |
| Words failed | 6 |
| Elapsed | 492.5s |

Failed lemma IDs: `699`, `716`, `480`, `752`, `1296`, `957`.

The quality gate rejected bad candidates for good reasons. Examples from the production log:

- forced/awkward naturalness: harvest sentences that were literal but implausible;
- unknown scaffold words such as `σιωπή`, `τόπος`, `κήπος`, `βιβλία`, `ζητώ`;
- wrong morphology such as `πριν ανοίγω` where natural Greek wants `πριν ανοίξω`;
- surreal/forced uses such as a person literally "branching" with `διακλαδίζομαι`.

This is the desired behavior: low-quality candidates should die before storage.

## Final Production State

Collected after deploy of `1fec5e4`:

```text
state_counts={'acquiring': 102, 'encountered': 33, 'ignore': 110, 'known': 1833, 'learning': 25}
active_all_states=1960 active_noncontent=0
retrieval_eligible_targets=149 assumed_known_no_card=1808 gaps_below_active_target_5=98
active_verified_quality_llm=429
next_session_sentences=29 intro_cards=0 skipped=4 sources=Counter({'llm': 29}) recently_shown=0 textbook=0
```

Server status:

```text
/opt/alif/polyglot git HEAD: 1fec5e4
GET http://127.0.0.1:3002/ => {"app":"polyglot","version":"0.1.0"}
No warm_sentence_cache.py / codex exec process running.
Production env: POLYGLOT_LLM_PROVIDER=codex, POLYGLOT_CODEX_MODEL=gpt-5.5.
```

## Tests and Verification

Focused:

```bash
arch -x86_64 .venv/bin/python -m pytest \
  tests/test_material_generator.py \
  tests/test_lemma_quality.py \
  tests/test_llm_cli.py
```

Result:

```text
55 passed
```

Full Polyglot suite:

```bash
arch -x86_64 .venv/bin/python -m pytest
```

Result:

```text
292 passed, 2 deselected, 108 warnings
```

Production checks:

- deployed commit matches `origin/main`;
- backend health OK;
- Claude structured JSON provider smoke OK;
- next-session selector mix OK;
- generated sentence stock increased to 429;
- no stale warm process left running.

## What Is Solved

1. **The 1968/1960 raw active count no longer drives generation.** It is documented as raw state, not retrieval-material need.
2. **Assumed-known cognates do not get generated retrieval material unless missed later.**
3. **Function words such as `παρά` are not review targets.**
4. **Book/textbook sentences are no longer expected to dominate normal review sessions.** Final selector check had 0 textbook rows.
5. **Repeated recently-shown sentences are excluded from normal sessions.**
6. **Generation produces enough useful material to keep forward sessions LLM-first.**
7. **Quality gate rejects forced, unnatural, mistranslated, or morphology-bad sentences before storage.**
8. **Claude and Codex providers both work on the Alif host; Codex remains the default.**

## Remaining Risks and Follow-Ups

### Hard targets will still fail often

Words like `σχεδιαστικός`, `σφηνοειδής`, `διακλαδίζομαι`, or domain-specific nouns can be genuinely hard to use naturally with the current known pool. A 20-40% acceptance rate can be healthy if the stored output is good.

Follow-up: add per-lemma generation backoff or failure classification if the same hard lemmas consume too much cron budget.

### The prompt still sometimes reaches outside the known pool

The deterministic gate catches this, but logs show common unallowed words such as `σιωπή`, `θέλει`, `μικρό`, `γυρίζω`, `βλέπει`, `παιδί`. This may indicate the high-utility scaffold list should be expanded only when the learner actually knows those lemmas, or the prompt should more strongly prefer the first 180 scaffold items.

Follow-up: analyze unknown-word rejects after the next 2-3 cron runs; do not relax the validator.

### Active-state stats remain visually confusing

Production has 1833 `known` rows, but most are no-card assumed-known scaffolds. That is correct for review behavior, but UI/stats language should distinguish:

- assumed-known scaffold,
- retrieval-active,
- FSRS-known.

Follow-up: add a stats field for `retrieval_eligible_targets` and `assumed_known_no_card` if the frontend keeps surfacing raw active counts as "active content."

### Cron cost and timeout need observation

The new funnel asks for 8 candidates per target and quality-gates more survivors. It is heavier but yields better stored material.

Follow-up: after 24h, inspect:

- generated sentences per cron pass,
- quality approval ratio,
- targets covered per pass,
- timeout exits in `/var/log/polyglot-update-material.log`,
- remaining `gaps_below_active_target_5`.

## Operational Notes

- The stale pre-deploy generation process was killed so old prompts would not continue writing rows.
- Manual production warms were bounded and checked after completion.
- One local SSH wrapper hung after the remote process finished; production logs confirmed completion and no server process remained.
- Documentation updated in `polyglot/CLAUDE.md` and `polyglot/NEXT_SESSION.md`.

## Cross-References

- [Experiment log](experiment-log.md)
- [Sentence Generation Prompt Experiments - 2026-05-04](sentence-generation-prompt-experiments-2026-05-04.md)
- [Stale PR / Idea Triage - 2026-05-22](analysis-2026-05-22-stale-pr-ideas.md)
- [Polyglot project rules](../polyglot/CLAUDE.md)
- [Polyglot next-session handoff](../polyglot/NEXT_SESSION.md)
