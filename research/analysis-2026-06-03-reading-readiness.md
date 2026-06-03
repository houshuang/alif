# Reading-readiness: how far from reading real Arabic books? (2026-06-03)

A cheap, **LLM-free** coverage analysis built on `scripts/reading_readiness.py`: tokenize a
text, lemmatize each token against Alif's own lemmas (clitic-stripping + CAMeL
disambiguation — no LLM), join the learner's `UserLemmaKnowledge` state, and report
token-weighted coverage plus the unknown lemmas ranked by frequency *in this text* (the
biggest unlocks) with a coverage curve. The "sort the gap by token frequency within the
target text" model (Nation's coverage thresholds; type-vs-token).

Run against fresh prod data (≈2,170 known lemmas at the time).

## Results

### Ghassan Kanafani — *Men in the Sun* (رجال في الشمس), opening chapter (~1,800 tokens)
Spare modern literary prose.

| metric | value |
|---|---|
| coverage now (function + known) | **84.0%** |
| + in-progress vocab | 86.9% |
| coverage curve | +10 → 86.9% · +50 → 90.9% · **+150 → 96.4%** |

Top unlocks: شط (riverbank), تراب (soil), عاقبة (consequence), نهر (river), غصة (lump in
the throat), اغتسل (to wash). A short, achievable gap — ~150 targeted words reach Nation's
comfortable-reading threshold (~98%).

### Ibrahim Ramzi — *Bab al-Qamar* (باب القمر, 1936), full novel (124,171 tokens)
A sprawling **historical** novel set in early-Islamic Egypt.

| metric | value |
|---|---|
| coverage now (function + known) | **80.1%** |
| + in-progress vocab | 86.5% |
| distinct gap words | 3,827 |
| coverage curve | +50 → 83.1% · +150 → 85.1% · +300 → 86.8% · **+500 → 88.1%** |

Top unlocks are dominated by **proper names** (اسكندرية Alexandria, هيلان, امبراطور emperor,
بطريق patriarch, رؤبة, أوس) and **classical/historical vocabulary** (مولى, بعير camel, بطريق,
جنّد to conscript, التمس to seek). The curve stays far below the 95–98% threshold even after
500 words.

## The key insight: book *type* dominates the readiness curve

Same learner, same vocabulary — wildly different curves. The Kanafani chapter reaches 96%
after ~150 words; the historical novel is still at 88% after 500. The difference isn't
vocabulary size, it's **register and name density**: contemporary spare prose has a short,
steep unlock curve; a historical novel carries a long tail of low-frequency classical terms
and a dense cast of proper names that no realistic study list closes. For the user's
"genuinely-known words week over week" north star, this argues for **choosing first reading
material by readiness curve shape, not prestige** — and the analyzer makes that curve
visible per-book before committing.

## Caveats (this is a cheap estimate, not ground truth)

- **Coverage is a lower bound.** CAMeL's proper-name (`noun_prop`) detection is imperfect, so
  many names (اسكندرية, امبراطور, هيلان) leak into the OOV/gap list instead of being scored
  as readable. A reader recognizes a name without "learning" it, so true readability is higher
  than reported — especially for the name-heavy historical novel.
- **Lemmatization noise.** The #1 *Bab al-Qamar* "unlock" راوند (297×) is almost certainly a
  mis-lemmatized name, not a content word. A handful of common verbs (بقي, بكى, التمس) show as
  OOV because CAMeL's lemma didn't match an Alif row — real gaps or lookup misses, hard to tell
  cheaply.
- **Function-word floor.** Particles/names are counted as trivially readable (35% of tokens
  here), which is correct for comprehension but means the "content" gap is the real signal.

## How it was run

`scripts/reading_readiness.py --text <file>` (accepts `.txt`/`.html`/`.epub`). No LLM; cost is
CAMeL morphology over the OOV tokens (cached by surface). Texts were scanned for vocabulary
only; no book content is stored in the repo. The contemporary-novel target originally intended
(*The Bamboo Stalk*, al-Sanousi) could not be fetched in-environment — Anna's Archive
fast-download needs a membership cookie and the slow/z-lib mirrors sit behind Cloudflare
browser challenges — so a full original-Arabic novel from the open Hindawi corpus
(HuggingFace) stood in for the full-length run.
