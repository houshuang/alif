# Sentence Pipeline Investigation — 2026-02-13

Benchmarked 213 sentences across 3 models x 6 strategies to find the best sentence generation pipeline for Alif.

## Reports

- **[corpus_evaluation.md](corpus_evaluation.md)** — Evaluation of Tatoeba and BAREC as sentence sources for vocabulary-matched review
- **[generation_benchmark.md](generation_benchmark.md)** — LLM benchmark: GPT-5.2 vs Gemini Flash vs Claude Haiku across 6 prompt strategies
- **[recommendations.md](recommendations.md)** — Actionable recommendations synthesized from both evaluations
- **[all_sentences.jsonl](all_sentences.jsonl)** — Raw data: all 213 generated sentences with quality scores and metadata

## Key Findings

- **GPT-5.2 is the worst model** for Arabic generation (4.63/5 quality, produced all 5 "word salad" sentences)
- **Gemini Flash is the best** (4.89/5 quality, 84% compliance, cheapest, fastest)
- **KNOWN_SAMPLE_SIZE=50 is the #1 problem** — GPT-5.2 compliance jumps 57%→88% with full vocab
- **POS-grouped vocabulary** scored 5.0/5 quality, 87% compliance
- **Quality gate fails open** — bad sentences pass when Gemini is unavailable

## Implementation

All recommendations implemented in the [Sentence Pipeline Overhaul](../experiment-log.md) (2026-02-13).
