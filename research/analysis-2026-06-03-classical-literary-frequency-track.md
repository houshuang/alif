# Classical/medieval Arabic literary frequency track — feasibility sweep (2026-06-03)

Deep-research sweep (`deep-research` harness): 5 angles · 18 sources fetched · 78 claims
extracted · 25 verified (17 confirmed, 8 killed) · 6 after synthesis · 100 agents.

Companion to the Quran-track build (`analysis-2026-06-03-arabic-frequency-lists.md` +
the Task A work). Scope: **can a high-quality classical/medieval Arabic LITERARY
frequency track — beyond the Quran, covering commentaries, medieval prose & poetry — be
built from an OPEN premodern corpus, with extractable, genuinely LEMMATIZED counts?**

## Verdict — GO on raw text, NO-GO on off-the-shelf lemmatized frequency (build-it-yourself)

**No genuinely lemmatized classical/medieval Arabic frequency list can be obtained
off-the-shelf from any vetted source.** The raw *text base* for building one is
obtainable (OpenITI), but every existing frequency artifact is surface-token counts —
exactly the conflation problem the frequency-core rebuild is trying to escape. The
decisive bottleneck is **classical-specific lemmatization: no validated premodern Arabic
lemmatizer exists.** CAMeL Tools ships only MSA + dialect morphology DBs; published work
processes classical text by *reusing the MSA database*, with no measured accuracy on
premodern Arabic.

Recommendation:
- **Conditional GO — OpenITI as a TEXT source only** (build lemmatization + counts
  ourselves; verify CC-BY-NC-SA non-commercial fit).
- **NO-GO — arabiCorpus** (untagged, surface-KWIC only, no export, retiring 2027-07-01).
- **NO-GO — any expectation of pre-lemmatized classical frequency data** (Shamela /
  OpenArabic / RAWrabica "already lemmatized" claims were refuted or unconfirmable).
- **Defer the track.** It is materially harder than the Quran track (which had a
  ready-made, manually-verified lemma list). Revisit only if we invest in a classical
  lemmatization pipeline — most plausibly an **LLM-in-context pass** like polyglot already
  uses for Greek/Latin, applied to OpenITI samples — and benchmark its homograph
  disambiguation before committing.

## Findings (adversarially verified)

### [high · 3-0] OpenITI is the best raw text base — but raw text only
~2.25 billion words across 11,195 titles (6,785 unique), 2,843 authors, almost
exclusively premodern Arabic, fully downloadable from Zenodo (DOI 10.5281/zenodo.10007820,
v2023.1.8, 5.8 GB). Ships as raw oMARkdown with **no lemmatization, no frequency counts,
no bundled NLP tooling** — frequency/lemmatization is entirely a downstream build step.
- https://zenodo.org/records/10007820 · https://github.com/OpenITI/RELEASE

### [high · 3-0] OpenITI license is CC-BY-NC-SA 4.0 (NonCommercial + ShareAlike)
The NC clause prohibits primarily-commercial use; ShareAlike propagates to any derived
frequency data. Likely fine for a personal non-commercial trainer — but must be checked
against Alif's distribution status, and any shared derivative inherits ShareAlike.
- https://zenodo.org/records/10007820

### [high · 3-0] The only OpenITI/KITAB frequency tool is surface-token, not lemmatized
The KITAB Token Frequency Counter "considers any sequence of Arabic-script characters a
token" (clitics attached: ولكتابه = one token), and explicitly cannot tell كتب-as-book
from كتب-as-writing. This is the **exact surface-count-on-wrong-lemma conflation** we are
avoiding — so it is no shortcut.
- https://kitab-project.org/A-Token-Frequency-Counter-For-OpenITI-Texts/

### [high · 3-0] arabiCorpus has the right material but wrong shape, and is closing
A Premodern subcorpus (Quran, 1001 Nights, al-Jahiz/Adab, grammarians, medieval
philosophy/science, Hadith) exists but is only **9.1M words (~5% of the corpus**, which is
78% MSA newspapers). It is **neither POS-tagged nor lemmatized** — exact surface-string
KWIC search with mechanical prefix/suffix patterns, surface-form frequency only, **no
documented export** (download.php 404s), and a **retirement notice for 1 July 2027**.
- https://arabicorpus.byu.edu/instructions.php · http://www.ncolctl.org/files/making-vocabulary.pdf

### [high · 3-0 / 2-1] No off-the-shelf classical Arabic lemmatizer of verified quality
CAMeL Tools v1.0.0 ships only `calima-msa-r13` (MSA) + `calima-egy-r13` (Egyptian);
neither is classical, and no release added a verified premodern morphological database
(later versions added a Gulf DB; Camel Morph exists but adds no classical DB). A
peer-reviewed 2025 study (JOHD 10.5334/johd.418) processes classical/Quranic/Hadith text
**using the MSA CALIMA database** — confirming reliance on MSA tooling even for premodern
text. Classical lemmatization quality + homograph disambiguation is the central unsolved
technical risk for mapping any external classical lemma list back to Alif's lemma rows.
- https://camel-lab.github.io/.../v1.0.0.html · https://doi.org/10.5334/johd.418

## Refuted / unconfirmed (do NOT rely on)
- A ~1B-word **lemmatized historical-Arabic corpus** from arXiv 1612.08989 (Shamela-derived,
  MADAMIRA-lemmatized, "~95K lemmas") — **could not be confirmed as obtainable or genuinely
  lemmatized** (0-vote / refuted).
- **OpenArabic/RAWrabica** as a named repo (404); its frequency collection is **surface/token
  counts** (+ an orthographically-normalized variant that is *not* morphological lemmatization).
- Treat every "Shamela / someone already lemmatized it" shortcut as **UNCONFIRMED, not available.**

## Open questions (what a real go-decision needs)
1. Actual lemmatization accuracy + homograph-error rate of CAMeL's **MSA** CALIMA DB on
   premodern OpenITI text — no source measured this; needs a small annotated classical benchmark.
2. Does any obtainable, quality-verified lemmatized OpenITI/Shamela derivative exist (Camel
   Morph releases, KITAB internal pipelines)? Direct check of current GitHub orgs.
3. Does Alif's use qualify as non-commercial under CC-BY-NC-SA, and what does ShareAlike
   oblige for a derived/shared frequency list?
4. **Is an LLM-in-context lemmatize+disambiguate pass over OpenITI samples viable** (cost/
   quality vs rule-based MSA tooling)? This is the most promising build path and mirrors
   polyglot's Greek/Latin approach.

## Caveats
arabiCorpus retires 2027-07-01 (finite even as a surface tool). OpenITI version figures
are v2023.1.8 (counts wobble slightly across releases, immaterial to the verdict). The
CAMeL finding is scoped to v1.0.0 at release. All six findings rest on primary sources
(deposits, official docs, the tools' own instructions, a peer-reviewed paper) with
unanimous/near-unanimous votes; the one weaker link (a garbled-on-fetch NCOLCTL PDF) had
its load-bearing quotes independently confirmed against arabiCorpus's own docs. The
throughline risk (lemma→app-row mapping, homograph disambiguation) was **not empirically
measured for premodern text by any source** — the evidence establishes only that no
validated classical lemmatizer exists, not how bad an MSA-DB-on-classical pipeline is.

Full machine-readable result + per-claim votes: deep-research run on 2026-06-03.
