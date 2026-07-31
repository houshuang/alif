# Complete token presentation evidence

## Purpose

Protocol v2 records what was actually displayed for every mapped token on a
reading card. Scheduling remains canonical-lemma based. The ledger exists so
form, tashkīl, function-word, and presentation effects can be analyzed without
inventing independent SRS cards.

Protocol v1 recorded schedulable content words only. Protocol v2 also records
function words and proper names. These additional rows are exposure-only: they
do not create knowledge rows, receive FSRS/acquisition credit, become due, or
enter the exact-form scheduling experiment.

## One immutable row per displayed token

Each row is keyed by client review and `sentence_word_id` and stores:

- sentence, token position, surface, lemma, and canonical lemma;
- exact front rendering and whether tashkīl was initially, ever, and finally
  visible before the answer;
- front/back toggle counts and answer-reveal state;
- back-side tashkīl visibility at rating time;
- token outcome and optional assisted-recognition cause;
- whether the token was schedulable content, a function word, or a proper name;
- whether the token outcome came from an explicit token mark or the whole
  sentence comprehension judgment; and
- the linked `ReviewLog` when scheduling credit exists.

Function-word and proper-name rows normally have no linked `ReviewLog`. Their
rating is explicitly labelled `sentence_comprehension`; it is evidence about
the displayed sentence judgment, not a claim that the learner independently
retrieved that token.

## Validation and compatibility

The backend validates token identity and surface against authoritative
`SentenceWord` rows, reconstructs expected rendering, checks toggle-state
consistency, and derives token role from the authoritative lemma. Bad telemetry
is dropped without blocking or changing the review.

Cached protocol-v1 clients remain accepted. They continue to record content
tokens only. Protocol-v2 clients submit all mapped tokens. Undo deletes all
presentation rows attached to the client review.

Read-only analysis:
`backend/scripts/analyze_word_review_evidence.py`.
