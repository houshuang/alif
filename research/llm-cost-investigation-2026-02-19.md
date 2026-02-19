# LLM Cost Investigation — Phase 1 Complete (2026-02-19)

## Goal
Investigate shifting LLM work from paid APIs (Gemini Flash, GPT-5.2, Haiku API) to Claude Code CLI (free via Max plan) for equal or better quality at lower cost.

## What We Learned

### Current LLM Architecture
- **`backend/app/services/llm.py`**: Central router. `generate_completion()` with fallback chain: Gemini Flash → GPT-5.2 → Haiku → Opus. Most callers set `model_override` to use one model only.
- **`backend/app/services/claude_code.py`**: Local-only wrapper for `claude -p`. Two modes: `generate_structured()` (single-turn JSON) and `generate_with_tools()` (multi-turn agentic with Read/Bash).
- **Claude Code does NOT work in Docker** — only on local machine. Server cron jobs can't use it without installing Node.js + claude CLI on server.

### All LLM Task Sites (18 callers tagged with task_type)

| Task Type | Caller | Model | Volume | Notes |
|-----------|--------|-------|--------|-------|
| `sentence_gen` | llm.py `generate_sentence()` | Gemini Flash | High (cron) | Single target word |
| `sentence_gen_batch` | llm.py `generate_sentences_batch()` | Gemini Flash | High (cron) | Multiple sentences per call |
| `sentence_gen_multi` | llm.py `generate_sentences_multi_target()` | Gemini Flash | High (cron) | 2+ target words per sentence |
| `quality_review` | llm.py `review_sentences_quality()` | Haiku (API) | High (per sentence batch) | Fail-closed gate |
| `enrichment_forms` | lemma_enrichment.py | Gemini Flash (fallback) | Medium (post-import) | forms_json for vocab expansion |
| `enrichment_etymology` | lemma_enrichment.py | Gemini Flash (fallback) | Medium (post-import) | etymology_json |
| `memory_hooks` | memory_hooks.py | Haiku (API) | Low (per acquiring word) | Mnemonics, cognates |
| `variant_detection` | variant_detection.py | Gemini Flash | Low (post-import) | Confirm morphological variants |
| `import_quality` | import_quality.py | Gemini Flash (fallback) | Low (post-import) | Classify standard/proper/junk |
| `flag_evaluation` | flag_evaluator.py | GPT-5.2 | Negligible (9 total) | Auto-fix flagged content |
| `story_gen` | story_service.py | Opus (API) | Rare | Full stories |
| `story_title` | story_service.py | Gemini Flash (fallback) | Rare | Title generation |
| `story_word_import` | story_service.py | Gemini Flash (fallback) | Rare | Translate unknown words |
| `mapping_verification` | sentence_validator.py | Gemini Flash | Off (VERIFY_MAPPINGS_LLM=0) | Homograph disambiguation |
| `morphology` | morphology.py | Gemini Flash (fallback) | One-time | Root meaning backfill |
| `grammar_tag` | grammar_tagger.py | Gemini Flash (fallback) | One-time | Grammar pattern tagging |
| `book_import` | book_import_service.py | Gemini Flash (fallback) | Rare | OCR cleanup + translation |
| `chat` | chat.py router | Gemini Flash (fallback) | Rare | User Q&A |

### Cost Analysis (Feb 8-19, server logs)

**Gemini 3 Flash pricing**: $0.50/M input tokens, $3.00/M output tokens

| Model | Successful Calls | Input Tokens (est) | Est. Total Cost |
|-------|-----------------|-------------------|----------------|
| Gemini Flash | 12,388 | ~10.5M | $10-15 |
| GPT-5.2 | 1,791 | ~861K | $3-5 |
| Haiku (API) | 963 | ~239K | $0.34 |
| OCR (vision) | 230 | unknown (images) | unknown |
| **Total** | **15,142** | | **~$14-20** |

**GPT-5.2 was a one-time spike** (Feb 11-12 redesign scripts), not ongoing. Multiple scripts (`verify_sentences.py`, `generate_sentences.py`, `pregenerate_material.py`, `analyze_word_distribution.py`) default to `--model openai` — should be changed to `gemini`.

