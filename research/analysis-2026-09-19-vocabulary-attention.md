# Vocabulary selection should buy reading progress, not indefinite maintenance

September 19, 2026. Read-only investigation; no production learning state, scheduler, imports, or deployment changed.

**Recommendation:** keep a smaller, deliberately chosen maintenance curriculum, make the next reading passage the main source of new learning priorities, and let other words remain available as glosses without becoming recurring obligations. Improve frequency evidence and lexical identity where they change those decisions. A wholesale replacement frequency list is neither the first dependency nor a complete solution.

The learner's near-term objective is modern novels, with classical literature later. Momo is the existing concrete target used here; translated Momo alone cannot represent all contemporary Arabic fiction. No new author preference was supplied during this investigation.

## What production actually shows

I took a fresh SQLite online backup on September 19, checked its integrity, and analyzed all **75,105 review rows**, with detailed comparisons from June onward and a maintenance window of **September 3, 10:00 UTC–September 19, 13:17 UTC**. Latest learner review: September 19, 09:44 UTC. September interaction logs include compressed files. The database checksum was unchanged after analysis.

Production is on **`f901a1b` (September 16)**. Local and freshly checked remote main are **`c2fedbe2`**. The September 17 Box-1/leech amendments, the September 17 general FSRS retention reduction, and the September 18 rank repair are merged but **not deployed**. Production's ordinary scheduler still constructs `Scheduler(desired_retention=0.95)`; its separate assisted-lapse scheduler uses 0.90. The service restarted on September 18, but its checkout did not advance. Restarted does not mean updated.

The existing maintenance checkpoint reports:

| September 3–19 measure | Result |
|---|---:|
| Recorded reading sentence reviews | 517 |
| Active days / median reviews on an active day | 15 / 32 |
| Median / 90th-percentile recorded response time | 32.6 / 80.6 seconds |
| True-new intake under the existing checkpoint definition | 2 |
| Strict main FSRS due | 708, versus 744 at activation |
| Actionable Box 1 / due Box 2 | 17 / 12 |
| Clean scheduled judgments | 75.8% of 1,636 |
| Old words tested after ≥7 / ≥14 days since scheduled review | 78.1% of 470 / 73.2% of 299 |

There is modest debt reduction, almost no new intake, and substantial difficulty maintaining old words. These are observational results, not a causal test of a component. Long-gap figures use the checkpoint's **scheduled-review clock**, not a guarantee of no intervening exposure. They cannot be read as unassisted novel comprehension. Response time includes reading, thinking, interface use and interruptions; it is not pure study time.

The new audit uses canonical, non-inert content words with the production function-word override. Its denominator is **1,634 scheduled judgments**, rather than the older checkpoint's 1,636; their content-classification rules differ. It keeps exposure-only tokens out of scheduled-review denominators. Raw `introduced_at` counts are not substituted for true-new intake: reintroductions and historical episode metadata make that unsafe.

### The feeling of rare words everywhere has a measurable basis

After overlaying the already-merged missing-rank repair **in memory**, there are **2,794 active canonical content lemmas**. Of these, **239 (8.6%)** have an effective rank worse than 5,000 and **48 (1.7%)** remain unranked. Effective rank is the better of the stored core rank and the CAMeL proxy; it is not a validated fiction-frequency rank.

| Maintenance-window burden | Rank >5,000 | Unranked | Total |
|---|---:|---:|---:|
| Scheduled word judgments | 234 | 74 | 1,634 |
| Primary word judgments | 51 | 17 | 516 |
| Sentence reviews containing such vocabulary | 282 | 29 additional, with no >5,000 word | 517 |

Thus **9.9% of primary targets** are ranked worse than 5,000, but those words appear somewhere in **54.5% of the reviewed sentences**. Including sentences with only unranked outliers brings the share to **60.2%**. This is a sentence-exposure measure, **not** a claim that 60% of the learner's time is wasted or that all those words should be dropped.

The same retrospective mapping/rank method finds >5,000 words in **13.1% of June**, **44.8% of July**, and **48.8% of August** sentence-review rows. Earlier months include passage child rows, so the monthly card counts and response times are not directly comparable workload measures. The large change is nevertheless consistent with the independently measured generation-sampler shift in the [September 18 analysis](analysis-2026-09-18-sentence-rarity.md).

