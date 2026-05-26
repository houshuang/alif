# Polyglot Latin — orthography migration + open Latin issues

**Status:** plan, not executed. Written 2026-05-26 to be picked up in a fresh
Claude session with no prior context. Self-contained: read this top-to-bottom
and execute in order.

## Background

The polyglot backend serves Modern Greek (primary), Ancient Greek, and Latin.
Latin shipped to prod 2026-05-25 (PR #140). Yesterday's session generated an
"LLPSI Familia Romana — Coverage Reader" (Story id=3 on prod) — 35 short
Latin paragraphs targeting LLPSI vocab chapter-by-chapter, ~97% coverage in
5,231 Latin words. Seeded into the polyglot reader via
`polyglot/scripts/seed_llpsi_coverage_story.py` from the JSON dump at
`research/polyglot-llpsi-coverage-2026-05-26.json` (local only — not in git).

The user opened the reader, hit several issues, and we want a clean
resolution before they read further.

## Three Latin issues, in priority order

### Issue 1 — Orthography mismatch (CONFIRMED, action needed)

**Symptom.** The seeded chapter 1 of the LLPSI coverage reader reads:
> *In capitulō primō Mārcī pensum est. Quid est in pensō? In pensō sunt
>  multa uocabula…*

Two problems:
1. **Macrons** on chapter 1 (`capitulō, primō, Mārcī, pensō, Latīnae`). The
   generation prompt explicitly said "no macrons" but Codex slipped them in
   only on chapter 1. Chapters 2–35 are macron-free.
2. **u-spelling** throughout (`uocabula, uir, iuuenis`). This was on purpose:
   `polyglot/CLAUDE.md:26` codifies "Display policy (Latin): one convention —
   **no macrons, u/i orthography**, 1sg/nominative lemma." The generation
   prompt asked for "use u not v; use i not j" to honor this.

User chose to flip the convention. **New convention: modern reading — no
macrons, v/j orthography** (`Marcus, vocabulum, vir, iuvenis` — or with j:
`Julius, juvenis`). User confirmed via AskUserQuestion 2026-05-26.

**Why this is non-trivial.** The LLPSI source TSV (`polyglot/data/vocab/
llpsi_fr.tsv`) already has v/j orthography (`vocabulum`, `vir`, `via`,
`navis` — see `grep -E '^(vir|vocabulum|navis)' polyglot/data/vocab/
llpsi_fr.tsv`). But `polyglot/scripts/import_latin_vocab.py` runs each entry
through LatinCy at import time, and LatinCy normalizes to u-spelling. So the
DB stored u-folded forms (`uir`, `uocabulum`). The polyglot convention was
chosen *because LatinCy emits u-spelled lemmas* — flipping the convention
means decoupling the display form from LatinCy's output.

The lookup key (`lemma_bare`) stays u-folded — that's how matching works
regardless of input orthography. `_normalize_latin` in
`polyglot/app/services/languages/la.py:56` is the right boundary: it always
strips macrons and folds v→u, j→i for the matching key. We keep that. The
*display form* (`lemma_form`) is what changes.

### Issue 2 — Bad philology on new Latin words (REPORTED, needs investigation)

**Symptom (user report 2026-05-26):** "the new words did not generate any
good philology notes"

**Context.** During yesterday's review session the user tapped 7 red Latin
lemmas: `excidium, exiguus, latrocinor, incrementum, fere, ullus, exordium`.
Each became an `acquiring` lemma with an intro card. The intro card pulls
from `Lemma.enrichment_json` (populated by `polyglot/app/services/
lemma_philology.py:batch_enrich` — runs as cron phase 5).

**Hypotheses, in priority order:**

1. **Enrichment hasn't run for these lemmas yet.** Cron phase 5 runs every 3h
   at xx:45. The lemmas were created at 06:51 UTC; the next cron was 09:45.
   The user may have read before enrichment fired. Check the prod DB:

   ```python
   # On alif server
   sqlite3 /opt/alif/polyglot/polyglot.db "
     SELECT l.lemma_form, l.enrichment_status, l.enriched_at,
            length(coalesce(l.enrichment_json, ''))
       FROM lemmas l
      WHERE l.language_code='la'
        AND l.lemma_bare IN ('excidium','exiguus','latrocinor',
                              'incrementum','fere','ullus','exordium')
      ORDER BY l.lemma_form;
   "
   ```

2. **Codex (production polyglot LLM provider) produces weaker enrichment
   than Claude.** The 2026-05-26 sentence-gen A/B suggested Codex is slightly
   weaker on the long-form descriptive prose that enrichment leans on.
   `lemma_philology.py` has its own prompt — could be model-quality or could
   be prompt issue. Investigate by re-running enrichment for one Latin lemma
   under both providers and diffing. See `research/alif-codex-migration-plan-
   2026-05-26.md` — the migration plan explicitly flags enrichment as the
   "test first before flipping" pipeline.

3. **The prompt is tuned for Greek and produces thin Latin outputs.** Check
   `lemma_philology.py` for language-specific branches; the Greek enrichment
   was the original target and Latin support was added 2026-05-25.

### Issue 3 — 3-word sentence translation, no comma (REPORTED, needs investigation)

**Symptom (user report 2026-05-26):** "the translation is just three words
with no comma etc"

**Context.** The review card the user saw served a sentence from the Eutropius
story (because the picker was falling back to textbook sentences for acquiring
lemmas — **this has since been fixed in PR #155, deployed**). The translation
is `Sentence.translation_en`, filled by the cron's translate phase
(`material_generator.translate_sentences_batch`, called by phase 4 of the
polyglot cron). For book sentences, translation is lazy — null at harvest
time, filled by the cron later.

**Hypotheses:**

1. **The sentence is short and the translation is faithfully short.** Some
   Eutropius sentences are genuinely 3-word maxims. Verify by pulling the
   actual sentence + translation:

   ```python
   sqlite3 /opt/alif/polyglot/polyglot.db "
     SELECT s.id, s.text, s.translation_en, s.source, s.page_id
       FROM sentences s
       JOIN pages p ON p.id = s.page_id
       JOIN stories st ON st.id = p.story_id
      WHERE st.language_code='la' AND st.title LIKE 'Eutropius%'
        AND s.translation_en IS NOT NULL
      ORDER BY s.id
      LIMIT 20;
   "
   ```

2. **The translation prompt produces stilted/literal English** — model-aware
   issue.

3. **A specific edge case (heading, fragment, page-boundary)** that confused
   the translator. `sentence_harvest.py` excludes page-boundary fragments but
   the rule may not catch everything.

The user's complaint is now historical (book sentences won't be served to
acquiring lemmas after PR #155). But the underlying translation quality is
still worth investigating because once the LLM-generated LLPSI coverage
texts get translation cron'd, the same machinery applies.

---

## What's already deployed (don't redo)

- **PR #154** (frontend) — reader revisit button (Know all / Next / Update).
  Merged + deployed. The Latin Eutropius story shows the right labels.
- **PR #155** (backend) — no textbook fallback for acquiring lemmas. Merged
  + deployed. Acquiring lemmas now get LLM sentences or get skipped from the
  session.
- **Latin cron** — running every 3h at xx:45 UTC. The 7 acquiring lemmas
  from yesterday's red taps will get LLM sentences on the 09:45 pass.
  `POLYGLOT_LANGUAGES=el la` confirmed in crontab.
- **LLPSI Coverage Story** — seeded on prod as Story id=3, 35 pages, 5231
  Latin words. Currently u-spelled with macrons on ch1 (the orthography
  issue this plan fixes).
- **Local `polyglot/.env`** sets `POLYGLOT_LLM_PROVIDER=codex` so future
  polyglot LLM work defaults to Codex (free via subscription, matches prod).
- **MEMORY pointers** for the relevant feedback:
  - `feedback_codex_cli_free.md` — Codex CLI is free; default for polyglot.
  - `feedback_no_book_sentences_for_acquiring.md` — durable preference.

---

## Execute (this is the plan)

### Phase 1 — Orthography migration (the user's "Latin fixes first" priority)

Goal: by end of Phase 1, the user can open the LLPSI coverage reader and see
v/j-spelled, no-macron Latin text. Lookup cards show matching display forms.
Re-doable for the Eutropius story as a follow-up if needed.

**Step 1.1 — Update the polyglot Latin convention in code + docs**

Files:
- `polyglot/CLAUDE.md:26` — change "no macrons, u/i orthography" → "no
  macrons, **v/j orthography** (consonantal v, consonantal j; vocalic u and
  i unchanged)". Note the choice was made 2026-05-26 by the user.
- `polyglot/app/services/languages/la.py:56` — keep `_normalize_latin` as
  the **lookup key** function (still strips macrons + folds v→u + j→i — this
  is the matching key, deliberately convention-agnostic).
- `polyglot/app/services/languages/la.py:159` — `lemmatize` currently
  returns `LemmaCandidate(lemma=match.lemma_, ...)` where `match.lemma_` is
  LatinCy's u-spelled output. Add a **display transformer**: convert
  LatinCy's u→v / i→j for consonantal positions before returning. The
  transformer is the hard piece; see Step 1.2.
- New: write `_to_modern_reading_orthography(form: str) -> str` in
  `la.py`. Used by both lemmatize() and the migration script.

**Step 1.2 — The u→v / i→j transformer**

The rule for converting LatinCy's u-spelled output to v/j orthography:
- Replace `u` with `v` when followed by a vowel AND preceded by either word
  start OR a consonant OR another vowel that doesn't form a known vocalic
  digraph (`au, eu`). Eg. `uir` → `vir`, `uocabulum` → `vocabulum`,
  `iuuenis` → `iuvenis` then `juvenis`. NOT `puella` → `pvella` (the u is
  vocalic between consonant `p` and vowel `e`, but the syllable structure
  `pu-el-la` makes the `u` vocalic).
- Similarly for `i` → `j`: when followed by a vowel AND at word start OR
  after a consonant, treat as consonantal: `iulius` → `julius`, `iam` →
  `jam`. Keep `i` when vocalic.

**The hard part:** Latin has no reliable syllable boundary marker in
written form. The cleanest approach is a heuristic + lemma-level override
list for known irregulars. Suggested implementation:

```python
_VOWELS = set("aeiouāēīōūȳ")  # post-macron-strip we won't see these but keep robust
_CONSONANT_AFTER_CONSONANT_OK = set("aeiou")

def _to_modern_reading_orthography(form: str) -> str:
    """Convert LatinCy's u-spelled (u/i) form to modern v/j orthography.

    Heuristic: u→v and i→j when consonantal (before a vowel, and either
    word-initial or post-consonant). Preserves vocalic positions.
    Lemma-level overrides handle known irregulars (a small map below).
    """
    # Override map for forms the heuristic gets wrong. Keep small —
    # add only when verified against a Latin dictionary.
    OVERRIDES = {
        # ...populate as needed...
    }
    if form in OVERRIDES:
        return OVERRIDES[form]
    chars = list(form)
    for i, ch in enumerate(chars):
        if ch not in ("u", "i"):
            continue
        if i + 1 >= len(chars):
            continue
        next_ch = chars[i + 1].lower()
        if next_ch not in _VOWELS:
            continue
        prev_ch = chars[i - 1].lower() if i > 0 else ""
        # Word-initial OR post-consonant → consonantal
        if prev_ch == "" or prev_ch not in _VOWELS:
            chars[i] = "v" if ch == "u" else "j"
    return "".join(chars)
```

**Validate the transformer.** Test cases (add to
`polyglot/tests/test_la_provider.py` or a new test file):
- `uir` → `vir` ✓
- `uocabulum` → `vocabulum` ✓
- `iuuenis` → `juvenis` ✓ (note: TWO transforms — `i`→`j` and `u`→`v`)
- `iulius` → `julius` ✓
- `puer` → `puer` (vocalic u between consonant p and vowel e — but it's at
  syllable start, so heuristic would say consonantal. ❌ heuristic fails)
- `puella` → ❌ similar — heuristic would output `pvella`

**Risk:** the heuristic gets some words wrong. Plan: build a verification
list from the TSV (which has v/j-spelled forms) and check every LLPSI lemma
the heuristic produces. Mismatches go into the OVERRIDES map.

Better practical approach: **don't run the heuristic on LLPSI-sourced
lemmas at all.** Take the v/j form straight from the TSV. Run the
heuristic only for *novel* lemmas created via reading_intake (where the
TSV doesn't help). For reading-intake lemmas, build an override list as
they're created.

**Step 1.3 — Migrate existing DB lemma_form values to v/j orthography**

Three sources of Latin Lemma rows:
- `source='llpsi'` — re-import from `polyglot/data/vocab/llpsi_fr.tsv` (has
  v/j orthography natively, no canonicalization needed)
- `source='roma_aeterna'` — check `polyglot/data/vocab/roma_aeterna.tsv` for
  orthography; if v/j, re-import; if u-spelled, apply the heuristic + manual
  audit
- `source='frequency_core'` (DCC) — DCC's frequency list is v/j-spelled
  per https://dcc.dickinson.edu (verify). Re-import from source file if
  available; else apply heuristic.

Write `polyglot/scripts/migrate_latin_lemma_orthography.py`:
1. Read TSV files
2. For each Latin Lemma row, look up by `lemma_bare` in the appropriate
   source TSV and update `lemma_form` to the TSV's spelling
3. For lemmas without a matching TSV entry, apply the heuristic + log for
   manual review

Run on prod after backup:
```bash
ssh alif "cp /opt/alif/polyglot/polyglot.db \
  /opt/alif-backups/polyglot_pre_orthography_$(date +%Y%m%d_%H%M%S).db"
# Then scp the migration script + run it
```

**Verify post-migration:**
```sql
SELECT lemma_form, COUNT(*)
  FROM lemmas
 WHERE language_code='la' AND lemma_form LIKE '%u%' AND lemma_form NOT LIKE 'u%'
 ORDER BY COUNT(*) DESC LIMIT 30;
```
Should show no consonantal-u patterns left in display forms.

**Step 1.4 — Update the LLPSI coverage generation prompt**

File: `polyglot/scripts/generate_llpsi_coverage_texts.py`

Find the prompt blocks `build_initial_prompt` and `build_remainder_prompt`.
Change:
- Old: `"Classical Latin orthography only: no macrons; use 'u' not 'v';
  use 'i' not 'j'. (Sum, uir, iuuenis — not sūm, vir, juvenis.)"`
- New: `"Modern reading Latin orthography: no macrons; use 'v' for
  consonantal v (vir, vocabulum, navis); use 'j' for consonantal i
  (juvenis, Julius, jam); vocalic u and i unchanged (puer, ignis).
  Examples: Marcus, vocabulum, vir, juvenis, navis, Julia — NOT Mārcus,
  uocabulum, uir, iuuenis, nauis, Iulia."`

Also: explicitly warn against the chapter-1 macron failure mode. Add to
the constraint block: `"Do NOT use macrons under any circumstance. Even
sentence-initial words: 'Marcus' NOT 'Mārcus'."`

**Step 1.5 — Regenerate the LLPSI coverage texts**

```bash
cd /Users/stian/src/alif/polyglot
POLYGLOT_LLM_PROVIDER=codex .venv/bin/python \
  /Users/stian/src/alif/.claude/worktrees/polyglot-latin/polyglot/scripts/generate_llpsi_coverage_texts.py \
  --all --passes 4 --coverage-threshold 0.93 \
  --tsv /Users/stian/src/alif/.claude/worktrees/polyglot-latin/polyglot/data/vocab/llpsi_fr.tsv \
  --report-out /Users/stian/src/alif/.claude/worktrees/polyglot-latin/research/polyglot-llpsi-coverage-2026-05-26.md \
  --json-out  /Users/stian/src/alif/.claude/worktrees/polyglot-latin/research/polyglot-llpsi-coverage-2026-05-26.json
```

(Use `run_in_background: true`. ~25 min on Codex.)

While it runs, do Steps 1.6–1.7 in parallel; come back to verify the
regenerated chapter 1 doesn't have macrons or u-spelling before
proceeding.

**Step 1.6 — Re-seed Story id=3 on prod (after regeneration completes)**

```bash
scp research/polyglot-llpsi-coverage-2026-05-26.json \
    alif:/tmp/polyglot-llpsi-coverage-2026-05-26.json
ssh alif "cp /opt/alif/polyglot/polyglot.db \
  /opt/alif-backups/polyglot_pre_llpsi_reseed_$(date +%Y%m%d_%H%M%S).db"
ssh alif "cd /opt/alif/polyglot && PYTHONPATH=/opt/limbic .venv/bin/python \
  /tmp/seed_llpsi_coverage_story.py --force"
```

The `--force` deletes existing Story id=3 + cascade-deletes its 35 pages
(no page_review_log harm — those rows reference story_id but the FK isn't
enforced strictly; orphan rows are harmless). Re-creates fresh.

**Verify:** open Story id=3 in the polyglot reader, page 1 should now read
`Marcus est puer Romanus. ...` style (no macrons, v/j orthography).

**Step 1.7 — Write the orthography PR**

Branch `sh/polyglot-latin-modern-orthography`. Files:
- `polyglot/CLAUDE.md` — convention update + dated reasoning
- `polyglot/app/services/languages/la.py` — add `_to_modern_reading_
  orthography` + use in `lemmatize()`
- `polyglot/scripts/import_latin_vocab.py` — preserve TSV spelling
  (don't canonicalize through LatinCy)
- `polyglot/scripts/migrate_latin_lemma_orthography.py` — NEW migration
  script
- `polyglot/scripts/generate_llpsi_coverage_texts.py` — updated prompt
- `polyglot/tests/test_la_provider.py` (extend or create) — tests for the
  transformer + a few representative LLPSI words

Don't include the regenerated text artifacts in the PR; those land in
research/ via a separate commit (or stay local-only — they're rebuildable).

### Phase 2 — Investigate philology + translation issues (parallel)

While the regeneration runs in the background during Phase 1, use the time
to investigate.

**Step 2.1 — Pull data for the user's session**

Write a diagnostic script (similar shape to
`/tmp/claude/check_latin_read.py` from 2026-05-26):

```python
# What sentence did the user actually see?
sqlite3 /opt/alif/polyglot/polyglot.db "
  SELECT srl.id, srl.sentence_id, s.text, s.translation_en,
         srl.target_lemma_id, l.lemma_form, l.gloss_en,
         srl.rating, srl.reviewed_at
    FROM sentence_review_log srl
    JOIN sentences s ON s.id = srl.sentence_id
    JOIN lemmas l ON l.lemma_id = srl.target_lemma_id
   WHERE l.language_code='la'
   ORDER BY srl.reviewed_at DESC
   LIMIT 10;
"
```

This surfaces the actual sentence text + translation + target lemma the
user reviewed. Compare translation length / structure to the user's
complaint ("3 words no comma"). If a 3-word translation is faithful to a
3-word Latin source, this is fine. If it's a thin translation of a
longer sentence, investigate the translation prompt.

**Step 2.2 — Pull enrichment for the 7 acquiring lemmas**

```python
sqlite3 /opt/alif/polyglot/polyglot.db "
  SELECT lemma_form, enrichment_status, enriched_at,
         json_extract(enrichment_json, '$.etymology.summary') AS etym,
         json_extract(enrichment_json, '$.diachrony.stages[0].label') AS stage1,
         json_extract(enrichment_json, '$.collocations[0]') AS coll1,
         length(coalesce(enrichment_json, '')) AS json_len
    FROM lemmas
   WHERE language_code='la'
     AND lemma_bare IN ('excidium','exiguus','latrocinor',
                         'incrementum','fere','ullus','exordium');
"
```

Compare with a Greek lemma's enrichment for the same fields. If Latin
enrichments are noticeably thinner or have empty fields, the prompt or
provider is failing on Latin specifically.

**Step 2.3 — One-shot enrichment A/B (if needed)**

If Step 2.2 shows weak Latin enrichments, run a manual enrichment for one
Latin lemma under both Codex and Claude, diff the outputs. Same
script-as-eval pattern as the sentence-gen A/B
(`backend/scripts/eval_codex_vs_claude_sentence_gen.py`).

### Phase 3 — Defer the Alif Codex audit-pipeline migration

The plan for the Alif hybrid Codex migration is in
`research/alif-codex-migration-plan-2026-05-26.md`. **Don't touch this in
this session.** User picked the migration but explicitly chose "Latin
fixes first, migration next session" via AskUserQuestion 2026-05-26. The
plan doc is ready when you're ready to execute.

---

## Order of execution + checkpoints

```
Phase 1 — Orthography migration
  1.1  Code/doc updates (CLAUDE.md, la.py, import_latin_vocab.py)  [30 min]
  1.2  u→v/i→j transformer + tests                                  [45 min]
  1.3  DB migration script + run on prod                            [30 min]
  1.4  Update generation prompt                                     [10 min]
  1.5  Regenerate LLPSI texts (background, ~25 min)
       ──── parallel ────
  2.1  Pull user-session data                                       [15 min]
  2.2  Check enrichment of the 7 acquiring lemmas                   [10 min]
  2.3  Decide if enrichment A/B needed (probably yes)               [—]
       ────────────────
  1.6  Re-seed Story id=3                                           [10 min]
  1.7  Open PR, self-review, merge, deploy                          [30 min]

Total: ~3-4 hours of focused work for Phase 1 + 2.
```

## Verification checklist (do these before declaring done)

- [ ] `polyglot/CLAUDE.md` says "v/j orthography" with 2026-05-26 date
- [ ] `lemma_form` for `vir`, `vocabulum`, `Julius` (whatever the canonical
  form is — `julius` or `Iulius`?), `juvenis` shows v/j spelling on prod:
  `sqlite3 /opt/alif/polyglot/polyglot.db "SELECT lemma_form FROM lemmas
   WHERE language_code='la' AND lemma_bare IN ('uir','uocabulum','iulius','iuuenis');"`
- [ ] Re-seeded Story id=3 page 1 body starts with v/j Latin (no `uocabula`,
  no `Mārcī`)
- [ ] Reader-side: tap a word like "vir" in the new chapter 1 → lookup card
  shows `vir` (not `uir`)
- [ ] Test suite passes: `cd polyglot && .venv/bin/python -m pytest`
- [ ] Investigation outputs from Step 2.1 + 2.2 captured in research/
- [ ] If philology turns out to need work, write up a separate plan
  (don't try to fix it inside this session)

## Rollback plan

If the migration goes sideways:
```bash
ssh alif "systemctl stop polyglot-backend && \
  cp /opt/alif-backups/polyglot_pre_orthography_<TIMESTAMP>.db \
     /opt/alif/polyglot/polyglot.db && \
  systemctl start polyglot-backend"
```
Then `git revert` the merged PR and re-deploy.

The LLPSI coverage Story can be rolled back independently — keep the
pre-reseed JSON file (`research/polyglot-llpsi-coverage-2026-05-26.json`
from before regeneration) and re-run `seed_llpsi_coverage_story.py
--force` against the older JSON.

---

## What I should and shouldn't do in this plan

**DO:**
- Re-read `polyglot/CLAUDE.md` end-to-end before starting (it's authoritative
  on polyglot conventions — Hard Invariants are load-bearing).
- Test the u→v/i→j transformer against ≥30 known LLPSI words before
  running the migration.
- Backup the prod polyglot DB before *every* destructive step.
- Commit incrementally on the branch — don't try to do all of Phase 1 in
  one commit.
- Use `sh/` branch prefix.

**DON'T:**
- Touch Alif's `llm.py` or any Alif-side code. The Codex hybrid migration
  is deferred (separate session, separate plan).
- Re-canonicalize the LLPSI TSV through LatinCy (the bug we're fixing).
- Try to fix philology / translation issues in the same PR — those need
  their own investigation + plan.
- Skip writing tests — the transformer is a regression risk magnet.
- Try to flip the lemma_bare lookup-key convention. That's the boundary
  function (`_normalize_latin`) — stays u-folded forever, it's the universal
  matching key.

---

## Linked artifacts

- `research/codex-vs-claude-sentence-gen-2026-05-26.md` — eval report (read
  if curious about the migration context)
- `research/alif-codex-migration-plan-2026-05-26.md` — deferred Alif refactor
- `research/polyglot-llpsi-coverage-2026-05-26.md` — current u-spelled
  generation output (to be replaced)
- `research/polyglot-llpsi-coverage-2026-05-26.json` — current generation
  JSON dump (used by seed script)
- `polyglot/CLAUDE.md` — convention source of truth
- `polyglot/app/services/languages/la.py` — Latin provider
- `polyglot/scripts/import_latin_vocab.py` — LLPSI/RA/DCC import
- `polyglot/scripts/generate_llpsi_coverage_texts.py` — generation script
- `polyglot/scripts/seed_llpsi_coverage_story.py` — seed script (already on
  prod at `/tmp/seed_llpsi_coverage_story.py` from 2026-05-26 run)
- MEMORY pointers: `feedback_codex_cli_free`,
  `feedback_no_book_sentences_for_acquiring`, `feedback_polyglot_mirror_alif`
