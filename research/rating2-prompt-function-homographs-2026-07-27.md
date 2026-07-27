# Rating-2 prompt and function-homograph validation

Date: 2026-07-27

## Trigger evidence

The first live word-evidence session after protocol v1 contained 13 sentence
reviews and 76 persisted token rows. One token was rating 2: `حَقَّقُوا` in
sentence 54327. It was fully vocalized and recorded no cause or tashkeel
interaction. EAS update health independently showed one iOS user/install, no
failed installs, and no crashes, so this was not an old-client explanation.

Four submitted token rows were rejected because frontend card metadata and the
backend lemma classifier disagreed. Three were real function words. The fourth
was `الأُمُّ`, mapped to lemma 76 أُمّ “mother”: the surface/card treated it as
content, but backend review submission normalized its lemma bare form to `ام`
and confused it with أم “or.”

## Before and after

| Area | Before | After |
|---|---|---|
| Rating-2 cause UI | Small inline row shown only while the yellow word retained current focus | Persistent, higher-contrast panel above submit; exact surface named; latest yellow remains active; arrows switch existing yellows |
| Prompt observability | No distinction between unseen prompt and ignored optional prompt | Stable `sentence_word_id` list logged on submission for panels actually rendered |
| Function classification | Normalized bare spelling decided even after a lemma was resolved | Unmapped tokens use spelling; mapped lemmas use shared lemma-aware helper |
| Homograph representation | No way to say “this lemma is lexical content despite its spelling” | Nullable `lemmas.function_word_override`; False explicitly opts into content treatment |
| Lexical categories | Would have been overloaded by a homograph category | Preserved; e.g. “meow” stays `onomatopoeia` while override=False |

## Audited migration population

The migration is guarded by production lemma ID, normalized bare spelling, and
a distinctive English-gloss fragment. A mismatch is a no-op.

| ID | Lemma | Meaning | Pre-state | Corpus tokens |
|---:|---|---|---|---:|
| 76 | أُمّ | mother | known | 670 |
| 554 | بَانَ | separate / become distinct | known | 62 |
| 615 | مَنِيّ | semen | learning | 33 |
| 663 | أُذُنٌ | ear | encountered | 87 |
| 943 | آنٌ | time | known | 2,163 |
| 976 | مَثَلَ | resemble | encountered | 74 |
| 1107 | أَمَا | meow | known; onomatopoeia | 60 |
| 1121 | لَهِمٌ | greedy / gluttonous | known | 46 |

Not overridden: grammatical/semantic gray cases (including أي and غير), and
any candidate without an unambiguous lexical reading in the current lemma row.

## Replay

Two copies of the 2026-07-27 115-MB production database were used. The control
copy received only the nullable column so current code could query it; the
treatment copy ran Alembic revision `f6b8c0d2e4a6`. Both ran
`build_session(limit=10, log_events=False, allow_intro_mutations=False)` at
2026-07-27 09:35 UTC.

| Metric | Before | After |
|---|---:|---:|
| Total due content lemmas | 867 | 871 |
| Covered due lemmas | 27 | 28 |
| Returned items (passage grouping can exceed requested card count) | 11 | 12 |
| Audited homographs visible in returned cards | mother (misflagged function, not due) | separate, mother, greedy (content/due) |

The +4 are existing mature/learning cards whose due status had been hidden by
the classifier, not new acquisitions. The migration does not rewrite ULK,
review history, counters, FSRS JSON, or intake state.

## Reversal

- Backend/data: Alembic downgrade drops `function_word_override`; no other row
  is modified. Before production upgrade, copy the SQLite database to
  `/opt/alif-backups/`.
- Frontend: revert the persistent active-yellow selector and prompt-ID field.
  Older backends ignore the new field; old clients omit it safely.
- Scheduling: no rating mapping, FSRS parameter, acquisition constant, or
  primary/collateral behavior changes.

## Validation after deployment

1. Confirm the migration column and all eight guarded False rows.
2. Fetch a session/card containing lemma 76 and verify
   `is_function_word=false`.
3. Submit the next yellow rating and verify its interaction row contains that
   token in `rating2_prompt_shown_sentence_word_ids`; cause may legitimately
   remain empty.
4. If a cause is chosen, verify the same token’s protocol-v1 evidence contains
   it.
5. Watch total due (+4 expected at the snapshot boundary), session completion,
   sync rejects, backend errors, OTA install failures, and crashes.

## Open risks for independent validation

- A token can be mapped to the wrong homographic lemma upstream; the override
  makes a correct resolved lemma classify correctly but cannot repair a bad
  mapping.
- The eight-row audit is intentionally conservative and not exhaustive.
- Prompt-shown telemetry is recorded at React render/effect time, not proof of
  visual attention. It distinguishes technical exposure from no exposure, not
  reading from ignoring.
- Multi-yellow interaction uses one active panel at a time and word-info arrows
  to switch. The exact token is named, but usability should be checked on a
  real multi-yellow card.