**Ongoing daily cost** (post-redesign, excluding one-time scripts): ~$1-1.50/day, almost entirely Gemini Flash.

**By task category** (Gemini Flash only, inferred from prompt length):
- Enrichment (<500 chars): 6,263 calls — $0.08 (cheap but high volume)
- Sentence gen (2k-8k chars): 2,979 calls — $0.66
- Multi-target sentence gen (8k+): 1,294 calls — $1.46
- Quality review/forms (500-2k): 1,852 calls — $0.16

### Claude Code CLI Capabilities
- **`generate_structured()`**: Single-turn, `--json-schema` validation, models: opus/sonnet/haiku
- **`generate_with_tools()`**: Multi-turn agentic with Read/Bash, self-validation loops, $0.50 budget cap
- **`dump_vocabulary_for_claude()`**: Exports vocab as `vocab_prompt.txt` + `vocab_lookup.tsv` for Claude sessions
- **Existing scripts**: `generate_story_claude.py`, `generate_sentences_claude.py`, `audit_sentences_claude.py`
- **Local only** — `is_available()` checks for `claude` in PATH, returns False in Docker

### Server Health
- Gemini Flash + Haiku both work fine on server (96%+ success rate)
- `update_material.py` cron was crashing since Feb 17 18:30 — **fixed and deployed** (None stability bug)
- Cron runs every 6h: `rotate_stale_sentences.py` then `update_material.py --limit 50`
- Cron script at `/opt/alif-update-material.sh`, log at `/var/log/alif-update-material.log`

### Bugs Found & Fixed
1. **`update_material.py:531`**: `card.get("stability", 0)` → `(card.get("stability") or 0)` — None stability crashes audio eligibility check
2. **Script defaults**: Several scripts default to `--model openai` instead of `--model gemini` (not yet changed)

### What Was Committed
- `6e5ad70` — task_type logging on all 18 callers, audit script, cron fix

## Phase 2 Plan: Quality Benchmarks + Enrichment Agent

### Step 3: Benchmark Claude Sonnet/Haiku vs Gemini Flash

Build `backend/scripts/benchmark_claude_code.py`:

1. **Sentence generation** (biggest ongoing cost):
   - Generate 20 sentences each with: Gemini Flash (via LiteLLM), Claude Sonnet (via CLI), Claude Haiku (via CLI)
   - Run through existing validator + quality gate
   - Compare: pass rate, vocabulary compliance, naturalness
   - Use `generate_structured()` from `claude_code.py`

2. **Forms generation**:
   - Generate forms_json for 20 words with each model
   - Compare against existing verified forms in DB

3. **Quality gate**:
   - Take 20 known-good + 10 known-bad sentences
   - Run quality review with each model
   - Compare precision/recall

4. **Memory hooks**:
   - Generate hooks for 10 words with each model
   - Subjective quality comparison

### Step 4: Agentic Enrichment Agent Prototype

Build `backend/scripts/enrich_words_claude.py`:
- One Claude Code session with tools that reads DB dump, identifies words missing enrichment, generates ALL enrichment (forms + etymology + hooks) in one pass, validates, returns structured batch
- Uses `generate_with_tools()` with Read/Bash
- Compare against running separate scripts

### Step 5: Architecture Decision

Three options:
- **A) Local cron**: Mac runs enrichment, syncs to server. Simple but depends on Mac.
- **B) Claude Code on server**: Install Node.js + claude CLI in Docker. Fully automated.
- **C) Hybrid MCP**: Build MCP server for DB access. Most flexible, most work.

### Quick Wins (can do now)
- Change script defaults from `--model openai` to `--model gemini`
- Fix `audit_llm_usage.py` cost estimates (was using wrong Gemini pricing)

## Key Files
- `backend/app/services/llm.py` — LLM router, all task_type tags
- `backend/app/services/claude_code.py` — Claude Code CLI wrapper
- `backend/scripts/audit_llm_usage.py` — Cost audit script (NEW)
- `backend/scripts/update_material.py` — Cron sentence generation (FIXED)
- `research/experiment-log.md` — Updated with findings
- `IDEAS.md` — Updated with new ideas
