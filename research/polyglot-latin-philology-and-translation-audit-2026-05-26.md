# Polyglot Latin — philology + translation complaint audit (2026-05-26)

> **EPILOGUE (2026-05-26, same day, ~2 hours later).** This audit's broad
> conclusions held but it had two blind spots. A follow-up investigation +
> PR #157 shipped three code fixes plus a one-shot data repair. See
> `research/experiment-log.md` entry "Polyglot Latin — three lookup-card
> quality fixes from the audit follow-up (PR #157, deployed)" for the full
> write-up.
>
> **What the audit got wrong:**
> 1. **The "3-word translation no comma" complaint is a *gloss*, not a
>    sentence translation.** This audit's tiny-translation probe ran only
>    against `sentences.translation_en` for Eutropius (`tiny <30 n=0`) and
>    missed `lemmas.gloss_en`. Two of the 7 acquiring lemmas the user
>    tapped had run-on multi-sense glosses from the Roma Aeterna seed
>    import: `excidium → "demolition setting of the sun"`,
>    `exordium → "beginning introduction foundation"`. Both appear on
>    Eutropius p.1 (`Troiae excidium`, `ab exordio`), so on Reveal the
>    user saw the bare gloss next to the Latin and read it as a "3-word
>    translation." Root cause: `parse_roma_aeterna_apkg.py` HTML stripper
>    collapsed `<div>sense1</div><div>sense2</div>` to one space.
> 2. **The enrichment quality check skipped one bad quote.** Of the 7
>    manually-enriched lemmas, `fere` came back with a 734-char Caesar
>    passage whose `translation_en` was
>    `"[Context: the passage contains 'omnibus fere annis' illustrating
>    fere with quantities]"` — meta-comment, not translation. The Haiku
>    verifier explicitly skips quotes (2026-05-21 spec) so this slipped
>    through silently. This audit noted only "the enrichment landed" and
>    did not inspect quote content quality.
>
> **What changed (PR #157, merged + deployed):**
> 1. `parse_roma_aeterna_apkg.py` — block-level tags emit `"; "`
>    separator. Prevents recurrence.
> 2. `lemma_philology.py` — QUOTES prompt bans meta-commentary;
>    `_strip_meta_commentary_quotes` post-check runs on both main and
>    self-correct paths.
> 3. `reading_intake._split_into_sentences` — Latin-aware, protects
>    `Kal.`/`Non.`/`Id.`/`a.d.` (subsumes the `Kal.` followup tracked in
>    commit `c8b557db`).
> 4. `scripts/repair_runon_glosses.py` — new one-shot data-repair tool
>    (LLM-judged, audit + apply, mirrors `regloss_lemmas.py` shape).
>
> **Data fix on prod:** 32 run-on Latin glosses repaired (including the
> two acquiring-lemma cases). `fere` re-enriched — 3/3 quotes now clean
> translations. Backup at `/opt/alif-backups/polyglot_pre_runon_
> 20260526_125523.db`. Logged to `ActivityLog` as
> `gloss_runon_repair_applied`.
>
> **Lesson preserved in IDEAS.md + CLAUDE.md:** when investigating a
> "translation" complaint, check both `sentences.translation_en` AND
> `lemmas.gloss_en` — both render on the lookup card and the user may
> not distinguish them.
>
> The original audit body below is preserved unchanged for the historical
> record.

---

Investigation triggered by two user reports during the 2026-05-26 Eutropius
reading session:
1. "the new words did not generate any good philology notes"
2. "the translation is just three words with no comma etc"

Both routed through `research/polyglot-latin-orthography-plan-2026-05-26.md`
as Issues 2 and 3 (Issue 1 was the orthography flip, addressed separately by
this PR).

## TL;DR

- **Philology**: not a bug — a cron-cadence gap. The 7 newly-acquiring lemmas
  were created at 06:51–06:53 UTC, **just after** the 06:45 Latin enrichment
  cron pass. The next cron pass at 09:45 picks them up; the lookup card was
  empty in the user's session window because of that 3-hour gap. Manually
  ran enrichment for those 7 lemmas → `{"enriched": 7}`, lookup cards now
  populated.
- **Translation**: no thin Latin translations exist in the DB (zero under
  30 chars). The complaint most likely refers to a per-word gloss tap
  (a gloss like `fere` → "almost, about, nearly" is literally 3 comma-less
  words), or to an orphaned fragment caused by the `Kal.` abbreviation
  splitter (already tracked as followup in commit `c8b557db`).

## Probe outputs

### Latin enrichment status — 0 enriched, 3,911 unenriched
```
Latin enrichment_status histogram (Q5):
  (NULL)  n=3911

Greek for comparison:
  done           n=242  avg JSON 3413 bytes
  done_partial   n=  1  avg JSON 2770 bytes
  failed         n=  2
```

`find_unenriched_lemmas('la', limit=30)` returns 7 IDs (the acquiring
lemmas the user just created). The cron at 09:45 UTC will batch-enrich them.

### Cron history — Latin enrich runs every 3h but returns `enriched: 0`
```
[2026-05-25 21:45:01] la enrich_lemma_philology → {"enriched": 0}
[2026-05-26 00:45:01] la enrich_lemma_philology → {"enriched": 0}
[2026-05-26 03:45:01] la enrich_lemma_philology → {"enriched": 0}
[2026-05-26 06:45:01] la enrich_lemma_philology → {"enriched": 0}
```

`enriched: 0` with empty `failed_lemma_ids` and `skipped_lemma_ids` =
`find_unenriched_lemmas` returned 0 IDs to begin with (the early-return at
`scripts/enrich_lemma_philology.py:58`). No lemmas were eligible at those
moments — the 7 acquiring lemmas didn't exist yet (created 06:51–06:53).

### Sentence reviews — none for Latin
```
sentence_review_log by language_code:
  el = 228
  (la = 0)
```

The user's session was page-advance only (`page_review_log`), not
sentence-review. So the "3-word translation" complaint did NOT come from a
sentence-review card. It came from either:
- a per-word lookup card (gloss text only — `fere` = "almost, about, nearly"
  is literally 3 words separated by commas, but tapping a word in the
  reader shows the gloss prominently)
- a Reveal of page 1 — which showed sentence #1410 starting with `"of May, ..."`,
  an orphan fragment caused by the `Kal.` abbreviation splitter in #1409

### Page-translation data is high-quality
```
Eutropius Liber I, page 1, sentence #1410:
  LA: "Maias, Olympiadis sextae anno tertio, post Troiae excidium,
      ut qui plurimum minimumque tradunt, anno trecentesimo nonagesimo quarto."
  EN: "of May, in the third year of the sixth Olympiad, in the three hundred
      ninety-fourth year after the fall of Troy, according to those who give
      the highest and lowest dates."
```

The English translation is faithful — but the Latin starts mid-clause
(`Maias,`) because sentence #1409 broke on `XI Kal.`. The Latin reads as a
fragment; the English reads as a comma-laden but ungrounded chunk. That's
the only structural translation quality issue in the entire Latin sentence
table (Q1: only one Kal.-ending sentence exists).

### Sentence-length histogram — no thin Latin translations
```
Latin sentence translations by length:
  normal (80+)     n=18
  empty            n=10  (cron lazy — translate_sentences will fill)
  short (30-80)    n=6
  tiny (<30)       n=0   ← the "3 words" complaint has no match here
```

## What was fixed in this audit

- **Manually enriched** the 7 acquiring Latin lemmas (`excidium`, `exiguus`,
  `latrocinor`, `incrementum`, `fere`, `ullus`, `exordium`) so their lookup
  cards work without waiting for the 09:45 cron:

  ```bash
  ssh alif "cd /opt/alif/polyglot && \
    PYTHONPATH=/opt/limbic .venv/bin/python \
    scripts/enrich_lemma_philology.py --language la --max-lemmas 7 \
    --include-failed"
  # → {"enriched": 7, "failed_lemma_ids": [], "skipped_lemma_ids": []}
  ```

## What's NOT fixed in this audit (deferred)

- **The 3-hour cron-cadence gap** means any newly-tapped acquiring lemma's
  lookup card is empty for up to 3 hours. Options if this becomes annoying:
  (a) trigger enrichment lazily on first lookup-card view, like the gloss
  fallback already does; (b) shrink the cron interval; (c) add a Box-1
  acquiring-lemma backfill on `start_acquisition()`. None are blocking
  the current PR. Track separately.
- **The `Kal.` (and `Non.`, `Id.`, `a.d.`) abbreviation splitter bug**.
  Already tracked as followup per commit `c8b557db`.
- **Translation quality vs prompt**: not actionable from this evidence —
  the existing Latin translations are good (faithful, complete) and the
  user's complaint doesn't match the data on file.

## Probe scripts

Source under `/tmp/claude/` locally (not committed). Re-run as:

```bash
scp /tmp/claude/probe_latin_session.py alif:/tmp/
scp /tmp/claude/probe_latin_deeper.py alif:/tmp/
scp /tmp/claude/probe_eutropius_translation.py alif:/tmp/
scp /tmp/claude/probe_kal_split.py alif:/tmp/
scp /tmp/claude/probe_unenriched.py alif:/tmp/
ssh alif "/opt/alif/polyglot/.venv/bin/python /tmp/probe_latin_session.py"
# ... etc
```
