# Rijāl fī al-Shams — Abu Qays read-only Alif rehearsal

Status: **artifact ready for reading; production import and activation not approved**

Machine-readable record:
`research/rijal-abu-qays-import-dry-run-2026-07-10.json`

## Result

The final bilingual EPUB contains 89 aligned Arabic/English pairs and 1,807
Arabic tokens. Its SHA-256 is
`937ed23293fd55816883372f668d2f4f00a4831fa160a54ed4a5046986d70eb4`.

The Alif rehearsal was performed against a consistent local SQLite backup of
the production database. The database passed `PRAGMA integrity_check`; its
SHA-256 remained
`14402d39ca993ef4c28202e177cc69b414a90c30c469e2c01f7b29079e21a0b5`
before and after analysis. Rehearsal code did not connect to production, invoke
an importer, apply corrections, create learner state, or write review history.

## Raw mapping is not activation-safe

The existing lookup mapped 1,591 tokens and left 216 tokens null across 152
surface forms. It made 179 ambiguous choices and derived 287 mappings through
clitic stripping. Raw category counts were 925 known, 58 in progress, 93 mapped
but not actively learned, 521 function-word tokens, 208 content-null tokens,
and two tokens already tagged as proper names.

The deterministic readiness probe estimates 85.6% immediately readable and
88.8% including in-progress vocabulary. Those percentages are useful for
reader planning, but they are optimistic until the ambiguous and clitic-derived
mappings are reviewed.

Confirmed failures include:

| Surface in the chapter | Unsafe result | Required reading |
|---|---|---|
| `رَأْسَهُ / رَأْسِهِ` (6×) | `رِئَاسَة` — presidency | `رَأْس` — head |
| `الرَّمْل` | `أَرْمَل` — widower | `رَمْل` — sand |
| `رَائِحَة` (4×) | `رَاحَ` — go | `رَائِحَة` — scent |
| `يُحَلِّق` | `حَلَق` — throat | `حَلَّقَ` — circle/fly |
| `آب` (3×) | `آبَ` — return | August, noun |
| `تَرَاهُ / تَرَيْنَ / تَرَى` | unrelated normalized collisions | `رَأَى` — see |

Chunk 33's `تُرَى` is the impersonal “I wonder” construction and needs separate
handling. The chapter also contains recurrent proper names that must remain
lookup-only: Qays (11), Salim (13), Saad (7), Basra (4), Kuwait (5), Tigris
(2), Euphrates (2), and Jaffa (1). Abu/Umm are kunya components here, not
vocabulary targets.

The contextual verifier was attempted on the first eight pairs. Both the Sonnet
and Codex calls timed out after 60 seconds, so the manifest explicitly leaves
all 179 ambiguous tokens unapproved. A timeout is not treated as verification.

## Learning decision

At the snapshot checkpoint Alif still had 136 actionable Box-1 words, 17 due
Box-2 words, and 912 strict main-lane FSRS cards due. The deployed recovery gate
therefore sets the current new-word budget to zero. Bulk-preloading this
chapter's gaps would work against the recovery policy and is not the fastest
route to reading.

Use the bilingual EPUB externally now. Read Arabic first and reveal the adjacent
English only after retrieval fails. This adds useful contextual exposure without
creating cards or distorting the recovery experiment. Once the earned intake
budget reopens, a provisional high-utility reader-support pool is:

`شَطّ، تُرَاب، مَأْمُون، عَاقِبَة، نَهْر، رَفِيع، تَهْرِيب، نَدِيّ،
اِغْتَسَلَ، اِمْتَلَأَ، حَامَ، نَحِيل، تَلَصَّصَ، هَزَّ، اِرْتَجَفَ، ذُلّ،
صَبِيّ، غُصَّة`.

This list is a curation queue, not an instruction to create 18 cards. Each item
must be checked against current learner state and the final contextual mapping;
introductions must remain within the earned daily budget.

## Backfill and projected import delta

No historical ReviewLog, FSRS, acquisition, yellow/confusion, story-completion,
or sentence data needs backfilling. Before a future activation, the mapping
layer needs five verified content lemmas (`رَمْل، رَائِحَة، حَلَّقَ، رَأَى`
and month-name `آب`) plus up to seven lookup-only name entries. The four lexical
roots already exist; the month name must not receive a fabricated root.

The desired safe-reader delta is:

| Table/entity | Projected change |
|---|---:|
| Story | +1 |
| StoryWord | +1,807 |
| Content Lemma | +5 |
| Proper-name Lemma | up to +7 |
| Root | +0 |
| UserLemmaKnowledge | +0 |
| Sentence / SentenceWord | +0 / +0 |

The generic story/book import paths are not authorized for this artifact. They
can auto-create data outside that delta and can retain bad mappings when
verification is unavailable. A future importer must consume the frozen 89-pair
manifest, fail closed on unreviewed ambiguity, keep proper names/function words
non-schedulable, and be rehearsed on a disposable database copy before a second
production approval.
