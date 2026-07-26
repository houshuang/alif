# Alif learning-system metric definitions

Version: 1  
Frozen for baseline commit: `0dff93402b2449795f60eaa3f1f0625e906256c9`  
Timezone: UTC

This specification defines the lightweight WP0/WP1 baseline. It is intentionally
smaller than the future counterfactual replay program: it makes current stocks,
historical outcomes, FSRS calibration, and session behavior reproducible without
claiming to reconstruct every historical scheduler state.

## 1. Input identity and time boundaries

Every report is tied to:

- one SQLite snapshot, identified by byte size and SHA-256;
- every selected interaction JSONL file, identified by filename, byte size, and
  SHA-256;
- the analysis Git commit and analysis-script SHA-256;
- the installed `fsrs` library version;
- an explicit `window_start` and `cutoff`.

Time windows are half-open: `window_start <= timestamp < cutoff`. Naive SQLite
timestamps are interpreted as UTC, matching the backend's stored timestamps.

The current-state snapshot is interpreted at `cutoff`. A caller must not pass an old
cutoff with a newer mutable database and describe the resulting current stock as
historical. Offline synchronization can insert reviews with old `reviewed_at` values,
so a later live database is not a substitute for the pinned snapshot.

## 2. Review units

### 2.1 Valid stored word review

One `review_log` row is one stored word-review event when:

- `rating` is in 1–4;
- the referenced lemma exists;
- the lemma is not a function word, proper name, or onomatopoeia.

Undo deletes its `ReviewLog`; therefore a deleted review is absent rather than
separately filtered. `client_review_id` is unique in the schema. The report still
checks for duplicate non-null IDs and warns if any exist.

### 2.2 Sentence-word review

A valid stored word review with a non-null `sentence_id`. Reading, listening, and quiz
remain separate diagnostic modalities. The primary retention/calibration dataset is
sentence reading: `review_mode='reading'` and `sentence_id IS NOT NULL`.

### 2.3 Primary and collateral

`credit_type` describes why the sentence was selected, not the validity of the word
outcome. Primary and collateral sentence-word rows:

- count equally as word reviews;
- carry the same ratings;
- are both eligible for acquisition, graduation, scheduling, and retention outcomes.

They are reported as diagnostic slices only. The sole admission exception in this
baseline is the recovery intake-permission calculation: independently answered
primary reading cards are the effort unit, so collateral fan-out cannot manufacture
permission to introduce new words.

### 2.4 Distinct reviewed word

Distinctness uses the root canonical lemma ID after following the full
`canonical_lemma_id` chain. Physical review-row counts are never collapsed.

## 3. Current stocks

### 3.1 Acquisition stock

Physical `user_lemma_knowledge` rows with `knowledge_state='acquiring'`, grouped by
`acquisition_box`.

- `due`: `acquisition_next_due < cutoff`.
- `zero_correct`: `times_correct` is null or zero.
- `age`: elapsed time from `entered_acquiring_at`, falling back to
  `acquisition_started_at`.

Recovery Box-1 actionable and Box-2 due reproduce
`acquisition_service._recovery_backlog_counts`:

- Box 1 counts never-reviewed rows regardless of due time and previously reviewed
  rows once due;
- Box-1 rows under active generation backoff are excluded;
- Box 2 counts due rows;
- inert categories are excluded.

### 3.2 FSRS debt

An FSRS row is due when its parsed card `due < cutoff` and its knowledge state is
`learning`, `known`, or `lapsed`.

- `raw_actionable_due` excludes function/inert lemmas.
- `strict_main_due` additionally applies the production main/slow frequency-lane
  classifier and removes variants shadowed by a known/learning canonical.
- Stability bands are `<7d`, `7–30d`, and `>=30d`.

The recovery gate is active when any of:

- actionable Box 1 ≥5;
- due Box 2 ≥30;
- strict main-lane FSRS due ≥750.

This reports admission pressure; minimizing it is not the learning objective.

## 4. Historical outcomes

### 4.1 Accuracy

Success is rating ≥3. Accuracy is always accompanied by its denominator. Primary and
collateral accuracy are diagnostic slices, never unequal credit rules.

### 4.2 Graduation

A graduation in the window is a current ULK whose `graduated_at` falls inside the
window. Duration is `graduated_at - entered_acquiring_at`, falling back to
`acquisition_started_at`. This counts the latest stored graduation timestamp and does
not reconstruct repeated historical treatment episodes.

### 4.3 FSRS calibration

Eligible rows are valid sentence-reading, non-acquisition reviews with:

- a stored `fsrs_log_json.pre_card`;
- pre-review stability and due time;
- actual review time at or after the stored due time.

Predicted retrievability is computed from that pre-card at the actual review timestamp
using the installed FSRS library. Thus lateness is included. Results are grouped by
pre-review stability and month, reporting:

- count;
- mean predicted recall;
- observed success;
- Brier score;
- median lateness.

Historical card states may have been produced by older FSRS/application versions.
This report measures current-library calibration against stored pre-states; it does
not authorize parameter deployment.

## 5. Interaction-log session behavior

An analyzable session has at least one planned `card_shown` carrying integer
`card_index` and `total_cards`. Checkpoint, wrap-up, grammar, and other out-of-band
cards are not planned-card positions.

Approximate completion requires:

1. display of a planned card where `card_index == total_cards - 1`; and
2. a `sentence_review` in the same session at or after that display.

Distinct rating-1/rating-2 words are unions of `sentence_review.word_ratings` by
session. Consequently, `no_idea` correctly fails every eligible word recorded in that
sentence. This metric is approximate because client-local transitions not logged by
the app cannot be reconstructed.

Automatic wrap-up workload comes only from `card_shown` events with
`card_type='wrapup'` and `detail.variant='wrapup_auto'`. Generic
`wrap_up_quiz.card_count=1` events are not treated as auto-wrap because checkpoint
fetches use the same endpoint.

Protocol-v2 retry telemetry is identified by `detail.v == 2`. Pre-v2 deliveries are
excluded from the future primary retry readout.

## 6. Determinism and read-only guarantees

The analysis command:

- opens the pinned SQLite snapshot with URI `mode=ro&immutable=1`;
- enables `PRAGMA query_only=ON`;
- in `--strict-read-only` mode rejects snapshot WAL/journal sidecars and verifies the
  database and selected log hashes before and after analysis;
- emits sorted, stable JSON and CSV with no wall-clock generation timestamp;
- writes only inside the requested output directory.

Two runs with identical bytes, arguments, code, and library version must emit
byte-identical artifacts.

## 7. Explicit limitations

This baseline does not yet:

- reconstruct historical daily stocks or prove a full stock-flow identity;
- replay scheduler/acquisition state across algorithm-version changes;
- estimate causal effects of re-exposure timing;
- resolve historical nullable acquisition episode kinds;
- infer missing client/offline transitions;
- recommend FSRS weights, Box-1 repetition changes, or graduation changes.

Those belong to bounded selector replay or the version-segmented deep lane.
