# Hindawi Passage Promotion Spec

**Date:** 2026-05-10
**Status:** Implementation spec
**Related:** `analysis-2026-05-10-hindawi-reading-path.md`, `backend/scripts/rank_hindawi_passages.py`

## Goal

Promote selected authentic consecutive passages from the raw Hindawi children's corpus into Alif's existing longer-passage review path.

The product outcome is narrow:

- Find a 3-5 sentence raw Hindawi window that is already readable under current lemma knowledge.
- Add translations and run the same validation/quality gates used for generated maintenance passages.
- Store it as a cohesive passage object that the reading selector can show as one grouped passage card.

This is not a full-book importer, not a generic corpus reimport, and not a change to the session selector's grouping policy.

## Current Grounding

The current longer-passage system is generated-maintenance-passage oriented:

- `backend/app/services/passage_generator.py`
  - `store_maintenance_passage(...)` validates 3-5 sentence passages, verifies vocabulary mapping, runs sentence quality review, checks passage cohesion, creates one `Story(format_type="maintenance_passage")`, and creates child `Sentence(source="passage")` rows.
  - `generate_and_store_maintenance_passage(...)` generates these passages for due maintenance words.
- `backend/app/services/sentence_selector.py`
  - `_group_maintenance_passages(...)` groups only rows where `Sentence.source == "passage"` and `story_id` is shared.
  - It intentionally does not bundle arbitrary `source="corpus"` rows, even if they are adjacent or individually good.
  - Passage cards are only valid for FSRS maintenance states: `known`, `learning`, `lapsed`, not early acquisition.
- `backend/scripts/rank_hindawi_passages.py`
  - Read-only scorer for raw Hindawi parquet.
  - Ranks consecutive 3-5 sentence windows by known/active coverage, unmapped percentage, and mapped-gap lift.
  - Supports fast broad scans with `--disable-camel` and targeted full-CAMeL reruns.

Confirmed prod candidate windows:

| Book/window | Current active coverage | Unmapped | Note |
|---|---:|---:|---|
| `دِمْنَةُ وَشَتْرَبَة`, start sentence 10 | 100.0% | 0.0% | Strong 4-sentence dialogue around the bull/lion scene. |
| `لَيْلَى وَالذِّئْبُ`, start sentence 45 | 100.0% | 0.0% | Strong 4-sentence wolf/dialogue window. |
| `لَيْلَى وَالذِّئْبُ`, start sentence 46 | 100.0% | 0.0% | Adjacent viable dialogue window. |
| `الْأَرْنَبُ وَالصَّيَّادُ`, start sentence 14 | 90.5% | 0.0% | Full mapped coverage after a tiny pre-study list (`لَمْ`, `مرحة`). |

The recommended first pilot is `لَيْلَى وَالذِّئْبُ`, start sentence 45, sentence count 4.

## Non-Goals

- Do not change `_group_maintenance_passages` to group normal `source="corpus"` sentences.
- Do not activate all imported Hindawi corpus sentences.
- Do not create or teach missing lemmas as part of the passage promoter.
- Do not promise full-book reading comfort; full books are still blocked by unmapped raw surfaces.
- Do not bypass vocabulary mapping, translation, or quality gates for authenticity.

## Proposed Deliverable

Add a script:

```text
backend/scripts/promote_hindawi_passage.py
```

Primary CLI:

```bash
cd backend
DATABASE_URL=sqlite:///data/alif.db \
  python3 scripts/promote_hindawi_passage.py \
  --parquet /tmp/hindawi.parquet \
  --title "لَيْلَى وَالذِّئْبُ" \
  --start-sentence 45 \
  --sentence-count 4 \
  --dry-run
```

Apply:

```bash
cd backend
DATABASE_URL=sqlite:///data/alif.db \
  python3 scripts/promote_hindawi_passage.py \
  --parquet /tmp/hindawi.parquet \
  --title "لَيْلَى وَالذِّئْبُ" \
  --start-sentence 45 \
  --sentence-count 4 \
  --apply
```

