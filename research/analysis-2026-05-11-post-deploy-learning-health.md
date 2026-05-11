# Post-Deploy Learning Health And Mini-Story Attribution

Date: 2026-05-11
Data:

- Production DB snapshot copied with SQLite `.backup` from `/opt/alif/backend/data/alif.db` to `/tmp/alif-analysis-2026-05-11/alif.db`.
- Production logs copied for `interactions`, `llm_calls`, and `sentence_gen` on 2026-05-09, 2026-05-10, and 2026-05-11.
- Systemd journals checked for `alif-backend` and `alif-expo` since 2026-05-10 00:00 UTC.
- Production commit: `c12bf81` (`Add form-aware confusion telemetry`).

Notes: DB timestamps are UTC. "Yesterday" in Oslo on 2026-05-11 means 2026-05-10 local time, approximately 2026-05-09 22:00 UTC through 2026-05-10 22:00 UTC.

## Executive Verdict

Production looks healthy. I do not see a rollback signal.

The most important check passed: mini-story / passage reviews are giving learning credit to the words across the full grouped passage, not just the first sentence. Since 2026-05-10 UTC, I found 7 grouped or passage-like sentence-review cards. Expected schedulable lemmas across their grouped `sentence_ids`: 117. Actual `ReviewLog` rows credited under those review IDs: 117. Missing expected lemmas: 0. Extra credited lemmas: 0.

The latest production mini-story review on 2026-05-11 at 06:49:54 UTC was a four-sentence passage (`sentence_ids` 44952-44955, `story_id=39`). It credited 13 of 13 expected content lemmas, including one confused word (`اقام`, rating 2 / Hard). The interaction log also recorded `sentence_ids`, `words_reviewed=13`, `collateral_count=12`, `word_ratings`, and the candidate confusor list for the confused lemma.

There is one attribution limitation to keep in mind: per-word `ReviewLog.sentence_id` is intentionally set to the primary sentence ID of the card. That is enough for coarse attribution to `source="passage"` and `story_id`, but it does not preserve which exact sentence inside the passage exposed each credited word. If later analysis needs sentence-level causal attribution inside a passage, add a review context table or a `source_sentence_ids_json` / `passage_story_id` field instead of overloading `ReviewLog.sentence_id`.

## What Changed Yesterday

I read the top of `research/experiment-log.md`, the 2026-05-10 analysis/spec docs, and the recent git history. The relevant deployed work is:

- Maintenance passage cards and generated mini-stories entered the review flow.
- Passage generation was hardened and then improved for cohesive 3-5 sentence mini-scenes.
- The selector now reserves stored passage cards and only groups rows intentionally stored as `Sentence(source="passage")`.
- Hindawi passage-promotion research/specs were written for converting authentic windows into the same passage path.
- Proper-name handling was made inert for review credit.
- Form-aware confusion help and candidate telemetry were deployed in `c12bf81`.

## Learning Snapshot

Current production state:

| State | Lemmas |
|---|---:|
| known | 1,824 |
| encountered | 201 |
| acquiring | 86 |
| suspended | 85 |
| learning | 44 |
| lapsed | 29 |

All-time counts in the snapshot:

| Metric | Value |
|---|---:|
| Word review rows | 39,790 |
| Sentence review rows | 7,950 |
| Latest word review | 2026-05-11 06:49:54 UTC |
| Latest sentence review | 2026-05-11 06:49:54 UTC |
| Passage sentences in DB | 20 |
| Maintenance passage stories | 5 |

Oslo-yesterday window, 2026-05-10 local:

| Metric | Value |
|---|---:|
| Word reviews | 673 |
| Sentence review log rows | 134 |
| Sessions | 12 |
| Rating 3+ | 593 / 673 (88.1%) |
| Again | 51 |
| Hard/confused | 29 |
| LLM-source word rows | 586 |
| Passage-source word rows | 43 |
| Corpus-source word rows | 28 |
| Book-source word rows | 16 |

UTC day summaries:

| UTC day | Word rows | Rating 3+ | Again | Hard/confused | Collateral share | Sentence rows | Understood share |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-05-08 | 418 | 85.4% | 56 | 5 | 81.6% | 82 | 43.9% |
| 2026-05-09 | 460 | 86.7% | 41 | 20 | 82.0% | 86 | 50.0% |
| 2026-05-10 | 677 | 88.2% | 51 | 29 | 84.0% | 135 | 51.9% |
| 2026-05-11 | 13 | 92.3% | 0 | 1 | 92.3% | 4 | 0.0% |

