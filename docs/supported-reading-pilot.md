# Supported reading pilot: Momo, first three sessions

Prepared September 5–6, 2026. Phase ID: `momo-wings`, content/protocol version 1.
Status: implemented; the learner trial starts with the first real opening after release. QA events in the local preview database are not learner observations.

## Aim and scope

Test whether a short, meaningful Arabic reading can be understood with help, reread more smoothly, and leave Stian wanting to continue. Energy is partly an outcome of the experience; recent review volume is not a fixed capacity. Reading takes roughly five minutes at the beginning of existing Arabic time and substitutes for some reviews. Extra reading is voluntary. There is no catch-up quota.

The first three portions are a checkpoint within the proposed ten-session trial. Prepare sessions 4–10 after learning what worked in these three. Do not invent a ten-session completion statistic from three available portions, or treat a rehearsed reread as fresh-text transfer.

## Reader

Open **Book Library → When stories grew wings**, or `/book-page?pilot=momo-wings`. This is a supported mode of the book-page route. It has an English orientation, clause-aligned Arabic/English, prewritten explanations of exact phrases and inflected forms, an explicitly adapted simpler Arabic retelling, and **Reread Arabic**. Rereading hides English and phrase notes; both remain available. Original Arabic retains the source transcription's spelling, punctuation and diacritics, with fully vocalized forms in the help and simpler retellings. No reading-time LLM calls or dictionary requests are required.

The source is `backend/data/momo_chapter_05.json`, printed pages 67–68. The content manifest pins its SHA256 and holds exact excerpts, 50/51/87 whitespace-delimited tokens respectively. These counts include punctuation tokens and are not canonical lemma counts. English alignments, phrase explanations and simpler retellings were prepared for this pilot; they are not attributed to a published English edition. The third portion begins Gigi's invented tale and intentionally ends before its resolution.

**Done for today** saves the next portion. **I'd like a little more** opens it immediately. **Pause here** retains the current portion and support state. After the third reread the reader presents the checkpoint. The two optional feedback questions ask whether the Arabic could be followed and whether the learner wanted to know what comes next.

## Evidence and learning state

`GET /api/books/reading-pilot` returns immutable versioned content. The whole sequence is cached on first successful opening for subsequent offline reading. Cache keys must change when releasing a content revision; do not edit a deployed version in place.

`POST /api/books/reading-pilot/events` writes only `reading_pilot_events`. Client event IDs are primary keys, making retries idempotent. Each event retains client time, server receipt time, content/session/attempt identity, read versus reread phase, support state and phrase-help identity, optional feedback, and cumulative foreground milliseconds per phase. The client first atomically saves its continuation and unsent event together, then hands events to the existing durable sync queue. A crash between enqueue and clearing the local outbox may duplicate deliveries, never database rows. Continuation is device-local; journal evidence is also backed up with the server database after sync.

Opening, help, translation, retelling, reread, feedback, pause and completion are separate events. Completion is attributed to the portion just read, and leaving does not record an unopened next portion. Cumulative times must be differenced or taken at the last event for each attempt, never summed across events. Foreground time includes English, explanations and inactive thinking; it is not pure Arabic reading speed. An opened portion is not proof that every rendered word was seen. App termination can lose time since the last interaction; background/route transitions attempt to checkpoint it.

This mode creates **no** ReviewLog rows, FSRS successes, acquisition starts or new vocabulary. It also does not increment the scheduler's exposure ledger. Analysts must include this separate supported-reading journal when considering intervening exposure; it is not evidence of unaided recall. No automatic blocker-to-SRS enrollment is implemented in this first checkpoint: use observed recurring difficulties to choose at most one or two later targets within the live intake budget. English use is free of vocabulary obligations.

## Phase boundary and decisions

Keep the September 3 maintenance policy intact. Run its operational check before production rollout of the reading phase; record first learner use as the start of reading substitution. Later retention changes cannot be attributed exclusively to the maintenance package. This feature adds no scheduled reminders or automatic daily prompts.

After three actual sessions, inspect feedback and ask what interrupted the story. If a supported reread is still laborious, adjust explanation, portion size or material difficulty. If the passage is understandable but uninteresting, switch to a historical narrative. Do not increase the workload merely because the trial was difficult.

If the first three work, prepare the rest of the sequence. At ten sessions, revisit the opening for visible improvement and assess fresh comparable passages under consistent support conditions. Use comprehension, assistance and voluntary return together. Retain important older-word recognition as a guardrail and report cohort/test coverage. No new lexical threshold or global retention retune is part of this pilot.

## Validation and release

Backend tests verify source fidelity, content/help identities, retry idempotency, rejected invalid evidence and the absence of vocabulary/review writes. Frontend tests cover reread support reset, continuation/checkpoint behavior, offline journal recovery, concurrent saves, corrupt-state preservation, cached content and retry routing. TypeScript and web export are checked, followed by a phone-sized browser walkthrough against an isolated local database.

Deploy the backend migration before releasing the frontend. The app can queue events while a connection is unavailable; a successful first content download is required before offline use. This is a web/React Native shared implementation; the browser walkthrough does not substitute for checking it on the learner's physical iPhone.

## September 15 read-only checkpoint and restart

Production remains at the September 3 maintenance baseline: this reader has not
been deployed. The [fresh checkpoint](../research/analysis-2026-09-15-reading-refresh.md)
found no instrumented operational stop signal, but older-word recognition and
recorded workload merit caution. It does not authorize a deployment or relax the
maintenance guardrails. [Three smaller prepared portions](../research/reading-refresh-2026-09-15/reading-next.md)
are available for conversation-based continuation, with vowel support and optional
rereading. They are prepared curriculum, not completed learner sessions; retain
the first actual reading as the phase boundary.
