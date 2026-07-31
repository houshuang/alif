# Context diversity in Alif — decision analysis

**Date:** 2026-07-31

**Data cutoff:** 2026-07-30 13:58 UTC
**Snapshot SHA-256:**
`f98ad15b279cdb9a4ec0fcfbbe9ddf76982aa1c6c51bf15c84ebc8d96232784a`

## Decision

Do **not** add a global context-diversity multiplier or activate a
constant-versus-variable retention experiment now.

Alif already implements variable contextual retrieval much more strongly than
the earlier analysis made explicit. The selector's sentence-level diversity
factor is learner exposure, not merely corpus variety: because every mapped
word in a reviewed sentence counts as seen, `Sentence.times_shown` is also a
valid context-use counter for every word in that sentence.

The remaining uncertainty is not whether Alif supplies different sentences.
It is whether Arabic learners should receive a new sentence **and** a new
surface form at the same time. That is a morphology/context interaction and is
better answered by the exact-form pilot plus protocol-v2 presentation evidence
than by forcing half of reviews back into familiar sentences.

## Finding 1 — acquisition is already variable practice

Across 2,159 completed acquisition episodes:

- mean reviews per episode: **5.04**;
- mean distinct sentences: **4.37**;
- among the 1,570 episodes with at least two reviews, distinct sentences per
  review averaged **87.65%**;
- only **0.32%** of repeated-review episodes stayed in a single sentence.

The apparently larger 27.5% "single sentence" figure across all episodes is
not repeated constant practice: it is dominated by historical one-review
graduations, which cannot exhibit context variation by definition.

The newly enabled distributed-day policy is already aligned with variable
retrieval. In 1,424 historical pairs where a successful acquisition review was
followed by success on another UTC day, **91.01%** used a different sentence.

## Finding 2 — the selector is already doing the proposed intervention

Across 49,123 non-acquisition reviews that followed an earlier reviewed
context for the same lemma:

- **85.11%** used a sentence never previously seen for that lemma, including
  its acquisition history;
- only **5.69%** immediately repeated the previous sentence;
- the median reviewed lemma appeared in **12** distinct sentences; the 90th
  percentile was **32**.

In the final 30 days, **78.87%** still used a new sentence. Immediate repeats
rose to 10.56%, consistent with the smaller active pool and the selector's
explicit rescue path when every sentence for a due word is inside its recency
window.

This behavior follows directly from the multiplicative score
`1 / (1 + sentence.times_shown)`: an unseen sentence receives 1.0, one prior
display 0.5, and two displays 0.33 before the other safety and coverage terms.
The proposed generic "prefer diverse contexts" rule therefore already exists.

## Finding 3 — familiar sentences are easier now

In the last 30 days:

| Context at review | Successful word reviews | N |
|---|---:|---:|
| New sentence for that lemma | 86.53% | 4,684 |
| Familiar sentence | 93.78% | 1,255 |
| Raw difference, new − familiar | **−7.25 points** | — |

This is not a causal estimate: familiar sentences are selected in different
states, especially recency rescue, failures, scarce material, passages, and
high-coverage cards. It does show that a familiar sentence supplies a strong
retrieval cue. Consequently, increasing diversity can make practice feel—and
look—worse immediately even if it improves decontextualized retention. That is
the exact metacognitive/desirable-difficulty pattern reported in variable
retrieval research.

The previously estimated adjusted effect of accumulated context variety on
later Alif accuracy was only **+0.6 points with an interval including zero**.
The new audit explains why that observational contrast is weak: almost all
multi-review acquisition episodes already receive varied contexts, leaving a
small and selected low-diversity comparison group.

## Finding 4 — Alif varies more than context

For the 1,424 historical cross-day acquisition confirmations:

- 91.01% changed sentence;
- only **0.28%** both changed sentence and retained the same normalized Arabic
  surface form.

The strongest recent variable-retrieval experiment held the foreign word form
constant while varying its cue sentence. Alif usually varies both the sentence
and the Arabic surface form—person, tense, clitic, inflection, or derivational
realization. This makes practice more authentic but also combines two desirable
difficulties. A failure cannot be attributed to context variation alone.

This is why the exact-form pilot and complete token evidence are complements to
the context analysis. They can determine whether the joint difficulty is
productive or whether early cross-day confirmations should temporarily hold
surface form constant while varying the surrounding sentence.

## Candidate experiments and why they were rejected

### Clean acquisition trial

Proposed arms:

