# Arabic reading refresh — September 15, 2026

The maintenance backlog has improved, but the reading transition remains untested in production. Continue a small supported-reading trial within existing Arabic time. Keep maintenance and the restricted intake while checking older vocabulary; neither the lower aggregate accuracy nor a better matched subset justifies a global scheduler change.

## Current evidence

A fresh SQLite online backup was taken September 15, approximately 09:23 Oslo time. The snapshot passes full integrity checking and its SHA256 is unchanged after analysis. Database cutoff: 07:22:55 UTC, just before capture. Latest stored review was earlier than this cutoff. Interaction logs were freshly copied, including compressed archives. Production remains at `5f212bcaaf51a3096eb768e213f15a27712728ef`; analysis uses committed main `2d859666042aa02054f1ff6434ac7cf68ba949db`. The supported Momo pilot is merged but not deployed. No application, production configuration or learning records were changed.

The standard [maintenance checkpoint](reading-refresh-2026-09-15/maintenance-checkpoint.json), [learning-system report](reading-refresh-2026-09-15/learning-system/summary.json), and [supplement](reading-refresh-2026-09-15/supplement.json) retain denominators, provenance and interpretation limits.

| Measure | Refreshed result | Interpretation |
|---|---:|---|
| Persisted sentence reviews since September 6 | 255, on 9 UTC dates through this morning | Stored submission dates can reflect offline synchronization; these are not exact daily active-time measurements |
| Activity distribution | Median 15 cards per recorded active date; 119 recorded September 13 | Activity varies substantially; do not infer a fixed capacity from the median or recommend catch-up marathons |
| Canonical content lemmas marked known | 2,641 vs 2,629 in September 5 analysis | +12 net state labels; not 12 newly learned words or fluent surface forms |
| New acquisition starts since September 3 activation | 0 | Current acquisition-episode fields show no true-new intake |
| Acquisition graduations since September 6 | 14 | Work is consolidating older intake |
| Actionable Box 1 | 41 at activation → 19 now | A smaller early-learning backlog |
| Total acquiring | 56 → 37 | Remaining items still include longstanding difficulties |
| Strict main FSRS due | 744 → 718 | Small net reduction, not a steady decline |
| Snapshot trajectory | 905 September 12 morning → 660 September 14 morning → 718 now | The large September 13 recorded batch accompanies much of the recent decline; due counts also rise with elapsed time |
| Scheduled reading word judgments since assessment | 642/856 clean, 75.0% | Excludes inert identities; clean means rating ≥3 and no confusion in legacy rows |
| Explicit cause tags | 17 of 1,898 token evidence rows | 7 unfamiliar-form, 7 mixed-up, 2 missing-tashkeel, 1 retrieval-lapse tags; too sparse to estimate population proportions |

The canonical state count uses the September 5 classification: exclude function words with overrides respected, proper names and onomatopoeia, and count canonical identities only. The standard report's 2,705 known rows is a different, unfiltered state count.

No new book/reader event appears in the copied logs. Momo's persisted reader location remains page 1, token 152, last passage completed August 22. This says nothing about reading elsewhere or unrecorded reading in conversation. The September 6 assessments and proposed Momo miniature are separate evidence. A prepared passage must not be recorded as completed.

## Maintenance is operational, with a retention warning to interpret

Across the 11.89-day policy window, 49 instrumented session builds recorded no four-obligation-cap breach and no automatic passages. All 941 logged mature-collateral exposure events meet the narrow exposure predicate. No automatic stop signal fires. Fourteen confusion-rescue selections concern four distinct captures; selection is not confirmed completion or successful treatment.

The standard checkpoint flags old-word ≥7-day recognition: **267/346, 77.2% clean**. Its ≥14-day figure is **144/207, 69.6%**. The ≥14-day warning is not yet mechanically enabled because the experiment is younger than fourteen days; that does not make the current observation reassuring. The workload flag is descriptive, not evidence that the learner should be made to do more.

Do not compare these percentages directly with the old 89% headline. The new policy removes easy early collateral successes from the scheduled denominator and emphasizes fragile words. There is also a measurement limitation: the standard checkpoint builds gaps from token evidence only and measures time since the previous scheduled event, not every recorded exposure.

The supplement therefore combines linked token evidence and the longer ReviewLog history, includes quiz and unscheduled token appearances in the exposure clock, and uses the same fixed older cohort on both sides (first-learning field at least 90 days before activation; 2,293 eligible canonical content lemmas). There is still no complete clock for external reading, lookup-only exposure, or offline occurrence times.

| Older-word checks after ≥7 days since any recorded encounter | Before policy, previous 12 days | Since policy |
|---|---:|---:|
| All eligible observations | 536/622, 86.2%; 610 words | 327/445, 73.5%; 430 words |
| Same 124 words eligible in both windows, last qualifying result per word | 84/124, 67.7% | 108/124, 87.1% |