Interpretation: review quality is not degrading after the May 10 changes. The May 11 row is only one passage review, so its understood share is not meaningful by itself.

Since 2026-05-10 UTC:

- 6 lemmas were newly introduced.
- 17 lemmas graduated.
- 11 lemmas were leech-suspended.

The suspension count is visible but not obviously bad. It matches the recent stronger leech behavior and the known difficulty cluster from the May 10 projection analysis. There were no database-lock or crash symptoms in the server logs.

## Mini-Story Attribution Check

The code path is doing the right high-level thing:

- Frontend sends the full passage group through `sentence_ids` via `passageSentenceIds()`.
- `submit_sentence_review()` builds `review_sentence_ids` from `sentence_id + sentence_ids`.
- It loads `SentenceWord` rows for all grouped sentence IDs.
- It dedupes by canonical lemma, skips function words / proper names / suspended lemmas, and credits every remaining content lemma.
- It creates one `SentenceReviewLog` row per grouped sentence, using `client_review_id` suffixes like `:s44953`.
- It creates word-level `ReviewLog` rows using IDs like `{client_review_id}:{lemma_id}`.

Observed production groups since 2026-05-10 UTC:

| Review base | Reviewed at UTC | Sentence IDs | Story | Expected lemmas | Actual review rows | Result |
|---|---|---:|---:|---:|---:|---|
| `15ab0e2e...` | 2026-05-10 15:29:52 | 4 | 37 | 21 | 21 | ok |
| `bcb6dccd...` | 2026-05-10 15:29:52 | 3 | none | 14 | 14 | ok |
| `0316efc8...` | 2026-05-10 15:37:46 | 5 | none | 26 | 26 | ok |
| `105281c5...` | 2026-05-10 17:49:14 | 1 | 35 | 8 | 8 | ok |
| `4b0d2738...` | 2026-05-10 18:03:39 | 4 | none | 21 | 21 | ok |
| `a15d9241...` | 2026-05-10 18:04:47 | 4 | 38 | 14 | 14 | ok |
| `841fc256...` | 2026-05-11 06:49:54 | 4 | 39 | 13 | 13 | ok |

Some grouped rows have `story_id = none` because older non-passage grouped cards were also present in the window. The generated maintenance-passage rows use `source="passage"` and `format_type="maintenance_passage"` as expected.

Latest mini-story review:

| Field | Value |
|---|---|
| Review base | `841fc256-134a-47eb-a9fb-55d29b87b64f` |
| Session | `cbce6ae7-db74-4b56-86b3-16583ccdcb54` |
| Sentences | 44952, 44953, 44954, 44955 |
| Story | 39 |
| Comprehension | partial |
| Response time | 162.5s |
| Expected / actual word rows | 13 / 13 |
| Confused lemma | 2804, `اقام`, rating 2 |

All 13 word-review rows point to `sentence_id=44952`, the primary sentence. That is the current fine-attribution limitation. But because 44952 is a passage row with the correct `story_id`, source-level and story-level attribution still works.

## Confusion Telemetry

Before the form-aware telemetry deployment, confusion events had `was_confused` on `ReviewLog` and `confusion_help` logs with counts, but no candidate ID list. After deployment, the latest synced sentence review includes:

- `confused_lemma_ids: [2804]`
- `confusion_candidate_lemma_ids: {"2804": [387, 2180, 3081, 396, 2980, 3064, 1022, 2830, 2845, 1044, 241]}`
- `word_ratings` showing lemma 2804 as rating 2

That is enough to start measuring whether the automatic candidate list contains the learner's likely actual confusor. Sample size is still tiny. The next useful threshold is around 50-100 confused reviews with candidate lists before making scheduling or UI policy decisions.

One small bug was fixed locally during this audit: the direct `/api/review/submit-sentence` route passed `confusion_candidate_lemma_ids` into the review service but did not include it in `log_interaction()`. The bulk sync route already logged it, which is why the latest production synced review was fine. The local patch makes both routes consistent and adds a regression test.

## Server And Generation Health

Prod services:

- `alif-backend`: active
- `alif-expo`: active
- Prod git commit: `c12bf81`

Backend journal since 2026-05-10 00:00 UTC:

| Signal | Count | Interpretation |
|---|---:|---|
| Tracebacks | 0 | good |
| Database locked | 0 | good |
| SQLAlchemy `SAWarning` | 43 | cleanup item, not user-facing failure |
| Self-correct batch generation failed | 18 | expected generation fallback noise, monitor |
| Mapping correction failed | 5 | expected validator discard path |
| Invalid HTTP request | 25 | likely internet scan noise |