Useful options:

| Option | Required | Meaning |
|---|---:|---|
| `--parquet PATH` | yes | Raw Hindawi parquet. |
| `--db PATH` | no | SQLite DB path; overrides `DATABASE_URL`. |
| `--title TEXT` | yes | Exact or substring title filter. Refuse ambiguous matches unless `--book-index` is provided. |
| `--book-index N` | no | Resolve ambiguity when several books match title. |
| `--start-sentence N` | yes | 1-based offset from `import_hindawi.extract_sentences(...)` using the same min/max word defaults as the ranker. |
| `--sentence-count N` | no | Clamp or validate to 3-5; default 4. |
| `--min-words N` | no | Default should match ranker/importer extraction assumptions. |
| `--max-words N` | no | Default should match ranker/importer extraction assumptions. |
| `--translations-json PATH` | no | Manual translations. JSON list of `{ "arabic": "...", "english": "..." }` or plain list of English strings. |
| `--translate` | no | Generate translations through the existing LLM service if translations are not supplied. |
| `--target-lemma-id ID` | repeatable | Force target lemma ids for passage validation/selector eligibility. |
| `--quality-gate / --no-quality-gate` | no | Default on for apply. `--no-quality-gate` only for local debugging. |
| `--dry-run` | yes/no | Print extraction, coverage, target candidates, duplicate status, and generated payload; write nothing. |
| `--apply` | yes/no | Write the passage. Mutually exclusive with `--dry-run`. |
| `--allow-duplicate` | no | Default false; only write duplicate body if explicitly requested. |
| `--json` | no | Machine-readable dry-run/apply output. |

## Implementation Plan

### 1. Reuse Ranker Runtime

The promoter should import rather than duplicate the core raw-Hindawi extraction and coverage machinery:

- `scripts.import_hindawi.extract_sentences`
- `scripts.rank_hindawi_passages._configure_database`
- `scripts.rank_hindawi_passages._load_runtime`
- `scripts.rank_hindawi_passages._load_context`
- `scripts.rank_hindawi_passages.sentence_coverage`
- `scripts.rank_hindawi_passages.window_to_dict` where useful

If importing private helpers feels too fragile, factor the reusable pieces into a small module such as:

```text
backend/scripts/hindawi_passage_utils.py
```

Do not start by refactoring the importer broadly. Keep the first implementation scoped.

### 2. Resolve the Book and Window

Load parquet with pandas:

```python
df = pd.read_parquet(args.parquet)
books = df[df["category"].str.contains(args.category, case=False, na=False)]
matches = books[books["title"].str.contains(args.title, na=False)]
```

Behavior:

- If no match: exit with a clear error.
- If multiple matches and no `--book-index`: print numbered candidates and exit.
- Extract sentences using the same `extract_sentences(text, min_words, max_words)` path as the ranker.
- Convert `--start-sentence` from 1-based to 0-based.
- Refuse out-of-range windows.
- Preserve the exact Arabic sentence strings from the raw corpus.

Store these provenance fields later:

```json
{
  "authentic_source": "hindawi",
  "hindawi": {
    "title": "...",
    "author": "...",
    "category": "children.stories",
    "start_sentence": 45,
    "sentence_count": 4,
    "min_words": 5,
    "max_words": 18,
    "parquet_basename": "hindawi.parquet"
  }
}
```

### 3. Score and Print Coverage

Before translation or writing, compute coverage for the selected window with the same method as `rank_hindawi_passages.py`.

Dry-run output should show:

- title, author, start sentence, sentence count
- Arabic sentences
- active percentage, known percentage, unmapped percentage
- top missing mapped lemmas
- top unmapped surfaces
- mapped content lemmas in the window with state and due status
- whether any duplicate passage already exists

Apply should refuse by default if:

