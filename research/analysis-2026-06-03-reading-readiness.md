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

### Saud al-Sanousi — *The Bamboo Stalk* (ساق البامبو, 2012), full novel (72,805 tokens)
The contemporary target (IPAF 2013). Fetched via the AA member fast-download API.

| metric | value |
|---|---|
| coverage now (function + known) | **82.4%** |
| + in-progress vocab | 86.8% |
| distinct gap words | 2,464 |
| coverage curve | +50 → 85.7% · +150 → 87.8% · +300 → 89.5% · **+500 → 91.0%** |

Top unlocks mix real content gaps and book-specific vocab: فيليبيني (Filipino — the
half-Kuwaiti/half-Filipino protagonist, 73×), عاطفة (emotion), ملامح (features), أريكة (sofa),
نداء (call), أحاط (to surround), plus some recurring names. Reaches ~91% after 500 targeted
words — close to but short of comfortable reading.

### Ibrahim Ramzi — *Bab al-Qamar* (باب القمر, 1936), full novel (124,171 tokens)
A sprawling **historical** novel set in early-Islamic Egypt.

| metric | value |
|---|---|
| coverage now (function + known) | **80.1%** |
| + in-progress vocab | 86.5% |
| distinct gap words | 3,827 |
| coverage curve | +50 → 83.1% · +150 → 85.1% · +300 → 86.8% · **+500 → 88.1%** |

Top unlocks are dominated by **proper names** (اسكندرية Alexandria, هيلان, امبراطور emperor,
بطريق patriarch, رؤبة, أوس) and **classical/historical vocabulary** (مولى, بعير camel,
جنّد to conscript, التمس to seek). The curve stays far below the 95–98% threshold even after
500 words.

## The key insight: register dominates the readiness curve, not vocab size

Comparing the **two full novels** (similar enough in size to control for the
distinct-vocabulary-grows-with-length effect that flattered the short Kanafani excerpt):
contemporary *Bamboo Stalk* reaches **91% after 500 words**, the 1936 historical
*Bab al-Qamar* only **88%** — and its gap is a long tail of low-frequency classical terms and
a dense cast of proper names that no realistic study list closes. Same learner, same
vocabulary; the readiness curve is set by the **book's register and name density**, not by how
many words you know. For the "genuinely-known words week over week" north star, this argues for
**choosing first reading material by readiness-curve shape, not prestige** — the analyzer makes
that curve visible per-book before committing. (The Kanafani chapter hitting 96% after 150
words is partly an artifact of its small size — fewer distinct words to cover — so a full
contemporary novel is the fairer benchmark: ~82% now, a few hundred words from comfortable.)

## Caveats (this is a cheap estimate, not ground truth)

- **Coverage is a lower bound.** CAMeL's proper-name (`noun_prop`) detection is imperfect, so
  many names (اسكندرية, امبراطور, هيلان) leak into the OOV/gap list instead of being scored
  as readable. A reader recognizes a name without "learning" it, so true readability is higher
  than reported — especially for the name-heavy historical novel.
- **Lemmatization noise.** راوند ("curves") recurs as a phantom top-unlock in *both* novels
  (297× / 85×) — a systematic CAMeL mis-lemmatization (likely a name/form collapsing to that
  lemma), not a real content word. A handful of common verbs (بقي, بكى, التمس) show as OOV
  because CAMeL's lemma didn't match an Alif row — real gaps or lookup misses, hard to tell
  cheaply. Worth a one-off `_LEMMA_OVERRIDES`-style fix if the tool gets recurring use.
- **Function-word floor.** Particles/names are counted as trivially readable (35% of tokens
  here), which is correct for comprehension but means the "content" gap is the real signal.

## How it was run

`scripts/reading_readiness.py --text <file>` (accepts `.txt`/`.html`/`.epub`). No LLM; cost is
CAMeL morphology over the OOV tokens (cached by surface). Texts were scanned for vocabulary
only; no book content is stored in the repo. *The Bamboo Stalk* was fetched via the Anna's
Archive member fast-download API (the slow/z-lib mirrors sit behind Cloudflare browser
challenges that scripted downloads can't pass); *Bab al-Qamar* came from the open Hindawi
corpus (HuggingFace).