Expo journal:

| Signal | Count | Interpretation |
|---|---:|---|
| Fatal errors | 0 | good |
| Require-cycle warnings | 14 | known warning |
| `expo-av` deprecation warnings | 7 | known warning |
| systemd kill-control-group messages | 8 | restart noise |

LLM call logs:

| Day | Calls | Failed | Failure rate | Main failures |
|---|---:|---:|---:|---|
| 2026-05-09 | 2,612 | 460 | 17.6% | mostly JSON parse failures and 8 Claude timeouts |
| 2026-05-10 | 2,599 | 67 | 2.6% | mostly JSON parse failures and 8 Claude timeouts |
| 2026-05-11 | 819 | 3 | 0.4% | 3 Claude 60s timeouts |

Sentence generation quality logs:

| Day | Quality reviews | Approved | Rejected |
|---|---:|---:|---:|
| 2026-05-09 | 125 | 48 | 77 |
| 2026-05-10 | 135 | 93 | 42 |
| 2026-05-11 | 22 | 19 | 3 |

The quality gate is actively rejecting material, which is desirable after the May 10 batch-quality fix. The high May 9 LLM failure rate looks like pre-fix / fallback noise; May 10 and May 11 are much cleaner.

## Pattern Check Against The May 10 Projection Analysis

The newest data still matches the prior pattern:

- Confusions remain concentrated in short / visually close forms and difficult verbs.
- The latest mini-story confusion was `اقام`, a verb whose exposed passage context looked like "she is staying," while the stored gloss is "to hold." That is exactly the kind of form/context mismatch the May 10 spec identified.
- Very short lemmas remain noisy. In the May 8-11 sample, `<=3` character lemmas had lower average rating than 4-8 character lemmas and much higher mean response time, though response time is skewed by long passage/card dwell times.
- Passage cards are not hurting top-line rating quality so far: passage-source word rows since May 10 had average rating 2.93 and confusion rate 3.6%, comparable or better than LLM-source rows.

The sample is not large enough to claim mini-stories improve retention yet. The best-case mechanism is now measurable: a passage review gives 10-25 contextual content-word reviews in a coherent scene, with grouped attribution and per-word ratings. The next analysis should compare subsequent review success for lemmas recently credited in passage cards versus matched non-passage sentence credits.

## Follow-Ups

1. Keep mini-stories enabled. Current data supports them.
2. Add a lightweight recurring health check for grouped reviews:
   - every `sentence_review` with `len(sentence_ids) > 1` should have `words_reviewed == len(word_ratings)`;
   - expected schedulable lemmas from `sentence_words` should match `ReviewLog` rows by `client_review_id` prefix;
   - alert on missing or extra rows.
3. Add explicit passage attribution if we start doing per-sentence causal analysis inside a passage. Today, `ReviewLog.sentence_id` is primary-sentence-only.
4. Clean the SQLAlchemy `SAWarning` instances in `word_selector.py` and `review.py`. They are not breaking prod, but they make log triage noisier.
5. Keep monitoring LLM fallback rates. The May 11 rate is healthy, but fallback can hide primary-model failures.
6. After 50-100 confused reviews with candidate maps, evaluate candidate recall. If the real mental confusor is often outside the list, add an explicit "I thought it was..." picker/search rather than broadening automatic candidates too far.

## Commands And Checks

Key checks run:

```bash
ssh alif "cd /opt/alif && git rev-parse --short HEAD && systemctl is-active alif-backend alif-expo"
ssh alif "sqlite3 /opt/alif/backend/data/alif.db '.backup /tmp/alif-analysis/alif_2026-05-11.db'"
scp alif:/tmp/alif-analysis/alif_2026-05-11.db /tmp/alif-analysis-2026-05-11/alif.db
scp alif:/opt/alif/backend/data/logs/{interactions,llm_calls,sentence_gen}_2026-05-{09,10,11}.jsonl /tmp/alif-analysis-2026-05-11/
backend/.review-venv/bin/python -m pytest backend/tests/test_sentence_review.py::TestConfused::test_confused_api_logs_candidate_lemma_ids backend/tests/test_sentence_review.py::TestConfused::test_confused_api_endpoint backend/tests/test_idempotency.py::TestBulkSyncEndpoint::test_bulk_sync_passage_sentence_ids -q
```

Test result: 3 passed.
