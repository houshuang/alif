# Supported chapters

Implemented September 16, 2026. Content identity `library-bridge`, version 1.
Entry: **Book Library → The bridge in the book**, or bookmark `/read` on the
Alif web host. `/read?chapter=drawing` and `/read?chapter=bank` open a particular
chapter. The bare bookmark resumes the last chapter used on that device.

## Reading design

One continuous scroll per bounded chapter, with paragraph breaks and an explicit
Finish chapter button. The first book has two chapters of 139 whitespace-delimited
Arabic tokens each. Chapter 1 gathers the three library scenes already read in
conversation; chapter 2 is a new continuation and ends the story. These are original
graded fiction, not excerpts from Momo or historical claims. Paragraph-aligned
English, fully vowelled Arabic and exact-token contextual glosses are editorial
content. There is no runtime generation or guessed lemma mapping.

Before each chapter, at most six words/phrases are offered as reminders. The learner
can immediately begin without doing anything with them. The reminders are available
again while reading. This is temporary support, not vocabulary introduction or a
retention test. Some words may be well known but slow to recognize in this form.

Arabic appears first, with vowel marks on. Every word can be tapped; help preserves
the complete surface and explains important prefix boundaries. English is revealed
independently for each paragraph. Vowels can be hidden or restored. No forced reread,
quiz, per-word marking, visible timer, daily target or automatic next chapter.

Finish is independent of feedback. The optional reflection offers three effort
choices, a typed note, and a voice recording of up to one minute in any language.
Stop first to preserve a voice draft; listen/discard/re-record before sending it
with Save reflection. Completion is still possible without a reflection. Drafts
are kept when leaving for another chapter. Voice notes are recordings for later
review; this release does **not** automatically transcribe them.

## Reused surfaces and boundaries

The chapter route uses the existing book library, paper palette, Arabic font and
shared Expo/web stack. It reuses the supported pilot's isolated event-table pattern
and existing durable sync queue, rather than the normal book reader's completion
or lookup-credit APIs. Existing Momo v1 content, storage keys and events are unchanged.
`expo-av` already supplies audio playback and now supplies recording; the existing
camera config plugin already includes iOS microphone permission. File-system access
is pinned to the already-locked SDK 54 `expo-file-system` 19.0.21, now declared directly.
No new native module or runtime version is introduced.

## Persistence and evidence

`GET /api/books/chapters` returns the versioned book, cached in AsyncStorage after
the first successful download. Never edit a released version in place: change both
content version and cache/journal keys for a new revision. Reading works offline
after caching; a fresh web navigation still needs the web app itself to be available
(this is not an installable offline PWA).

Device-local journal: `@alif:chapters:library-bridge:v1`. Each chapter has its stage,
scroll position, vowel/translation settings and text/voice draft. Updates serialize
read-modify-write operations. A submitted action atomically stores progress plus an
outbox event, then hands it to `sync-queue.ts`. Failed handoff leaves the outbox intact;
duplicate delivery uses the same event ID. Corrupt saved data is surfaced, not reset.
Completed recordings are base64 drafts, capped at 1.5 MB decoded; a submitted voice
note moves into the durable outbox before its draft clears. A recording still in
progress cannot be guaranteed to survive a browser/process kill.

`POST /api/books/chapters/events` writes only `reading_pilot_events`, with
`reader_id`, version, chapter and attempt identity, client/server time, action,
stage, vowel state, exact paragraph/token where applicable, and optional reflection.
Actions: open/start/word/translation/vowels/preview/pause/complete/feedback/reread.
Rereading is an explicit event within the same saved chapter attempt, with English
hidden again. Do not count it as independent fresh-text transfer. Opening does not
mean all words were read; lookup does not mean a forgotten lemma; finish does not
prove unaided comprehension. No speed estimates are collected in this first version.

`POST /api/books/chapters/voice` accepts `{event, mime_type, audio_base64, duration_ms}`.
Supported containers: WebM, MP4/M4A and Ogg. The server bounds and validates the
payload, writes a complete content-addressed blob under `backend/data/reading-voice/`,
then commits its journal reference. Retries reuse the blob and event. The database
stores its filename, MIME type and duration, not the base64 body.
`GET /api/books/chapters/voice/{event_id}` retrieves a recorded note via the same
private API. Back up **both the journal database and that audio directory**. There
is no automatic transcription, deletion, retention expiry or public media URL.

No ReviewLog, SentenceReviewLog, ULK, acquisition, exposure-ledger or due-date writes
are made. Do not use normal book completion, review lookup or introduce endpoints
from this reader. This continues the supported-pilot evidence boundary; it is not a
change to the foundational scheduler-credit policy for reviewable sentences.

## First trial and next decisions

Use one short chapter when convenient. The prior conversation reports fast, fluent
reading with vocabulary previews and coherent context, including recognition of
whole word shapes. A brief reparse of وَرَقَة recovered the ending; attached-prefix
boundaries and missing vowel marks remain plausible sources of effort. These are
self-reports, not controlled evidence that previewing or rereading caused gains.

After roughly three to five chapter sittings, use optional effort/voice/text reports,
the exact help locations and voluntary continuation to decide what to change:

- Comfortable new text: offer more connected content at similar difficulty before
  increasing the vocabulary burden. The known chapter is not the fresh-text test.
- Repeated form/prefix friction: add narrowly targeted notes and reminders in the
  next content revision, without turning every lookup into a new review card.
- Tiring despite comprehension: shorten chapters or change subject matter; energy
  is an outcome of this experience, not a fixed learner trait.
- Comfortable length but awkward navigation: consider pagination only after actual
  use; paragraph spacing and typography can be adjusted without changing content.

Two chapters are the initial content supply, not an entire graded course. Preparing
more coherent chapters is the next curriculum task after feedback. There is no
automatic story generator or rule that every chapter must be harder than the last.

## Verification and release

Backend tests cover bounded exact-token content, invalid identities, scheduling
isolation, idempotent events, voice validation/storage/retrieval and conflicts.
Frontend tests cover content caching, offline handoff recovery, atomic voice draft
submission, independent bookmarks, concurrent edits and corrupt-storage preservation.
Browser QA uses an isolated empty database, not learner telemetry. Phone-sized web
layout is checked separately from desktop; physical iPhone recording still needs
device verification. HTTPS is required for web microphone access (localhost is the
development exception). Do not give the existing plain-HTTP Metro URL as a working
voice-enabled bookmark. Production needs the private HTTPS web entry and backend
routes before web/OTA release. The existing reading-pilot migration must already be
applied; this feature adds no schema change.

### Private HTTPS web release

The HTTPS host already serves other applications at its root. Export this app
with `ALIF_WEB_BASE_PATH=/<private-capability>/reader` and the usual private
`ALIF_API_URL`, then install the output under `/opt/alif-web/releases/<commit>`.
Point `/opt/alif-web/current` at that release. Render
`deploy/alif-reader-web.conf.template` into a root-readable nginx snippet with
the existing capability, include it in the HTTPS server, run `nginx -t`, and
reload. Back up the previous nginx config before changing it. Keep export logs
private: Expo prints its base path. Never expose these bundles at public asset
paths: their configuration contains the API capability.

The bookmark is `https://<host>/<private-capability>/reader/read` (append
`?chapter=bank` to start the new second chapter). Client routing and font/JS asset
paths must all retain this prefix. Verify direct deep links, reloads, library
navigation and paragraph/word help after publishing. Leave `ALIF_WEB_BASE_PATH`
unset for `scripts/publish-ios-update.sh`; native routing stays unchanged.