That analysis already found the inverse-sentence-count sampler offering roughly **22% low-ranked vocabulary**, from a pool containing roughly 10%, with an even rarer first 100 items. Words that are hard to fit into sentences have few sentences, so the sampler repeatedly gives them preferential access to new prompts. This combines a good objective—cover neglected words—with no adequate check that they deserve so much future attention.

## Why previous rounds have not resolved this

### 1. Acquisition still gives old textbook provenance a permanent override

A read-only execution of `select_next_words(count=40)` against the new snapshot returned **40 textbook_scan-priority candidates out of 40**. This is candidate ordering, not a prediction that all 40 will pass recovery, supply and daily-intake gates.

The code gives textbook provenance **220 points**, core top-500 **240**, top-1,000 **210**, top-2,000 **170**, and top-5,000 **120**. Within-tier frequency/root bonuses are tiny by comparison. Once the earliest core is mostly introduced, old textbook backlog dominates. The first 500 core rows are already in introduced states; the first 1,000 contain only two encountered-state rows.

Some textbook candidates are useful, including تَعَوَّد “get used to” and جَاع “be hungry.” Others expose lexical QA problems: `#3988 عاوَد` is stored as a noun meaning “compresses, poultices”; `#4006 مُنْتَصِب` as a verb meaning “tear, rip”; and `#4484 سَلِيم` is explicitly glossed as a proper name but has no inert category. All carry a completed quality-gate stamp. These need an identity/context audit, not automatic teaching because of old provenance.

The curriculum documentation also overstates frequency priority: its statement that top-1,000 always outranks active book words omits the stronger textbook tier, and its source-weight table still lists the Quran weight as 150 while code uses 700. This contributes to repeatedly assuming the intended policy is the actual policy.

### 2. Frequency evidence is being attached to the wrong identities

The rank-path fix is necessary: it supplies missing ranks for **1,476 lemmas**, **800 active canonical content lemmas** in this snapshot. It does not repair wrong core links, inflection conflation, gloss/POS errors, or corpus mismatch.

The strongest reproducible example is `#751 دَنّ “wine jug”`, core rank **816**. Its stored Quran contribution is 18 occurrences. Re-running the shared QAC mapper produces:

> QAC `l~adun`, لَّدُن, 18 occurrences → Alif `#751`, دَنّ, “wine jug”.

The mapper has treated part of the dictionary lemma as a detachable prefix. POS compatibility did not save it: both entries are represented as nouns. The genuinely lemmatized source was damaged at the join into Alif.

Other diagnostic mappings include سُندُس → دَسَّ “hide/insert” and نِحْلَة / نَحْل → نَحُلَ “grow thin.” These illustrate why a dictionary-source mapper must preserve lexical identity and reject an unsupported match. The full probe contains **1,229 coarse POS mismatches**; that is an audit queue, **not 1,229 proven wrong lemmas**. Some nominal/participial rollups are intentional and tag sets differ. The exact wine-jug case is sufficient to establish the failure mechanism.

The old high-priority outliers are still present: خَمَّ “rot/stink” at core **1,914**, and ذَكَرِيّ glossed “memory” at **1,938**. They are concrete QA candidates. They are not most of the recent workload: wine jug received two collateral scheduled judgments and zero primary judgments during this window; rot/stink received two judgments, one primary. Fixing a few memorable examples alone will not transform the experience.

### 3. The “general reading” core is a mixed, incomplete curriculum

The 5,000-row core currently has CAMeL evidence on **4,802** rows, news/SAMER on **4,912**, Hindawi on **1,690**, and Quran on **1,292**. **KELLY, Buckwalter and arTenTen remain empty.** There are **1,867 unmapped rows** and **3,112 distinct mapped canonical lemmas**. Twenty-one linked rows duplicate another canonical identity. Honest unmapped denominators should remain; rank rows are not a count of distinct learned words.

Quran contributions occur on **494 of the first 500** and **817 of the first 1,000** rows. This does not make those words inappropriate—much vocabulary is shared—but it confirms that this is not a purpose-built contemporary-fiction ordering. The builder gives Quran a 700 weight and exempts strong Quran evidence from the agreement penalty. Classical ambitions should not silently determine every near-term choice.

The Hindawi signal is also selected data: `build_frequency_core` counts already-imported `source='corpus'` sentence mappings. The import pipeline originally accepted only sentences whose content words could be mapped into the existing inventory, and selected children's categories. That cannot independently measure the missing vocabulary of modern novels. A future rebuild would also mix later Momo corpus rows into the same Hindawi source unless its provenance is separated. Generated practice sentences must never become independent evidence of real-world usefulness.

