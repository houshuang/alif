# Confusion learning (local implementation, 23 November 2026)

A pair is a learner-reported distinction between two canonical lemmas. Members are
stored in ascending ID order; reports in either direction share one pair. This is
separate from FSRS, word acquisition, sentence ratings and the fragment follow-up
scheduler. It adds teaching exposure, which must be included as a co-intervention
in the ongoing `fragment_followup_v1` readout before activating it in production.
The existing hold on red-word named capture remains intact.

## Intake and manual text

`POST /api/confusions/refresh` imports unlinked captures, idempotently. Selected
word IDs link directly after canonicalization. Free text is stripped of invisible
direction marks and retrieved against the existing vocabulary with
`limbic.hippocampus.resolve` (Arabic forms and English gloss aliases). Only an
unambiguous exact/folded hit at score >= .93 with > .02 separation auto-links;
fuzzy candidates are confirmation choices only. A description also matching the
failed word never auto-links another word. “Forgot”, “Forget”, “No idea”, etc. are
retained as forgetting notes, not fabricated lexical pairs.

More → Confusion pairs has separate Pairs and Needs matching views. A manual entry
can be searched in Arabic/English, confirmed against an existing word, or marked
as a forgetting note. Original text and capture remain intact. Resolutions live only in the new teaching
link; they do not fill the legacy capture `resolved_lemma_id` consumed by sentence
selection. This prevents intake from silently changing the ongoing trial's selector. Marked notes can
still be searched and reassigned. Canonical self-pairs are refused with an
explanation; they are form-recognition problems rather than a second lexeme.

Local read-only production intake on 23 November: 80 captures, 47 selected and
33 manual entries; 54 canonical pairs, 17 ambiguous descriptions and four forgetting
notes. “Happy” and “judge” remain confirmation choices; “wheat” links directly.
These are private inputs, not committed fixtures or a production backfill.

## Teaching and session practice

The pair library prepares a contrast lesson on request. Reading also prepares a
pending pair in the background. Provider work occurs without an open database
transaction or write lock; an atomic, 30-minute generation claim prevents duplicate
paid work. A failed preparation is retryable and never serves unchecked teaching.

Generation uses the existing completion gateway: distinction, specific cue for
each word, and four short Arabic cloze exercises (two targets each). A second
call independently checks Arabic, meanings, distinguishing cues, ambiguity and
answer forms. One repair/re-audit is allowed. Claimed base-letter changes,
missing blanks, malformed output or failed checks prevent publication. Cloze
options can adjust final case vowels (including the orthographic accusative
tanwin alif), but cannot change lexical base letters; the audit
also checks internal lexical vowels and the lexeme. Two gloss-to-word questions
precede the clozes, giving six questions. Meaning questions use stored glosses.

Ready lessons are cached on device. New reports are prioritized over older prepared pairs. A newly entered ambiguous
confusor can ask for a match at the next submission boundary, with an optional
“Match later” exit; it never interrupts a sentence. A clear new pair prepares in
the background even while older lessons are available. At a reading-sentence
submission boundary, after two normal cards, a lesson and two exercises may appear. Later two-card
gaps offer the next exercises. They also remain visible if the boundary leads
to Today/wrap-up/session end; the next reading timer stays held. Background
preparation never opens a modal in the middle of reading. Close skips that pair
for the rest of the current session, without scoring a failure. The library can
run all six exercises consecutively. Choice order varies; meanings and full
sentence translation appear only after an answer. Help records an assisted,
unsuccessful attempt rather than correct recall.

Answers are graded by the server from the stored question, not a client-supplied
correctness value. Client attempt IDs are idempotent; conflicting reuse is 409.
Offline attempts use the durable sync queue and never trigger the normal review
cache/synced event. Progress is authoritative after sync. Network failures retain
answers locally; unavailable lesson generation and confirmation expose retry.

## Separate progress

Statuses are computed from underlying reports, attempts and current review
records; undo therefore removes its evidence from the calculation.

- Active / “Still confused”: no sufficient post-report success yet.
- Partially resolved / “Improving”: at least two unaided successful question/session
  combinations covering both members, after the latest report, mixed-up member
  mark, or unsuccessful/assisted exercise.
- Resolved: at least four such successes covering both members, plus both members
  seen in **three distinct clean sentence contexts across at least two dates**,
  all more than 24 hours after the latest reset. Clean means validated protocol-v3
  reading evidence with rating 3, no causes, and the sentence's server-computed
  `reading_timing.eligible=true`. No report, legacy timing, repeated context, or
  immediate coached success can substitute for this evidence.
- A new named report, mixed-up member mark, or unsuccessful/assisted exercise
  reopens recovery. Opening a lesson alone changes no status.

These conservative thresholds are a product starting point, not a calibrated
probability of mastery. The interface separately shows exercise correct/total,
clean-context count and clean dates. FSRS is never updated by this subsystem.

## Storage and API

- `confusion_pairs`: unique canonical member IDs; immutable checked lesson;
  generation claim/state/error.
- `confusion_links`: one intake result per original capture; pair or confirmation
  candidates. Capture deletion cascades to the link so existing undo is safe.
- `confusion_attempts`: durable session/question/choice/assistance/server grade.
  `question_index=-1` denotes teaching exposure, excluded from scoring and reset
  calculations, retained for co-intervention analyses. Nonnegative indices are
  scored questions. Pair views with no remaining capture links are hidden.

Migration `a2d4f6c8b019` follows `f3b5d7e9a1c4`; no historical timing or FSRS backfill.
Routes: `GET /api/confusions`, `POST /refresh`, `GET /search?q=…`,
`POST /captures/{id}/confirm`, `POST /{id}/prepare`, `POST /{id}/attempts` (all under
`/api/confusions`). Local preview data and generated examples are private.

## Touch explanations

The side rail retains 44pt targets and hover/focus descriptions. Short names are
visible under icons. A 350ms hold shows the full legend for 2.5s and suppresses
the toggle action; it never changes a word mark or reason. Timer cleanup prevents
legends persisting after navigation.