- `unmapped_pct > 0`
- any sentence has zero mapped content words
- `active_pct` is below a conservative threshold, initially `0.90`, unless `--allow-low-coverage` is added later

The first implementation can keep this conservative. The ranker remains the right tool for exploratory lower-coverage windows.

### 4. Choose Target Lemmas

`store_maintenance_passage(...)` requires `target_words` and also requires that at least one target appears in the passage. The selector also only groups passages when due-bearing rows cover FSRS maintenance states.

Target selection behavior:

1. If `--target-lemma-id` is supplied, use those ids after verifying each id appears in the selected window as a non-function, non-proper content lemma.
2. Otherwise auto-select up to 4 content lemmas from the window in this priority order:
   - `known`, `learning`, or `lapsed` with FSRS due date `<= now`, oldest due first.
   - `known`, `learning`, or `lapsed` with the nearest future FSRS due date.
   - `acquiring` only if no FSRS maintenance lemma appears, but warn that the grouped passage will not be eligible until a maintenance-state word is due.
3. Exclude function words, proper names, junk, onomatopoeia, and lemmas without `gloss_en`.

Tradeoff:

- Due-only target selection gives immediate review visibility but may reject good passages when no current due word appears.
- Allowing nearest-future maintenance targets lets us build a queue of authentic passages that surface naturally later.
- The MVP should allow both: auto due-first, explicit override for controlled pilots.

### 5. Build Eligible Word List

For `store_maintenance_passage(...)`, pass an eligible vocabulary list broad enough to validate authentic text:

- Include canonical lemmas with user state in `known`, `learning`, `lapsed`, `acquiring`.
- Exclude proper names, onomatopoeia, junk.
- Exclude function words via the same `_is_function_word` logic.
- Require `gloss_en`.
- For `acquiring`, mirror `_eligible_passage_words(...)`: exclude fragile early acquisition where `acquisition_box < 2`.

Implementation options:

- Preferred MVP: import and use `_eligible_passage_words(db)` from `passage_generator.py`.
- If private import is undesirable, move `_eligible_passage_words` to a small shared helper in `app/services/passage_vocab.py` and update generated passage code to use it.

Avoid duplicating a subtly different eligibility rule in the script.

### 6. Translation

Each stored sentence needs an English translation because quality review checks translation correctness and UI renders the passage with English support.

Supported paths:

1. Manual translations via `--translations-json`.
2. LLM translations via `--translate`.

LLM translation requirements:

- Use the project's existing LLM abstraction, not a raw shell call, unless the codebase pattern clearly favors CLI-only for scripts.
- Request concise, literal sentence-level English translations.
- Preserve sentence count and order exactly.
- Return strict JSON.
- Refuse apply if translation count differs from Arabic sentence count.

Suggested schema:

```json
{
  "sentences": [
    {"arabic": "...", "english": "..."}
  ]
}
```

Dry-run should be able to run without translation if `--translate` and `--translations-json` are absent. Apply should require one of them unless an explicit `--no-quality-gate` debug mode is used.

### 7. Store Using Existing Passage Path

Recommended MVP:

Create a generated-shaped payload and call `store_maintenance_passage(...)`:

```python
generated = {
    "title_ar": hindawi_title,
    "title_en": f"Hindawi passage: {hindawi_title}",
    "style_tag": "hindawi_authentic",
    "sentences": [
        {"arabic": ar, "english": en}
        for ar, en in zip(arabic_sentences, english_translations)
    ],
}

story = store_maintenance_passage(
    db,
    generated,
    target_words=target_words,
    eligible_words=eligible_words,
    quality_gate=args.quality_gate,
)
```

Then patch story metadata in the same transaction or immediately after:

```python
story.metadata_json = {
    **(story.metadata_json or {}),
    "authentic_source": "hindawi",
    "hindawi": provenance,
    "target_lemma_ids": sorted(target_ids),
}
```

Keep `Sentence.source == "passage"` because the selector depends on it.