In the matched subset, 31 words changed from non-clean to clean and 7 changed the other way. The initially much lower 67.7% in this selected subset itself illustrates selection toward fragile words. This is not random sampling or a controlled treatment effect: forms, contexts, intervals and intervening practice differ, and eligibility is conditioned on later testing. It does, however, undermine a simple claim of general deterioration based on the aggregate percentage. Only 430/2,293 older words have a qualifying delayed observation in the policy window; do not generalize retention to the untested remainder.

Using the full history identifies more legitimate prior encounters than the checkpoint's token-only history, so its denominators differ; this is a sensitivity analysis, not a silently revised checkpoint. Including quiz/unscheduled exposure changes the supplementary policy result little (73.7% using scheduled gaps versus 73.5% using all recorded gaps). Neither approach establishes fluency.

## Timing and telemetry limits

The standard policy-window response median is 32.6 seconds, p90 81.4 seconds. These include looking away, English/reveal interactions and other activity. They must not be converted into a reading-speed trend or a daily time prescription.

Use persisted sentence-review rows for completed-card counts. The checkpoint's `interaction_telemetry.daily_sentence_cards` counts cards offered at session construction, not answered cards. Separately, 452 raw sentence-review events occur in the policy window versus 331 persisted sentence reviews. They are not interchangeable denominators; the cause of that discrepancy is not established here. All client/sentence groups in token evidence have a matching persisted sentence review. No conclusion of lost user work follows solely from excess events. Follow up on instrumentation before using event counts for a precise adherence metric.

There are 49 cards with at least five scheduled judgments, despite zero logged due-density breaches. These are different concepts: all scheduled judgments can include not-yet-due fragile collateral, whereas the selection cap concerns actionable due obligations. Do not report this as a proven cap violation, or assume the cap measures subjective sentence difficulty adequately.

## What to change in the learning process now

1. **Make returning easy.** Each visit starts with a one-sentence recap and the next small meaningful portion. After an interruption, resume without another assessment, a catch-up quota, or reconstructing where the conversation stopped.
2. **Try reading before the review queue consumes the session.** Allocate roughly 3–5 minutes from existing Arabic time for each of the next three visits, as a provisional experiment. The text can be shorter. Keep a maintenance component; the old-word warning argues against a large immediate reduction. Do not treat the due count as an entrance requirement for reading.
3. **Help should be immediate and specific.** Begin with vowel marks available, brief context, and a few useful phrase glosses. Full translation remains available. If meaning stalls, use it freely. Do not make the learner prove every word before continuing.
4. **Use an optional reread, then move the story forward.** September 6 provided a promising change from puzzle-solving to flowing on the same story (85.8 to 52.6 seconds), with assistance confounded. Test whether a new continuation is manageable rather than merely perfecting the opening.
5. **Track meaning and effort with one short response.** After a portion: could the image/action be followed, and which phrase still required solving? Only distinguish unknown meaning, slow familiar-form recognition, and a sentence relationship when a blocker actually occurs. Avoid tagging every word.
6. **Practice at most one or two recurring blockers in other clear sentences.** Examples worth noticing in recent reviews include تَسَاءَلَ / يَتَسَاءَلُ and تَابَعَتِ / فَسَأَتَابِعُ. These are candidate observations, not a prescribed vocabulary list. Choose practice for the reading at hand; a repeat miss on جَعْبَة (quiver) need not displace a phrase that unlocks the next scene. No automatic SRS enrollment or suspension is authorized by this analysis.

After three actual readings, decide whether material, explanation or continuity needs adjusting. A comfortable supported reread counts as an intermediate success. Evidence of broader progress requires fresh text under similar support conditions, with preserved meaning and willingness to continue. Keep timing optional and interpret it only alongside attention and comprehension.

Prior assessment corrections remain authoritative: deserts and woman/man were learner-confirmed misclicks; years/hours was a genuine reported confusion; here/there required thought. None is a new September 15 test result. The user's report that energy depends partly on the app experience remains central: enjoyment and voluntary continuation are outcomes to improve, not fixed inputs to constrain forever.

## Reproduction and delivery

Run the existing checkpoint with `--start 2026-09-03T10:00:00Z`, the pinned snapshot, copied logs and cutoff above. Run the existing learning-system analyzer with `--window-start 2026-09-06T00:00:00Z --strict-read-only`; its required FSRS 6.3.1 dependency is available in the established backend venv. Run [refresh.py](reading-refresh-2026-09-15/refresh.py) for the supplementary calculations. Raw database and logs stay in private local storage; report artifacts retain their checksums. Backup filename times used for the debt series are interpreted as Europe/Oslo.

Prepared [three small restart portions](reading-refresh-2026-09-15/reading-next.md), derived from the existing Momo pilot and explicitly labeled for source/adaptation. These are ready to read, not completed learner sessions. This research update does not deploy the reader or alter the maintenance experiment. Subjective review difficulty and outside reading since the assessments were asked during this refresh and remain pending at report preparation.