### 4. The slow lane is based on source labels and can miss entire import routes

`ARTIFACT_SOURCES` contains textbook_scan, book, story_import, scaffold and book_ocr. It omits **bookifier** and **dragoman**. `is_main_lane_word` admits any non-artifact source, regardless of rarity. A change in import label therefore changes the learner's maintenance commitment.

There are **104 active Bookifier** and **14 active Dragoman** lemmas outside the top-5,000 proxy or unranked. Together they account for **158 scheduled judgments and 44 primary judgments** in the maintenance window. Eighteen are due at this snapshot, two of them acquiring; acquisition already has its own unconditional main-lane exemption. Fixing the omission would affect the remaining due FSRS subset, not erase hundreds of obligations immediately.

Nor is the current 10% slow-lane sample a 10% cap on rare vocabulary appearing in sentences. It limits sampled due lemma IDs. Rare collateral words can still occur throughout the selected material and still receive ordinary scheduling after failures. This explains why changing a target quota alone is insufficient.

The maintenance selector also intentionally prioritizes observed lapse risk and overdue age before frequency in its opening ordering. That is sensible **after** choosing a valuable maintenance set. Applied to all admitted words, it can reward repeatedly failing a low-value word with more attention indefinitely.

### 5. Whole-book imports created obligations earlier than reading created value

The July Momo work explicitly introduced a large tranche at once; its contemporary report recorded **Box 1 = 303** and an accepted delay to leech recovery. Today's Bookifier cohort contains **236 canonical content lemmas: 165 active and 71 suspended**. Most active ones are labelled known; this is not a failed-learning cohort. The question is whether retaining all of it now has the best return.

Meanwhile all **243 Momo corpus sentences remain inactive**; seven have passed linguistic QA. They have not supplied review practice in this window. Of the 517 recent sentence reviews, **516 are generated and one is book-sourced**. Supported chapter/reader events are separate: there are 43 chapter events, including one completion event for `drawing`. That does not establish independent comprehension or sustained reading, and app-testing events cannot be assumed to be learner outcomes.

The system has therefore absorbed substantial book vocabulary while providing almost entirely generated review practice. The supported reader already offers a better place to encounter book-specific words without requiring global SRS enrollment. This investigation does not recommend enabling all corpus activation or relaxing mapping gates.

## What to retain, what to defer

**Do not suspend everything beyond rank 5,000.** After rank repair, “father” is rank 7,480, “boy” 27,302 and “sofa” 54,138 in the fallback proxy. These are plausible novel words. CAMeL's public dataset explicitly contains **surface-word counts**, not a sense-disambiguated lemma list. Definite forms, plurals, derived forms and homographs complicate the comparison. [CAMeL dataset documentation](https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists).

I ran a fresh automatic surface-preserving map over the available full Momo OCR. It found 38,443 tokens, 4,060 unresolved tokens and 4,666 tokens with competing identities. Those flags do not capture every error: it also assigns many إذن occurrences to “ear.” The older July map uses 37,803 tokens and loses enough surface/context information that it cannot simply be refreshed by assigning its bare OOV forms to new lemmas. **Neither supports a defensible claim that the learner is now at 95% or 98% comprehension.** The existing readiness script additionally treats some unanalyzable OOV forms as readable function words; that optimistic fallback must not be carried into an acceptance metric.

The fresh map is still useful for generating a small contextual inspection queue. Examples: فَعَلَ “do” has 52 proposed occurrences, سَارَ “walk/go” 40, and suspended اِسْتَطْرَدَ “continue speaking” 22. These are candidates to verify in real passages, not an approved bulk import. Conversely “boy” has 18 mapped occurrences and is already known: a low global rank is no reason to deprioritize it when reading this book.

The appropriate unit of decision is **a verified lemma/sense, its useful forms and constructions, and the next texts in which it will matter**. “Know the root” and “recognize this word effortlessly in a sentence” are different achievements. The September reading assessment already demonstrated accurate comprehension accompanied by effortful processing; more unrelated headwords would not by itself solve that.

## Proposed operating process

### Make attention eligibility independent of memory state and provenance

Maintain three dispositions, with transparent reasons:

