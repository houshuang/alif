# Learning-policy deployment candidates — 2026-07-31

## Decision

One change is ready for a bounded production experiment, behind a default-off
feature flag:

> Expand the existing randomized exact-surface-form pilot from rare yellow
> events to successful first encounters with meaningful Arabic forms.

Enable with `ALIF_PROACTIVE_FORM_EXPERIMENT=1`. This does **not** create cards,
change due dates, change review credit, or increase the requested session size.
The treatment arm may use at most one already-due reading sentence per session.
The canonical lemma remains the scheduling unit; the intervention changes only
which sentence represents an already-due lemma.

Two tempting alternatives were rejected rather than shipped:

1. Ignoring early collateral successes in FSRS produced only tiny predictive
   changes and essentially no workload change in full-history replay.
2. Globally preferring underexposed forms did expose more weak forms, but it
   displaced seven other reviewable words in small-session replay. A strict
   dominance-only version preserved every word but made zero swaps in 12
   depletion-stressed requests. It was safe but inert.

## Why this is the best candidate

The longitudinal model estimated a **4.8 percentage-point penalty for a novel
surface form** and a **7.7-point penalty for verbs**, conditional on the
available covariates. These are not tiny calibration discrepancies: they are
large enough to affect comprehension and they identify a representational
blind spot in lemma-only scheduling. The intervention is also unusually clean:
it can be randomized without changing when a lemma is due.

The causal question is:

> After the learner successfully encounters a meaningful form for the first
> time, does ensuring one later exact-form retrieval in a different sentence
> improve exact-form retrieval and subsequent word-level performance relative
> to ordinary lemma scheduling?

Assignment occurs only when:

- the form is a meaningful inflection, derivation, enclitic, or non-citation
  verb form;
- this is its first recorded successful exposure;
- the word is no longer in acquisition;
- a different reviewable sentence containing exactly that form exists;
- the lemma has no unresolved exact-form episode; and
- the form has never had an episode before.

Assignment is deterministic 50/50 randomization from the review identity,
lemma, and surface form. Control leaves selection unchanged. Treatment reserves
at most one normal due slot and makes the form the retrieval target.

## The primary-target problem was real

The old pilot waited for a later review where the word happened to be tagged as
the scheduler's *primary* target. Reconstructing the proposed episodes over the
frozen trajectory showed:

| Endpoint within 14 days | Episodes | Yield |
|---|---:|---:|
| First later all-word review | 440 / 504 | **87.3%** |
| First later primary-tagged review | 43 / 504 | **8.5%** |
| Exact-form all-word review, different sentence | 203 / 504 | **40.3%** |
| Exact-form primary-tagged review | 3 / 504 | **0.6%** |

Of the 440 usable all-word outcomes, **95.0% carried “collateral” metadata**.
Restricting the endpoint to primary targets would therefore discard roughly
nine tenths of the usable longitudinal evidence and nineteen twentieths of the
actual outcomes. The implementation now records:

- the first later review of the word, regardless of scheduling label;
- whether its form was exact;
- whether the context repeated the trigger sentence;
- the word-level rating, confusion flag, and credit type; and
- the first later exact-form review in a different sentence, regardless of
  scheduling label.

The old primary-only fields remain intact as secondary, backward-compatible
endpoints.

## Frozen-data simulation

Input database:
`artifacts/longitudinal-2026-07-30/alif.db`

SHA-256:
`f98ad15b279cdb9a4ec0fcfbbe9ddf76982aa1c6c51bf15c84ebc8d96232784a`

Cutoff: `2026-07-30T13:58:00Z`

The reconstruction used real chronological reading reviews and the sentence
pool available at the cutoff. It found:

- **504** serviceable first-form assignments;
- **3.04 assignments per active learning day**;
- 248 control and 256 treatment assignments under deterministic hashing;
- median one alternate sentence (mean 1.73);
- 308 derived forms, 109 inflections, 51 enclitics, 27 present-tense verb
  forms, and 9 other verb forms;
- 87.3% later all-word endpoint availability under ordinary historical
  scheduling; and
- only 4.1% of those first later outcomes repeated the trigger sentence.

This is a throughput and endpoint-yield simulation, not an estimate of the
treatment effect. Historical review order is real, but serviceability uses the
sentence pool at the cutoff; some alternate sentences did not exist at the
historical trigger time.

### Power

The next all-word success rate was already 95.5%, creating a ceiling. A
two-sided, equal-arm test would need approximately:

| Assumed absolute gain | Evaluable episodes for 80% power | Active days at reconstructed rate |
|---|---:|---:|
| +1 point | 12,186 | 4,598 |
| +2 points | 2,682 | 1,013 |
| +4 points | 486 | 184 |

Therefore “next review was successful” is a valuable safety/learning endpoint
but a poor short-pilot primary endpoint.

The operational intention-to-treat endpoint is:

> a successful exact-form review in a different sentence within 14 days,
> counting non-delivery as failure.

Ordinary historical scheduling achieved this for 38.3% of reconstructed
episodes. In 20,000 Monte Carlo trials per cell:

| Assigned episodes | +10-point effect | +20-point effect | +30-point effect |
|---|---:|---:|---:|
| 40 | 8.9% power | 23.2% | 45.6% |
| 80 | 15.5% | 45.9% | 79.7% |
| 120 | 20.6% | 61.2% | 92.3% |
| 200 | 30.5% | **82.5%** | 99.2% |

A useful first read is therefore 80 episodes for gross failures and balance;
200 episodes is the planned efficacy milestone if an effect around 20 points
is plausible. At 3.04 assignments per active day, 200 assignments is about 66
active learning days.