- constant: reuse one anchor sentence during acquisition;
- variable: rotate semantically distinct sentences;
- common outcome: successful retrieval in a third, previously unseen sentence;
- same normalized surface form in every arm and assessment;
- identical review count and timing.

Eligibility required two distinct alternatives, content-lemma Jaccard at most
0.50, word-count difference at most three, and normal reviewability. Only
**10** completed historical episodes qualified, or **0.152 per active day**.
The experiment is scientifically clean but operationally impossible without
first generating form-controlled sentence sets.

### Micro-randomized mature-word trial

At each eligible review, choose the most recent successful familiar sentence
or an unseen same-form sentence; use the next 1–14-day review as retention
outcome. The reconstruction found 747 non-overlapping opportunities across 78
active days (**9.58/day**), but only 67.1% had a historical outcome in the
window.

Approximate 80%-power timelines:

| True absolute benefit | Episodes | Active days | With outcome window |
|---:|---:|---:|---:|
| +2 points | 17,424 | 1,820 | 1,834 days |
| +3 points | 7,684 | 803 | 817 days |
| +5 points | 2,720 | 285 | 299 days |
| +10 points | 648 | 68 | 82 days |

Only an implausibly large effect could be decided quickly. Worse, the control
arm would deliberately replace Alif's existing variable practice with familiar
cues for half the episodes. That is not justified merely to measure a small
increment over a behavior already deployed at ~80–88% prevalence.

## Literature comparison

Butowska-Buczyńska et al. ran six foreign-vocabulary experiments in which the
foreign form was held constant and cue sentences were constant or varied.
Variable cues improved learning when practice required retrieval, benefits
appeared immediately and after 24 hours, and spacing magnified them. Learners
nevertheless believed constant cues were better. This closely supports Alif's
retrieval-before-reveal flow and explains why new contexts can lower practice
accuracy while supporting transfer:
[PNAS 2024](https://doi.org/10.1073/pnas.2413511121).

Frances, Martin, and Duñabeitia held encounter count constant while spreading
pseudowords across 1, 2, 4, or 8 texts; diversity improved recall, recognition,
and meaning matching in native- and foreign-language reading:
[Scientific Reports 2020](https://doi.org/10.1038/s41598-020-70922-1).

The literature is not uniformly "more diversity is always better." Norman et
al. found that diverse learning improved generalization to unseen contexts,
whereas non-diverse learning improved performance in familiar contexts, with no
word-form recognition benefit:
[Quarterly Journal of Experimental Psychology 2023](https://pmc.ncbi.nlm.nih.gov/articles/PMC10280660/).
An anchoring replication again found null word-form effects and a familiar-
context advantage for low-diversity learning:
[Memory 2024](https://pubmed.ncbi.nlm.nih.gov/38012815/).

Van den Broek et al. further show that contextualized retrieval is not
automatically superior when retrieval success is low; informative contexts can
outperform failed retrieval, while retrieval plus feedback becomes competitive
after stronger encoding:
[Cognitive Science 2022](https://doi.org/10.1111/cogs.13135).

Alif is therefore directionally consistent with the literature, but no numeric
"better than literature" comparison is defensible: published studies use
pseudowords, controlled repetitions, intentional translation recall, and short
post-tests, whereas Alif uses naturally varying Arabic forms, whole-sentence
self-assessment, collateral exposure, and FSRS timing.

## Next evidence gate

Do not change scheduling for context diversity now. Re-evaluate after the
7-day exact-form pilot and protocol-v2 evidence provide enough observations to
estimate:

1. new-context cost when surface form is held constant;
2. new-surface cost when context novelty is controlled;
3. whether a new context plus new form predicts better subsequent novel-
   context retrieval than either difficulty alone; and
4. whether failures cluster in the second-day acquisition confirmation.

If the joint condition is harmful, the actionable policy is narrow: for the
new distributed-day confirmation, prefer a different sentence with the same
surface form when one exists. It should remain a preference, not a blocker, so
scarce material never delays a due review.

## Reproduction

```bash
cd backend
PYTHONPATH=. .venv/bin/python \
  scripts/simulate_context_diversity_experiment.py \
  --db /path/to/pinned/alif.db \
  --cutoff 2026-07-30T13:58:00Z \
  --expected-db-sha256 \
    f98ad15b279cdb9a4ec0fcfbbe9ddf76982aa1c6c51bf15c84ebc8d96232784a \
  --output /tmp/context-diversity.json
```

The script opens SQLite with `mode=ro&immutable=1`, enables `query_only`, checks
the database hash before and after, and refuses to overwrite an output file.