| Disposition | What earns it | Consequence |
|---|---|---|
| Maintain now | Verified broad fiction utility, recurrent near-term reading need, or explicit learner importance | Eligible for bounded deliberate review and priority repair after genuine lapses |
| Reading support | Useful within the current book/scene but insufficient general or repeated need | Available in glosses and context; no automatic permanent maintenance obligation |
| Later / parked | Remote classical specialization, abandoned material, or low expected benefit for its cost | Preserve history and lookup access; exclude from current debt, automatic reintroduction and ordinary scaffold demand |

Identity uncertainty is a separate QA status; it is not evidence that a word is rare or unimportant. Retain an explicit “unknown usefulness” category in audits.

Parking must be separate from `knowledge_state='suspended'`: today's suspension is a leech-treatment state with automatic reintroduction. A study pause must not auto-return as a supposedly rehabilitated leech. Preserve all FSRS history and evidence; do not label parked words forgotten or reset their memory. The prospective behavior of parked-word encounters needs a versioned change: preserve token outcomes, but prevent incidental appearances from silently re-enrolling them. This changes a foundational scheduling rule and needs explicit implementation tests and a logged policy phase, not a direct database flag hack.

### Spend a fixed session budget on current reading benefit

Within the maintained set, use a transparent estimate of:

**expected useful encounters × chance this review improves recognition × importance in the passage, divided by expected practice cost.**

Initially use broad bins, not spurious numeric precision: next-passage recurrence, spread across books/chapters, recent unaided recognition, and observed repeated failure. Frequency is a prior; actual reading supplies the personal evidence. Lapse risk should decide how to help an important word, not establish its importance.

High-cost repeated failures trigger a choice: verify the identity, provide a better cue/form explanation, practise in the actual scene, or park it. A persistent failure should not automatically win forever. Keep a small sample of valuable old words for delayed recognition so quieter review does not simply conceal forgetting.

### Admit a few words after useful encounters, not an entire book's tail

Prepare one short next passage with immediate translation, optional tashkeel and explanations. Gloss freely. Enroll **at most the existing 0–2 true-new words/day**, selected for recurrent near-term value; zero is fine. Repeated need in different passages is stronger evidence than four occurrences somewhere in a 300-page book. Explicit user imports should stage a reading vocabulary list by default, with a visible distinction between “available for this book” and “maintain every week.” Explicitly requested bulk enrollment can remain possible, but should show its projected obligation first.

Remove permanent source-based privileges from old textbook material. Keep provenance for audit; replace its priority bonus with an explicit current curriculum selection that expires or is renewed when reading evidence warrants it. Existing book opt-ins already point in this direction; extend that separation consistently to textbook and external-import routes.

### Control the whole sentence, including its background words

Reuse the September 18 sampler investigation rather than start another generation redesign. Background vocabulary should be overwhelmingly fluent and useful, with an unfamiliar or lower-priority word included because the sentence needs it or because it is the deliberate target. Penalize avoidable total difficulty, not only the target's frequency. Keep naturally occurring literary words when context supports them.

The proposed numerical rare-scaffold cap remains a **shadow test** until rank uncertainty and demonstrably wrong links are handled. A raw >5,000 gate would suppress useful fiction words. Before retiring problematic inventory, regenerate enough verified alternatives for every affected important due word. Corpus generation stays bounded and source-preserving.

### Give modern fiction and classical literature separate priorities

Use a small, varied contemporary-fiction reference set—native Arabic fiction as well as translated fiction—and the next selected book. Count spread across titles/chapters as well as tokens so one story's recurring prop cannot dominate the universal curriculum. Pin the text edition, genre, mapping version and identity confidence; retain unresolved counts.

Keep Quran/classical evidence as its own domain, useful when that domain is active. Its overlap with modern fiction naturally remains valuable. Later, activate a small prose/classical reading selection; do not pre-maintain the full medieval tail while it has no near-term encounter opportunity. Quran frequency alone is not a general measure of medieval prose or poetry usefulness.

## Frequency-source work: a bounded prerequisite, not another open-ended hunt

The June research correctly distinguished raw classical text from a ready, trustworthy classical lemma list. There is no reason to restart that entire search for this immediate goal.