## Analysis plan for the pilot

Use episode-level intention-to-treat analysis. Do not compare only delivered
exact-form reviews: delivery is caused by treatment, so conditioning on it
would bias the estimate.

Primary:

- Binary successful exact-form review in a different sentence within 14 days;
  no review counts as failure.
- Report risk difference, risk ratio, Fisher exact interval, and a
  lemma-clustered bootstrap interval.

Secondary:

- exact-form delivery regardless of success;
- first later all-word rating and success;
- first later all-word confusion;
- time to exact-form outcome, with expiry as right censoring;
- same-context versus different-context outcome;
- effects by morphology category, explicitly exploratory; and
- session opportunity cost: due-word coverage, all-word breadth, card count,
  and sentence quality when an exact-form treatment card is selected.

Pre-specified safety checks:

- no more than one exact-form treatment card per reading session;
- no acquisition episode;
- no new card or due-date mutation;
- no concurrent episode on one lemma;
- no repeat episode for the same lemma/form pair;
- deterministic assignment balance between 40% and 60% after 40 episodes;
- endpoint completeness at least 75% by day 14; and
- stop and inspect if treatment lowers next all-word success by more than five
  points at any milestone.

Because this is one learner, uncertainty describes repeated items and episodes,
not population generalizability. Cluster by lemma and present every result as a
within-learner policy comparison.

## Literature comparison

The direction of the distributed-days result agrees with experimental
vocabulary research. A web-based factorial experiment found better vocabulary
learning when practice was spread across more sessions, with retrieval and
feedback also manipulated
([Nakata et al., 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8638698/)).
Alif's adjusted distributed-days estimate was +4.6 points (AIPW, 95% interval
+2.0 to +7.4), but the designs and outcomes differ too much for a defensible
numeric “faster than literature” claim.

Within-session repetition has a nuanced literature. Five or seven retrievals
produced higher delayed scores than one or three in one L2 experiment, but one
retrieval was most efficient after controlling time on task
([Nakata, 2017](https://doi.org/10.1017/S0272263116000280)). That is consistent
with Alif's result that distributed days matter more than simply accumulating
same-day repetitions, and with rejecting a policy that merely adds rapid
reviews.

Controlled contextual-diversity work found better recall, recognition, and
meaning matching when the same number of encounters was distributed across
more texts
([Frances et al., 2020](https://pmc.ncbi.nlm.nih.gov/articles/PMC7435265/)).
Alif's adjusted context-variety estimate was only +0.6 points and its interval
included zero. That is weaker than the controlled literature, possibly because
Alif sentences share vocabulary and constructions, because the observational
diversity measure is crude, or because spacing already captures much of the
same variation. It is not evidence that context diversity is harmful.

Morphological studies make lemma-only transfer an unsafe assumption.
Surface-form frequency predicts recognition latency for inflected words
([Alegre & Gordon, 1999](https://pubmed.ncbi.nlm.nih.gov/9259621/)), and adult
learners can acquire inflectional regularities incidentally without verbalizing
the rules
([Rogers, Révész & Rebuschat, 2016](https://doi.org/10.1017/S0142716415000247)).
The literature therefore supports both halves of Alif's design: contextual
sentence exposure can teach morphology, but particular forms still need enough
evidence to become retrievable.

The Alif result that novel forms cost 4.8 points and verbs cost 7.7 points is
not a clean published-effect-size replication; it is a within-learner,
observational estimate over real Arabic sentences. Its contribution is
diagnostic: the magnitude is large enough to justify the randomized test, but
not to justify deploying targeted form scheduling without that test.

## Does FSRS optimism matter if ordering is right?

Only partly. If every session has a fixed number of cards and the system always
takes the same top-ranked obligations, a monotone calibration error leaves the
ordering unchanged and may have little practical effect. That is why the
early-collateral FSRS guard was rejected: despite large per-card due-date
counterfactuals, the full replay barely changed prediction and did not change
workload.

Magnitude still matters wherever Alif uses an absolute threshold rather than a
pure ranking: deciding what is due, deciding whether to preview almost-due
words, balancing maintenance against introduction, and forecasting backlog.
Calibration should be monitored, but it is not automatically a learning
priority. A ranking-preserving correction with no change in selected material
does not merit deployment.

## Deployment and rollback

The code is inert until the backend service receives:

```text
ALIF_PROACTIVE_FORM_EXPERIMENT=1
```

After restart, verify the append-only interaction log contains
`exact_surface_experiment_assigned` events with
`trigger_kind=successful_first_form_exposure`, followed by
`exact_surface_experiment_all_word_outcome` and
`exact_surface_experiment_exact_all_word_outcome`.

Rollback is immediate: remove or set the environment variable to `0` and
restart. Existing episodes remain valid data; without the flag, no new
successful-first-exposure episodes are assigned. The original yellow-confusion
pilot continues unchanged.

## Reproducibility

- Simulation:
  `backend/scripts/simulate_proactive_form_experiment.py`
- Milestone analysis:
  `backend/scripts/analyze_exact_surface_experiment.py`
- Machine-readable results:
  `artifacts/longitudinal-2026-07-30/proactive-form-experiment-simulation.json`
- Feature implementation:
  `backend/app/services/surface_form_experiment.py`
- Tests:
  `backend/tests/test_surface_form_experiment.py`

The simulator checks the frozen database SHA before and after execution, opens
SQLite as immutable/query-only, uses 20,000 Monte Carlo trials per cell, and
records its seed.