For `Story.source`, prefer leaving the existing value from `store_maintenance_passage` (`"maintenance"`) in the MVP unless the agent audits all consumers of `Story.source`. Provenance belongs in `metadata_json`.

Tradeoff:

- Reusing `store_maintenance_passage` minimizes data-model risk and uses the established validation path.
- It is semantically imperfect because the function is named for generated maintenance passages. That is acceptable for the pilot.
- A later cleanup can factor a generic `store_passage(...)` helper used by both generated and Hindawi paths.

### 8. Duplicate Detection

Before apply, check for existing passage stories with the same Arabic body:

```python
existing = (
    db.query(Story)
    .filter(
        Story.format_type == "maintenance_passage",
        Story.body_ar == body_ar,
    )
    .first()
)
```

Default behavior:

- Dry-run reports the duplicate story id.
- Apply refuses duplicate unless `--allow-duplicate`.

Optional later improvement:

- Store and compare a `source_hash` in `metadata_json`.

### 9. Selector Integration

Do not change selector behavior for the MVP unless a test exposes a real gap.

Expected behavior after promotion:

- Child sentences have `source="passage"` and shared `story_id`.
- If at least one child sentence covers a due `known`/`learning`/`lapsed` word, the reading selector loads active siblings and returns the group as one passage card.
- `source="corpus"` Hindawi sentences remain standalone and ungrouped.

If promoted passages do not show up, inspect due-target selection first. Do not "fix" this by grouping corpus rows.

## Tests

Add focused tests rather than broad end-to-end production tests.

Suggested new file:

```text
backend/tests/test_promote_hindawi_passage.py
```

Minimum tests:

1. **Window extraction is stable**
   - Given fake book text and `start_sentence=2`, script extracts exactly the requested consecutive window.
   - Verify 1-based CLI offset behavior.

2. **Ambiguous title is refused**
   - Fake two matching books.
   - No `--book-index` exits with candidate list.

3. **Duplicate detection**
   - Existing `Story(format_type="maintenance_passage", body_ar=...)`.
   - Apply refuses without `--allow-duplicate`.

4. **Generated payload shape**
   - Manual translations plus selected targets produce the expected generated-shaped dict.

5. **Target selection**
   - Due maintenance lemma in the window is preferred over future due.
   - Proper names/function words are excluded.
   - Explicit `--target-lemma-id` must appear in the window.

6. **Selector invariant**
   - Existing or added test: promoted `source="passage"` siblings group; arbitrary `source="corpus"` rows do not group.
   - This may already be covered in `test_sentence_selector.py`; extend only if needed.

Run:

```bash
cd backend
.review-venv/bin/python -m pytest \
  tests/test_promote_hindawi_passage.py \
  tests/test_rank_hindawi_passages.py \
  tests/test_passage_generator.py \
  tests/test_sentence_selector.py \
  -q
```

Also run:

```bash
cd backend
.review-venv/bin/python -m py_compile \
  scripts/promote_hindawi_passage.py \
  scripts/rank_hindawi_passages.py \
  app/services/passage_generator.py \
  app/services/sentence_selector.py
```

Note: system Python recently failed locally because `pydantic_core` had an architecture mismatch. Use `.review-venv` for focused tests unless the environment is fixed.

## Manual Prod Smoke Plan

Dry-run first:

```bash
ssh alif 'cd /opt/alif/backend && DATABASE_URL=sqlite:////opt/alif/backend/data/alif.db .venv/bin/python scripts/promote_hindawi_passage.py --parquet /tmp/hindawi.parquet --title "لَيْلَى وَالذِّئْبُ" --start-sentence 45 --sentence-count 4 --dry-run'
```

Expected dry-run properties:

- 4 Arabic sentences printed.
- `active_pct == 100.0`.
- `unmapped_pct == 0.0`.
- At least one target candidate in `known`, `learning`, or `lapsed`.
- Duplicate status is false, unless the passage was already promoted.