One June availability inference needs correction. SUBTLEX-AR's paper describes lemma/POS information and a large modern subtitle/news resource, but today's inspection of its public OSF file listing and README found **validation trials and analysis code**, not a verified complete lexical-table export. The publisher also links a **November 2025 correction acknowledging a processing error**. The full correction was not retrieved, so its field-level implications remain unverified. The query website timed out during this session. This does not prove the full list is unavailable; it means “six downloadable files” never established that the desired lemma table was downloadable. [SUBTLEX-AR paper](https://doi.org/10.3758/s13428-024-02560-8), [correction](https://doi.org/10.3758/s13428-025-02899-6), [OSF project](https://osf.io/spb8c/).

Time-box a source pilot: obtain a real corrected export with schema/version, audit roughly 100 high-impact identities including known traps, and compare its choices on held-out fiction against a simple verified book-frequency baseline. Stop if it does not improve decisions. Do not rebuild the curriculum simply because a new source has a large token count.

Coverage remains a useful descriptive measure once mapping is trustworthy, but an English 98% rule is not an Arabic readiness certificate. A 2023 replication did not fully reproduce the original threshold findings. Begin supported reading now and assess comprehension/effort directly; do not make reading wait for an arbitrary vocabulary total. [Kremmel et al., 2023](https://onlinelibrary.wiley.com/doi/full/10.1111/lang.12622).

## Concrete implementation sequence

1. **Close the operational loop.** Verify and release the already-merged maintenance changes and rank-loader repair in a separately recorded phase; run the existing NULL-only rank backfill with backup and effect verification. This investigation did not deploy them. A release report should name code present, data migration/backfill applied, and observed policy version separately.
2. **Run a bounded curriculum audit.** Review the next 40 intake candidates and the small high-burden identity queue with their actual source sentences. Quarantine wrong identities from new teaching and frequency contributions. Repair QAC joining so citation lemmas cannot lose lexical letters through running-text prefix stripping; accept unresolved rows rather than forced matches. Shadow the source-based main-lane omission before choosing which book-specific words deserve reduced maintenance.
3. **Build the attention disposition and all-path contract.** One shared eligibility rule must serve intake, due counts, leech reintroduction, sentence targets, scaffold sampling, collateral scheduling, stats, explicit imports and guided reading. Test that each source behaves identically for the same disposition; prefetched sessions remain inert; parked history is preserved; lookup cannot resurrect an obligation.
4. **Substitute supported reading for part of review.** Start with one next passage. For example, within a ten-minute visit, spend about five minutes on chosen maintenance and five on reading; this is a trial allocation, not an extra daily requirement or a demand to complete 30 cards first. Keep intake at 0–2 and prioritize only verified passage needs. The supported-reader machinery already exists.
5. **Evaluate after two weeks, with delayed checks later.** At equal or lower total effort, seek more voluntarily completed passages, fewer help requests per 100 running words on comparable fresh passages, lower reported effort and sustained recognition of the protected high-value set. Use sparse 7/14/30-day checks and distinguish first reading from rereading. Count minutes, real passages and word-level help separately from scheduled judgments. Due count and “known” stock are secondary diagnostics. Record any new loss of important vocabulary before expanding the parked set.

The tradeoff is deliberate: some deferred vocabulary will be less accessible later and may need relearning. The benefit we are trying to buy is the ability and desire to read now. It would be misleading to promise both substantially less practice and unchanged retention of every word ever imported.

## Evidence and reproduction

- [Analysis script](vocabulary-investigation-2026-09-19/analyze.py), [aggregate results](vocabulary-investigation-2026-09-19/results.json), [per-word audit](vocabulary-investigation-2026-09-19/word-audit.csv).
- [Selector/QAC/Momo probe](vocabulary-investigation-2026-09-19/probe_mapping.py), [probe results](vocabulary-investigation-2026-09-19/mapping-probe.json.gz), [maintenance checkpoint](vocabulary-investigation-2026-09-19/maintenance.json), [source availability audit](vocabulary-investigation-2026-09-19/source-audit.json).
- [Reproduction and limitations](vocabulary-investigation-2026-09-19/README.md).
- Prior work incorporated: [June source review](analysis-2026-06-03-arabic-frequency-lists.md), [classical feasibility](analysis-2026-06-03-classical-literary-frequency-track.md), [July Momo intake](momo-vocab-queue-2026-07-15.md), [September reading reassessment](analysis-2026-09-05-reading-fluency-reassessment.md), [September 15 checkpoint](analysis-2026-09-15-reading-refresh.md), [September 18 rarity investigation](analysis-2026-09-18-sentence-rarity.md).

No causal minutes-saved estimate, novel-readiness percentage, automatic parking list or production change is claimed. The report delivers the diagnosis and a bounded process change proposal.
