# Parallel Reading experiment

Implemented 10 October 2026. A shared **Parallel** tab and direct `/parallel`
bookmark offer Arabic-first reading with Ancient Greek, Latin, Russian and optional English.
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
Compare all stacks all five versions vertically. Reader controls include text size,
vowel marks under Aa, contextual Arabic word help, collapsible phrase clues, numbered passage navigation,
an Arabic-only whole-text reread, completion and optional effort reflection.
Passage reading hides the app tab bar to give the text room. A fixed bottom bar keeps
Previous/Next and passage position reachable while translations scroll. Library returns to
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

The catalogue shows passage counts; the word counts above describe the source
edition and include standalone punctuation, rather than NLP-derived vocabulary counts. Source URLs and version
provenance are visible in About. New translations are AI learning aids, reviewed
in preparation but not independently checked by a language specialist. Longer
pieces retain selective vowel marks only; the UI says so rather than claiming
full vocalization. Optional English is confined to phrase clues.

## Persistence and evidence boundaries

Content is bundled with the app for immediate offline reading. Canonical content
is `backend/app/data/parallel_reading_v2.json`; the byte-identical frontend copy is
`frontend/lib/data/parallel-reading-v2.json`, enforced by frontend/backend tests.
Treat released content as immutable: a future edition must bump content identity,
backend accepted version and storage key together, preserving old evidence.
Web still needs the app bundle loaded; this does not add an installable offline PWA.

Device-local journal: `@alif:parallel-reading:v2`. Edition-one bookmarks, preferences
and queued events are copied on first successful save from `@alif:parallel-reading:v1`,
without editing or deleting the old journal. Both event versions remain accepted. New server-only token/panel fields are omitted
when serializing a version-one event, so delayed retries still match pre-upgrade
stored payloads exactly. It contains each text's passage,
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
clue/token identity, display-panel identity, support language, settings, action and timezone-aware client time.
It stores only `ReadingPilotEvent`, reusing the supported readers' idempotent
journal writer. Conflicting payloads for the same ID are rejected. Newly recorded
events also append `parallel_reading` to interaction JSONL. The durable database
journal is authoritative if a process dies between commit and JSONL append.

Actions: open/leave/library/select/passage/support/reveal/all/vowels/size/clue/about/
reread/complete/reflection, plus word/display in edition two. Events retain settings and exact text/paragraph identity.
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

## Edition two — English and word help

The Arabic, Greek, Latin and Russian source strings from edition one are unchanged.
Edition two adds an English learning translation for every paragraph, and 338 exact
whitespace-token records, of which two are punctuation-only and not tappable.
Every Arabic word has a prepared contextual English gloss, including attached
particles, pronoun referents and feminine plural endings. These are editorial aids,
not dictionary/lemma identities, runtime AI answers or retention judgments.

Inline token presses preserve the full Arabic text, word order, source whitespace
and punctuation. The same token IDs work with hidden/selective/full vowel marks.
A word card shows the displayed form and meaning; close/backdrop/Escape returns to
reading. It uses no animation so web dismissal does not depend on CSS animation-end
callbacks. Word help also works in the whole-text Arabic reread; events carry the
actual clicked paragraph and exact token, even if it differs from the saved passage.
No lookup enrolls a word or changes knowledge/scheduling state.

English is an optional fourth support tab. Selecting a language switches to a pair
and reveals it; Hide translation restores Arabic alone. Compare all includes English.
Display controls and phrase clues are collapsed to reduce the main screen's clutter.
The word-card content scrolls if needed at large text sizes, while the return action
remains inside that scrollable content. Physical iPhone taps still need user verification.

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


Edition-two validation (10 October 2026): all 269 frontend tests / 25 suites and
2,150 backend tests passed (9 slow tests deselected). The 52 focused reader tests
cover edition-one stored-event retries, edition-two validation, exact-token help
and the continued scheduling boundary. Typecheck, iOS export and layout/API
preflights pass. Browser QA at 390×844 and 320×740 exercised English persistence,
word meanings including attached pronouns, keyboard dismissal, fixed navigation,
Compare all and normal-launch resume. Vowel-mode token identities are checked for
every paragraph. No horizontal overflow was observed.


Edition-two publication (10 October 2026): source
`411e3f35656f7352afec83c783e3fd6ea9b2eb02` (PR #288) is live in the backend
and private HTTPS reader. Both event versions, English and token identity appear
in the live OpenAPI schema; an invalid empty payload returns 422 without creating
synthetic reading evidence. The live JS contains English, v2 journal and word-card
markers. The prior web release `314d26ffdc9c4fe2e0031b804b29cb62800355ae` is retained
for rollback. iPhone preview update `01a12706-6cff-7d6b-89fd-ada4a3e110d8`, group
`c8a725c9-7912-47b0-bb1b-1f80e1ec2048`, runtime `1.0.0`, is published. Its source
commit, secure API configuration, downloaded launch-asset SHA-256 and English/word
help/v2-journal markers are verified. Physical device checks remain for Stian;
publication is complete. Local preview/worktree cleanup follows receipt preservation.
