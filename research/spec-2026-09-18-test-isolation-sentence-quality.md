# Spec: hermetic tests, coherent review sentences, and next steps

**Date:** 2026-09-18 · **Status:** proposed, nothing implemented · **Base:** `main` at `bb52d7e`
(production still runs `f901a1b`; maintenance amendments #271 and #272 are merged but not deployed)

**Why this exists.** On 2026-09-17 the learner reported that review debt hovers at 600–700
whatever the effort, that many review sentences are strange, and that new words have stopped.
The debt and intake parts were addressed by maintenance v1.1/v1.1b (experiment log,
2026-09-17). This spec covers what remains: the review sentences themselves, a test suite that
turned out to call real LLM providers, and the order of work after that.

Evidence for every number below is in [`spec-2026-09-18/`](spec-2026-09-18/):
[`sentence-sample.json`](spec-2026-09-18/sentence-sample.json) (60 rated sentences plus
population statistics), [`test-provider-leaks.json`](spec-2026-09-18/test-provider-leaks.json)
(every intercepted provider call), and the recorder used to collect it,
[`llm_leak_recorder.py`](spec-2026-09-18/llm_leak_recorder.py).

---

## Workstream A — make the fast test suite hermetic

### Problem

`pytest` (the fast suite, `-m 'not slow'`) is supposed to make no LLM calls; the `slow` marker
is documented as "tests that make real LLM calls". On a developer machine it makes many. With
the recorder plugin loaded, which blocks and logs every `claude`/`codex` spawn and every
non-local socket, the suite still passed 2,066/2,066 while logging **396 provider calls from
127 tests in 28 files**:

| Target | Calls |
|---|---:|
| `api.openai.com` (API fallback chain) | 294 |
| `codex` CLI (the `claude_haiku` audit alias) | 93 |
| `claude` CLI | 8 |
| `raw.githubusercontent.com` (litellm cost map fetched at import) | 1 |

Every test still passing means none of them depend on these calls; they are side effects.
The main sources:

- **Lemma enrichment** (`lemma_enrichment.enrich_lemmas_batch`), usually from daemon threads
  started by `lemma_quality.py:387`, `root_enrichment.py:124` and `pattern_enrichment.py:145`
  when a test creates vocabulary. These threads can outlive their test. One printed
  `no such table: roots` after the temporary database had already been removed.
- **OCR and import paths**: `ocr_service.process_textbook_page` and
  `_schedule_material_generation`, `story_service.import_story`,
  `book_import_service.create_book_sentences`.
- **Generation and review**: `material_generator.generate_material_for_word` and
  `llm.review_sentences_quality`.
- **Memory hooks** started on failed reviews (`fsrs_service.py:277/282`,
  `acquisition_service.py:922/927`).

By file, most calls come from `test_ocr.py` (132), `test_story_service.py` (95),
`test_sentence_selector.py` (36), `test_reintro.py` (20), `test_simulation.py` (13),
`test_sentence_review.py` (12) and `test_sentence_validator.py` (11). Another 12 happened
while no test was running: threads outliving their test, plus the import-time fetch.

**Consequences.** The login shell exports `OPENAI_API_KEY`, and `claude` and `codex` are on
PATH, so a plain `pytest` spent real quota and money. A suite documented at about 2 minutes ran
for 30+ minutes, blocked on HTTPS. Inside the Bash sandbox the same calls hang on blocked
network instead, which looks exactly like a hung test.

Two smaller defects surfaced at the same time:

- `tests/test_analyze_learning_system.py` launches `BACKEND_DIR/.venv/bin/python`. That path
  does not exist in a git worktree, so 8 tests fail there.
- `tests/test_mapping_rescue.py` has 12–34 s setup and teardown phases (the slowest in the
  suite). This is likely lock waits against leftover background threads; not yet confirmed.

### Requirements

**A1. Fail-closed provider guard.** Add a session-scoped, autouse guard in
`backend/tests/conftest.py`, installed before any test runs. It replaces the provider boundary,
not individual callers:

- `llm._generate_via_claude_cli`;
- `llm._generate_via_codex_cli_with_logging` and the `codex_cli` runner;
- the litellm or API completion call that `generate_completion` falls back to;
- `claude_code.generate_structured` and `generate_with_tools`.

Each replacement raises the module's own failure type (`LLMError`/`AllProvidersFailed`), so
tests exercise the production failure paths ("verification failure ≠ success", skip or retry)
instead of a novel exception. The guard records caller, test id and thread, and exposes them to
tests that assert "no provider call was attempted". Tests marked `slow` opt out.

**A2. Network backstop.** In the fast suite, block `socket.getaddrinfo` and
`socket.create_connection` for any host other than `localhost`, `127.0.0.1`, `::1` and
`testserver`. The error must name the test and host. Set `LITELLM_LOCAL_MODEL_COST_MAP=True`
before importing the app so litellm stops fetching its cost map. This catches Gemini, ElevenLabs
and any future provider the A1 list misses.

**A3. Environment scrub.** Before the app is imported, `conftest.py` deletes
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_KEY` and `GEMINI_KEY`. The
`slow` job restores them explicitly, so no test can silently inherit paid credentials.

**A4. Background threads.** The fast suite must not leave provider-bound daemon threads
running across tests. Preferred approach: one test-only switch makes the thread-spawning
helpers (enrichment, root/pattern enrichment, memory hooks) either no-ops or run inline under
the A1 guard. This mirrors how `conftest.py` already turns `BackgroundTasks.add_task` into a
no-op. Tests that need the enrichment behaviour call the service directly with a mocked
provider.

**A5. Worktree-safe subprocesses.** Tests that launch Python use `sys.executable`, never
`BACKEND_DIR/.venv/bin/python`.

**A6. Speed budget.** Diagnose the `test_mapping_rescue.py` setup/teardown time after A4
lands, since A4 is the likely cause. Target: a full fast run under 90 s on the development Mac.

### Acceptance

- The recorder plugin in `spec-2026-09-18/` reports **0 intercepts** for the fast suite.
  Promote it into a committed opt-in pytest plugin, or fold it into A1/A2, so this stays
  checkable.
- A dedicated test proves the guard is active: calling `generate_completion` in a fast test
  raises the guard's failure type, and a raw socket connect to an external host is refused.
- `pytest` passes from a git worktree with API keys exported and both CLIs on PATH, and makes
  no provider call.
- The `slow` suite still reaches real providers when explicitly selected.

**Not in scope:** rewriting the 127 affected tests one by one (the guard makes them hermetic),
and changing any production fallback behaviour.

---

## Workstream B — coherent review sentences

### Problem and evidence

Two cards the learner flagged, both generated and both passed by the quality gate:

- «المُؤَخَّرَةُ طِيزٌ، وَعَقَّبَ زَعْلَانُ بِتَأْنِيبٍ» — "The rear is an ass, and the upset one followed up
  with reproach." One acquiring target (عَقَّب) plus four known words, all due when the card
  was shown: مُؤَخَّرَة (rank
  71,368), طِيز (vulgar, unranked, imported via Dragoman), زَعْلان (colloquial, rank 53,130),
  تَأْنِيب (unranked). The gate's verdict: "vulgar and odd, but it could plausibly be said."
- «اِعْتَدَلَ مُتَثَاقِلٌ حَائِرٌ، ثُمَّ قَبَضَ عُودًا وَسَالَمَ غَاضِبًا» — "A puzzled sluggish person straightened
  up, then seized a piece of wood and made peace with an angry one." Six of its seven content
  words are unranked or ranked beyond 17,000 in the lemma frequency table, and two were lapsed.

**Population: 384 sentences reviewed in reading mode between 4 and 18 September.**

- 18.5% of content words are rare (effective rank over 5,000, or unranked).
- 149 sentences (39%) contain two or more rare words; 55 (14%) contain three or more.
- 83 (22%) have two or more target words.
- The last recorded comprehension is "understood" for 58% of sentences with no rare word,
  41% with one, and 34–35% with two or more. This is confounded, because rare words also tend
  to be the fragile ones, but it shows the reading burden.

**Rated sample: 60 of those sentences, seed 20260918, one rater (Claude), mostly judged from
the English gloss.**

| Rating | Count | Mean rare words | Share with ≥2 rare | Share multi-target |
|---|---:|---:|---:|---:|
| Natural | 33 (55%) | 0.70 | 18% | 18% |
| Forced but coherent | 20 (33%) | 1.55 | 60% | 20% |
| Word salad | 7 (12%) | 2.14 | 57% | 14% |

Rare-word density separates the classes; target count does not. For comparison, **1,609 of the
1,614 active generated sentences carry `quality_natural = true`.**

### Root causes

1. **The prompt vocabulary is skewed toward awkward words.** Batch generation gives the model a
   closed list ("you may ONLY use these Arabic content words"). `sample_known_words_weighted`
   draws 500 of roughly 2,900 active lemmas. Each weight is `1/(1 + existing sentence count)`,
   multiplied by the at-risk boost (lapsed or recently missed ×3, acquiring ×2.5, stability
   under 14 days ×2, 14–45 days ×1.5) and randomly jittered. Words are then **sorted by
   descending weight**, and `format_known_words_by_pos` keeps that order within each POS group.

   Rare words tend to have few sentences, plausibly because they are hard to use, so they keep
   the highest weights. They are listed first, and fragile rare words get boosted further. This feeds
   itself: an obscure word stays obscure, and keeps being offered.

   **Hypothesis to test in B1:** the model favours the top of each list.

2. **Selection rewards density.** Session scoring grows with `due_coverage^1.5`, relaxes
   freshness and diversity penalties for due-dense sentences, and assembles most of each
   session by greedy cover. The `session_start` of 17 September logged 9 of 12 cards as
   `greedy_cover`. The four-due-word ceiling bounds density, but the score still prefers cards
   that reach it.

3. **The quality gate is binary and lenient.** Its rubric already rejects "forced word-list
   combinations", yet a single yes/no verdict from the audit model accepts most of them.
   "Could plausibly be said in any context (… poetry)" gives the model an easy escape.

4. **Register is unknown.** `Lemma.register` and `dialect` are NULL for 2,837 of 2,859 active
   lemmas. Vulgar and colloquial words imported from Bookifier or Dragoman, such as طِيز and
   زَعْلان, are indistinguishable from MSA scaffold.

### Changes, in order of leverage

**B1. Rarity-balanced prompt vocabulary** (`sentence_generator.py`, `material_generator.py`).

- Stratify the 500-word sample: at least 80% from effective rank ≤5,000, and at most a small
  fixed quota of unranked or rank >20,000 words.
- Keep the at-risk boost, but apply it within strata, so fragility cannot pull rare words into
  the common pool.
- Shuffle within each POS group instead of listing by descending weight.
- Mark uncommon words in the prompt and instruct: at most one uncommon non-target word per
  sentence.

Target words are exempt, so a rare word can still get its own sentence.

**B2. Deterministic rarity pre-gate** (validation, before LLM review). Reject a generated
sentence with more than one rare non-target content word. Rarity uses the resolved lemma's
effective rank, classified through the hardened mapping path, as CLAUDE.md requires. This is
cheap and needs no LLM. On lemma-table ranks it would have rejected both flagged sentences
(four and five rare non-target words).

**B3. Graded coherence review** (`review_sentences_quality`). Replace the single `natural`
boolean for coherence with a 1–5 score and a required one-line realistic context: "where would
a native speaker write this?" Require a score of at least 4 and persist the score. Keep the
grammar and translation checks as they are.

Calibrate before enabling on the rated sample in `sentence-sample.json`, extended with about
20 sentences the learner spot-checks. Enable only if the gate:

- rejects at least 80% of the forced and word-salad items; and
- passes at least 90% of the natural ones.

**B4. Selection preference for coherent cards** (`sentence_selector.py`; requires the Rule 8
gate audit).

- Feed the persisted coherence score into the existing `quality_multiplier`.
- Penalize a candidate carrying two or more rare scaffold words that are not due.
- Keep the four-due-word ceiling unchanged.

No LLM call may be added to `build_session`.

**B5. Register backfill.** One bounded Codex batch classifies register (neutral, literary,
colloquial, vulgar) for active vocabulary. Colloquial and vulgar words may still be targets of
their own sentences, but are excluded from other sentences' scaffold vocabulary.

Words the learner deliberately added through Bookifier keep their curriculum status; this only
stops them appearing incidentally.

**B6. Retire existing bad inventory.** Run the B2 pre-gate and the B3 review over the 1,614
active generated sentences and deactivate failures, but only when each affected due or
acquiring word keeps at least one reviewable sentence. Otherwise queue replacement generation
first through the single verified pipeline. Reuse the existing reverify and salvage patterns,
with compare-and-set writes.

### Invariants that bind this work

- All generation still goes through `generate_material_for_word` / `batch_generate_material`.
- Session build stays DB-only and under 1 s.
- Every reviewed word keeps its evidence.
- Coverage must not regress: the due-coverage deficit that `refill_due_deficit.py` reports may
  not rise.
- **Relation to the maintenance experiment:** B1–B6 change card content, which is part of the
  experiment's feasibility question. Record each deployment as a phase boundary in the
  experiment log.

### Evaluation

Re-run the same rating protocol on a fresh 60-sentence sample of reviewed cards two active
weeks after B1–B3 ship:

| Measure | Baseline | Target |
|---|---:|---:|
| Natural share | 55% | ≥80% |
| Word-salad share | 12% | ≤3% |
| Mean rare words per reviewed sentence | 1.25 | ≤0.8 |
| Share of sentences with ≥2 rare words | 39% | ≤15% |
| Last comprehension "understood" | 44% | report the change; confounded, not a target |
| Due-coverage deficit | current `refill_due_deficit` count | no increase |

---

## Next steps, in order

1. **Deploy maintenance v1.1 and v1.1b** (backend restart, no migration), when the learner
   agrees. Deploying both together gives the experiment one boundary.
   - **Monitor the known conflict.** The 2026-09-15 refresh flagged older-word recognition at
     77.2% after ≥7 days and 69.6% after ≥14 days, both below the protocol's floors. The 90%
     target lengthens intervals, while the evidence says lateness, not the target, drives the
     loss.
   - **Rollback triggers for v1.1b alone:** FSRS recall within three days of due below 85% over
     at least 150 reviews, or older-word ≥7-day clean falling below the September 15 level.
2. **Workstream A.** One PR, before any further algorithm work, so every later change can be
   tested safely and cheaply.
3. **Workstream B, in phases:**
   - B1 + B2 (generation side, one PR);
   - B3 with calibration (one PR);
   - B6 retirement run (an operation with its own dry run and backup);
   - B4 and B5 (each after a gate audit).
4. **Box-1 starvation diagnosis.** فَرَغَ, رَاوَدَ and مُقَلِّدَة stayed due for weeks while
   each had reviewable sentences that were never shown. Replay `build_session` on a snapshot to
   find which score or gate rejects them. Candidates: the due-density ceiling counted against
   the full due stock, comprehensibility, and the unknown-scaffold cap. v1.1 stops them
   blocking intake; it does not get them served.
5. **Day-30 maintenance checkpoint (2026-10-03).**
   - Standard analyzer, split at the v1.1/v1.1b boundary.
   - Add: suspended-leech queue size, words excluded from Box 1 as unserved (a warning above
     ten), true-new starts per day, and the share of due reviews more than 14 days late.
6. **Intake after the backlog turns.** Once actionable Box 1 stays under 5 and strict main
   debt trends down for two weeks, reconsider the true-new cap of 2/day. The 2026-09-17
   estimate suggests room for about 2–4 per day at roughly 40 cards/day. Decide on measured
   arrivals, not the estimate.
7. **Reading bridge.** The Momo restart portions prepared on 2026-09-15 remain the route
   toward the actual goal of independent reading; the items above serve it. Nothing here
   changes that plan.
8. **Vocabulary data quality** (feeds the existing frequency-core rebuild initiative in
   `IDEAS.md`).
   - Frequency-core mislinks give obscure words high priority: دَنّ "wine jug" has core rank
     816 and خَمَّ "to rot" 1,914.
   - At least one gloss is wrong: ذَكَرِيّ glossed "memory".

## Decisions for the learner

- **Deploy timing** for v1.1 and v1.1b.
- **Scaffold policy for colloquial and vulgar words (B5):** keep them only as their own
  targets, or allow them as scaffold when the learner added them on purpose.
- **Retirement tolerance (B6):** whether a temporary rise in regeneration work is acceptable to
  remove the worst existing cards quickly.
- **Spot-check:** about 20 sentences, to confirm or correct the single-rater labels before B3's
  thresholds are fixed.
