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

## Phase 2: Quality Benchmarks — COMPLETE (2026-02-19)

### Quick Wins (done)
- Changed 4 script defaults from `--model openai` to `--model gemini` (verify_sentences, pregenerate_material, generate_sentences, analyze_word_distribution)
- Fixed `audit_llm_usage.py` cost estimates: Gemini Flash $0.075→$0.50/M input, added proper output pricing

### Step 3: Benchmark Results

Benchmark script: `backend/scripts/benchmark_claude_code.py`

#### Sentence Generation (5 words × 3 sentences each)

| Model | Validator Pass% | Total Time | Per Word | Cost |
|-------|----------------|-----------|----------|------|
| Gemini Flash (API) | 73.3% | 14.2s | 2.8s | ~$0.001 |
| **Sonnet (CLI, per-word)** | **86.7%** | 160.4s | 32.1s | Free |
| Sonnet (CLI, batched) | 73.3% | 79.9s | 16.0s | Free |

**Key finding**: Sonnet per-word has the best vocabulary compliance. Batching cuts time 50% but quality dropped to Gemini level — the batch prompt may need tuning. Sonnet is ~11x slower than Gemini but free and higher quality.

**Invalid sentence analysis**: Most failures are conjugated forms not in forms_json (ذَهَبْتُ, مَكْتُوبَةٌ, وَجَدَتِ). This is a validator limitation, not a model quality issue — both Gemini and Sonnet produce natural Arabic.

#### Quality Gate (10 known-good sentences)

| Model | Approval% | Time | Notes |
|-------|-----------|------|-------|
| Gemini Flash (API) | 80% | 5.9s | |
| **Haiku (API)** | **90%** | 8.1s | Current production — best balance |
| Sonnet (CLI) | 90% | 41.7s | Same quality as Haiku API, 5x slower |
| Haiku (CLI) | 60% | 16.0s | Unexpectedly strict — possible prompt/format issue |

**Key finding**: Haiku API remains the best quality gate. Sonnet matches but is much slower. Haiku CLI behaves differently (more rejections) — likely a `--json-schema` vs `response_format` formatting difference worth investigating.

#### Forms Generation (8 words)

| Model | Match% | Avg Time |
|-------|--------|----------|
| Gemini Flash | 100% | 1.3s |
| Sonnet (CLI) | 100% | 12.4s |
| Haiku (CLI) | 100% | 6.9s |

All models produce identical quality forms. Gemini is 5-10x faster and this task is cheap ($0.001/word), so no reason to switch.

#### Memory Hooks (5 words)

| Model | Avg Time | Cognates/word | Mnemonic Quality |
|-------|----------|---------------|------------------|
| Gemini Flash | 3.4s | 2-3 | Good sound-alikes |
| **Sonnet (CLI)** | 29.9s | **3-4** | **Best — creative, more cognates** |
| Haiku (CLI) | 12.2s | 1-4 | Decent but less creative |

**Key finding**: Sonnet produces noticeably better mnemonics with more cognates. Worth using for hooks since they're generated once per word (low volume) and quality matters most.

### Recommendations

Based on benchmarks, here's the migration priority:

| Task | Current → Recommended | Rationale |
|------|----------------------|-----------|
| Sentence gen | Gemini → **Keep Gemini** | 2.8s vs 32s, pass rate difference is mostly validator false negatives. Gemini is fast+cheap. |
| Quality gate | Haiku API → **Keep Haiku API** | Best balance of accuracy (90%) and speed. CLI Haiku too strict. |
| Forms gen | Gemini → **Keep Gemini** | All models 100% match. Gemini is 5-10x faster. |
| Memory hooks | Haiku API → **Sonnet CLI** | Sonnet quality is noticeably better. Low volume (once per word). |
| Story gen | Opus API → **Opus CLI** | Already proven. Save API costs. |
| Flag eval | GPT-5.2 → **Gemini** | Only 9 flags total. GPT-5.2 is overkill. |

**Biggest savings opportunity**: Not model switching (costs are already low at ~$1.50/day) but **reducing false validator rejections** — expanding forms_json with more conjugation patterns would improve pass rates for ALL models equally.

### Phase 3: Implementation Complete

All background LLM tasks switched to Claude CLI (free via Max plan):

| Task | Old Model | New Model | Status |
|------|-----------|-----------|--------|
| Sentence gen (cron) | Gemini Flash | **Claude Sonnet CLI** | Done — `update_material.py --model claude_sonnet` |
| Sentence gen (on-demand) | Gemini Flash | **Gemini Flash** (kept) | No change — latency-critical |
| Quality gate | Haiku API ($) | **Haiku CLI** (free) | Done — `llm.py review_sentences_quality()` |
| Enrichment (forms) | Gemini Flash | **Haiku CLI** (free) | Done — `lemma_enrichment.py` |
| Enrichment (etymology) | Gemini Flash | **Haiku CLI** (free) | Done — `lemma_enrichment.py` |
| Memory hooks | Haiku API ($) | **Haiku CLI** (free) | Done — `memory_hooks.py` |
| Pre-gen warm cache | Gemini Flash | **Gemini Flash** (kept) | No change — runs during user session |
| Story gen | Opus API | Opus API (kept) | TODO — switch to Opus CLI |
| Flag eval | GPT-5.2 | GPT-5.2 (kept) | Negligible volume (9 total) |

**Key architecture decisions:**
- `llm.py` has `_generate_via_claude_cli()` — shells out to `claude -p` with `--output-format json`
- Model overrides `claude_sonnet` and `claude_haiku` route through CLI, all others through LiteLLM API
- On-demand generation (user-facing) stays on Gemini for ~1s latency
- Background/cron tasks use Claude CLI — ~15-30s latency is fine for batch jobs
- Claude CLI authenticated on server via `claude setup-token` (Max plan)

### Remaining Work

1. **Expand forms_json validator** — add past_1s, present_3fs, present_3p, passive forms to reduce false negatives
2. **Switch stories to Opus CLI** — already have `generate_story_claude.py`
3. **Agentic enrichment prototype** — one session for forms+etymology+hooks per batch
4. **Monitor post-deploy** — verify Claude CLI stability on server cron

## Key Files
- `backend/app/services/llm.py` — LLM router with Claude CLI integration
- `backend/app/services/claude_code.py` — Claude Code CLI wrapper (standalone scripts)
- `backend/scripts/benchmark_claude_code.py` — Benchmark script
- `backend/scripts/audit_llm_usage.py` — Cost audit script
- `backend/scripts/update_material.py` — Cron sentence generation (default: claude_sonnet)
- `research/experiment-log.md` — Updated with findings
- `IDEAS.md` — Updated with new ideas
