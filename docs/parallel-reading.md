# Parallel Reading experiment

Implemented 10 October 2026. A shared **Parallel** tab and direct `/parallel`
bookmark offer Arabic-first reading with Ancient Greek, Latin and Russian.
The app remembers the last visible tab and restores it on a normal launch at `/`.
Explicit non-root bookmarks, query links and native deep links take precedence.
A saved tab from an incompatible language surface falls back to that language's
home. Detail screens do not replace the remembered tab. The navigator stays
mounted during startup, but Arabic review content does not mount until startup
has resolved and the Arabic review tab is actually selected. An already-open
review session remains mounted when switching tabs, preserving existing behavior.

## Reading experience

A small library opens each text at its independently saved passage. Arabic is
above a selectable supporting version; support can remain visible across passages.
All-four mode stacks versions vertically. Reader controls include text size,
vowel marks, curated optional English phrase clues, numbered passage navigation,
an Arabic-only whole-text reread, completion and optional effort reflection.
Passage reading hides the app tab bar to give the text room. Library returns to
the catalogue; Alif returns to the normal review tab. The catalogue shows tabs.
Changing passages returns to the top; the bookmark is a passage, not a pixel offset.

The first three pieces are:

- Aesop's *Zeus and the Jar of Good Things*: three passages, 24 Arabic words;
  the original demo's Greek narrative and new supporting translations. The separate
  moral is omitted. Checked-in Arabic vocalization is available for this piece.
- *The Doves and the Net*: six passages, 179 Arabic whitespace-delimited tokens;
  a continuous original Arabic excerpt from *Kalīla wa-Dimna*. It ends with the
  escape plan, rather than claiming to contain the entire fable. New supporting
  prose translations; Greek/Latin render جرذ as mouse, explicitly disclosed.
- *The Bathhouse and the Beautiful Horse*: five passages, 135 Arabic words;
  complete *Enchiridion* sections 4 and 6, with original Greek and new translations.
  The two arguments are joined; intervening section 5 is not included.

Word totals use whitespace splitting, including standalone punctuation; they are
orientation labels, not NLP-derived vocabulary counts. Source URLs and version
provenance are visible in About. New translations are AI learning aids, reviewed
in preparation but not independently checked by a language specialist. Longer
pieces retain selective vowel marks only; the UI says so rather than claiming
full vocalization. Optional English is confined to phrase clues.

## Persistence and evidence boundaries

Content is bundled with the app for immediate offline reading. Canonical content
is `backend/app/data/parallel_reading_v1.json`; the byte-identical frontend copy is
`frontend/lib/data/parallel-reading-v1.json`, enforced by frontend/backend tests.
Treat released content as immutable: a future edition must bump content identity,
backend accepted version and storage key together, preserving old evidence.
Web still needs the app bundle loaded; this does not add an installable offline PWA.

Device-local journal: `@alif:parallel-reading:v1`. It contains each text's passage,
reread and completion state, the active text/catalogue view, supporting-language
preference, reveal/all-four/marks/size settings and an unsent event outbox. Updates
serialize read-modify-write operations. Progress plus event are committed in one
AsyncStorage value before the UI advances. Failed local writes keep the previous
bookmark; corrupt data is preserved and an error/retry surface is shown. Events
move into the existing durable sync queue and retry with stable event IDs.
Parallel-only synchronization does not invalidate vocabulary caches or emit the
learning-sync event that can refresh a hidden review screen.
Bookmarks do not synchronize across devices. Effort reflection is journal evidence,
not a persistent draft or a retention assessment.

`POST /api/books/parallel/events` validates content version, text, paragraph,
clue identity, support language, settings, action and timezone-aware client time.
It stores only `ReadingPilotEvent`, reusing the supported readers' idempotent
journal writer. Conflicting payloads for the same ID are rejected. Newly recorded
events also append `parallel_reading` to interaction JSONL. The durable database
journal is authoritative if a process dies between commit and JSONL append.

Actions: open/leave/library/select/passage/support/reveal/all/vowels/size/clue/about/
reread/complete/reflection. Events retain settings and exact text/paragraph identity.
Opening is not proof of reading, support use is not a failed vocabulary review,
and completion is not proof of unaided comprehension. There are no ULK, ReviewLog,
SentenceReviewLog, lemma, acquisition, exposure-ledger or due-date writes. There
are no runtime LLM calls, NLP imports, vocabulary classification or enrollment.

## Verification

Frontend tests cover independent bookmarks, concurrent edits, outbox retry,
failed commits, corrupt storage, content parity, language routing, last-tab
selection and sync-endpoint isolation. Backend tests cover content parity,
attributable events, idempotency, collisions and absence of scheduler writes.
Phone-sized browser QA exercises the reading flow, restart/resume, support modes,
large text, completion/reflection, failed-save recovery and direct links.
Physical iPhone cold-start/OTA behavior still requires device verification.

## Experiment aim

Use voluntarily for several sittings. The useful signals are voluntary return,
which supporting version resolves a difficult Arabic clause, and whether the
Arabic-only reread feels easier. Optional effort is descriptive, not controlled
learning evidence. Prepare further coherent pieces based on actual reading
feedback; do not automatically increase difficulty or introduce review debt.

Validation receipt (10 October 2026): all 25 frontend suites / 265 tests and
2,140 backend tests passed (9 slow tests deselected). iOS/web exports, layout
preflight and secure API configuration preflight passed. Local browser QA at
390×844 and 320×740 confirmed no horizontal overflow, a preserved passage after
simulated storage failure and successful retry, completion/reflection, root resume
and direct chapter-link precedence. Cold-launch transport capture contained no
review endpoint call. Browser transport was an isolated recording stub; backend
storage behavior was verified through hermetic pytest fixtures.


## Publication receipt — 10 October 2026

Published source: `314d26ffdc9c4fe2e0031b804b29cb62800355ae` (PR #287).
Backend main and the new journal OpenAPI schema are verified live; an invalid
empty event returns 422 without adding synthetic production reading evidence.
The private HTTPS reader now serves this commit through `/opt/alif-web/current`;
the previous static release `f901a1b447351ed1a0e2cdc82a48e9291f32cb77` remains
available for rollback. HTTPS deep-link HTML and bundled library/last-tab/journal
markers were verified with `private, no-cache` response policy. Legacy Metro was
restarted with its Alif-scoped generated caches removed.

Installed iPhone preview-channel OTA: update
`01a12675-132e-7564-8459-037a2c340177`, group
`44ea400b-4b4f-4550-b960-6f198ca2adbc`, runtime `1.0.0`.
The guarded publisher verified the private API URL in the published manifest.
The manifest source commit and downloaded launch asset SHA-256 match, and the
published bytes contain both longer texts and bookmark/last-tab keys. Asset
verification follows the update protocol's multipart extensions and its provided
asset-request headers; a naked CDN URL is intentionally unauthorized.

Fully terminate/reopen the installed app to let it download, then terminate/reopen
again to run the downloaded update. Physical iPhone cold-start and reading feel
remain for Stian to check. Server/OTA publication is complete; feedback and actual
reading benefits remain unmeasured. Local preview and owned worktree are released
and cleaned up after preserving the publication receipts and test/browser evidence.