Apply only after inspecting translations:

```bash
ssh alif 'cd /opt/alif/backend && DATABASE_URL=sqlite:////opt/alif/backend/data/alif.db .venv/bin/python scripts/promote_hindawi_passage.py --parquet /tmp/hindawi.parquet --title "لَيْلَى وَالذِّئْبُ" --start-sentence 45 --sentence-count 4 --translate --apply'
```

Post-apply checks:

```sql
SELECT id, title_ar, source, format_type, json_extract(metadata_json, '$.authentic_source')
FROM stories
WHERE format_type = 'maintenance_passage'
ORDER BY id DESC
LIMIT 5;

SELECT id, story_id, source, target_lemma_id, is_active, arabic_text
FROM sentences
WHERE story_id = <story_id>
ORDER BY id;
```

Expected:

- One story.
- 3-5 sentence rows.
- Every sentence has `source = 'passage'`.
- Shared `story_id`.
- `target_lemma_id` populated.
- No `SentenceWord.lemma_id IS NULL` for non-function content words.

## Tradeoffs and Decisions

### Authentic Hindawi vs Generated Passages

Authentic Hindawi passages are better for the reading goal because they expose real children's-book style, repeated characters, and narrative continuity. Generated passages are better for precise due-word targeting.

Decision: use both. Generated passages remain the automatic maintenance backfill. Hindawi passages are curated authentic reading objects promoted only when coverage is high and quality gates pass.

### Reuse Existing Store Function vs New Generic Store

Reusing `store_maintenance_passage` is fastest and safest because it already enforces the invariants the selector expects.

The downside is naming and metadata semantics. A generic `store_passage` abstraction would be cleaner, but it risks turning a small pilot into a cross-cutting refactor.

Decision: reuse first, patch provenance metadata, refactor later if multiple authentic passage sources are added.

### Strict Coverage vs Slightly Challenging Passages

A 90-95% passage may be pedagogically useful, especially with a pre-study list. But the first promotion path should prove storage/selector behavior, not test learner tolerance.

Decision: MVP apply threshold should be conservative: no unmapped content and active coverage at or above 90%, with the first pilot at 100%.

### Due-Word Targeting vs Reading-Pack Availability

If the passage only stores future-due maintenance targets, it may not show immediately in a normal session. If it requires currently due targets, good passages may be impossible to promote on demand.

Decision: due-first auto-selection with explicit `--target-lemma-id` override. Dry-run must expose target candidates and due status so the operator can make the right call.

### Manual vs LLM Translation

Manual translations are safer for the first production object but slower. LLM translations make the script operational.

Decision: support both. Require inspection through dry-run; keep quality gate on for apply.

## Acceptance Criteria

Implementation is complete when:

- `promote_hindawi_passage.py` can dry-run the `لَيْلَى وَالذِّئْبُ` sentence-45 window and report coverage/targets/duplicates without writing.
- With supplied or generated translations, `--apply` stores exactly one maintenance-passage story and 3-5 `source="passage"` sentence rows.
- The stored rows pass existing vocabulary validation and quality gates.
- Duplicate apply is refused by default.
- Selector tests still confirm only intentional passage rows are grouped.
- Focused backend tests pass under `.review-venv`.
- `docs/scripts-catalog.md` includes the new promoter script.
- This spec and `research/README.md` are updated if implementation details materially differ.

## Future Extensions

After the first pilot works:

- Batch promote top N ranker candidates with an approval file.
- Add a `--prestudy-limit` mode that allows 1-3 mapped missing lemmas and prints/imports a tiny pre-study queue.
- Add a lightweight admin view for authentic reading passages.
- Build full Hindawi Reading Pack mode: 20-50 sentences/passages with a pre-study list and optional book progression.
- Build selected-book unlocker for `لَيْلَى وَالذِّئْبُ`: classify top unmapped surfaces as morphology gap, function word, proper name, or true missing lemma.
