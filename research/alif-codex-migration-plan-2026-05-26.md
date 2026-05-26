# Alif hybrid Codex migration — plan

Captured 2026-05-26 after the Codex `gpt-5.5` vs Claude Sonnet A/B (see
`codex-vs-claude-sentence-gen-2026-05-26.md`). User direction:
**hybrid migration — audit pipelines move to Codex, generation stays on
Claude.** Execution deferred to a dedicated session (Latin issues take
priority).

## Findings from the A/B (8 targets × 5 candidates × 2 providers)

| | Claude Sonnet | Codex gpt-5.5 |
|---|---|---|
| Time | 906s (22.6s/sentence) | 253s (6.3s/sentence) |
| Grammar errors | 0/40 | 1/40 (def/indef relative `الَّتِي`) |
| Vocab compliance | ~85% (uses out-of-list content words for fluency) | ~95% |
| Semantic coherence | 40/40 | 37/40 (joins unrelated facts under constraint) |
| Stylistic variety | Higher (classical particles, varied starters) | More uniform (default VSO + adverbial) |

**Read:** Codex is **comparable but not equal** on Arabic — the gap is real
on naturalness and coherence under vocab constraint, but no longer the
"clearly worse" of the earlier-Codex era. Speed advantage (3.6×) is large.

## Scope: which Alif pipelines flip

Audit / classifier pipelines (currently `claude_haiku` via Claude CLI):

| Service | Function | Why safe to flip |
|---|---|---|
| `sentence_validator.py` | `verify_and_correct_mappings_llm` | JSON audit, structured |
| `sentence_validator.py` | `apply_corrections` LLM step | Mapping correction proposals |
| `llm.py` | `rerank_sentences_by_naturalness` | Sentence scoring (closed-class) |
| `lemma_quality.py` | LLM-in-context lemma audit | JSON audit |
| Disambiguation service | Lemma pick from candidates | Closed-class pick |
| Flag / tagging services | Classifier paths | Structured outputs |

Generation pipelines (**stay on Claude**):

- `generate_sentence` / `generate_sentences_batch` (`claude_sonnet`)
- Story generation (`opus` via `claude_code.py`)

Test-first pipeline (**A/B done 2026-05-26 — flips with the audit pipelines**):

- ~~Lemma enrichment (root/pattern/grammar notes). Accuracy-sensitive,
  user complained about Latin philology quality 2026-05-26 — symptom
  may have a prompt cause, not a provider cause. Run the philology A/B
  before flipping.~~ **A/B run on 10 morphologically-diverse Arabic
  lemmas (see `codex-vs-claude-enrichment-arabic-2026-05-26.md`): Codex
  `gpt-5.5` outperforms Claude Haiku on Arabic enrichment.** Pattern
  naming canonical 9/9 (Claude 5/9 — `if'tala` vs `ifta'ala`, etc.);
  cultural notes filled 10/10 vs 3/10, all fact-checked correct;
  diacritization complete (Claude drops indicative mood vowels);
  hollow-verb morphology both pass the critical past_1s test (قُلْتُ
  not قَالْتُ). The Latin philology complaint was indeed prompt+parser
  (closed by PR #157), not a provider issue. **Enrichment flips with
  the audit pipelines — no separate decision needed.**

## Implementation shape (when the time comes)

1. **Port codex CLI subprocess** from polyglot's `app/services/llm_cli.py`
   (`_call_codex`, `_call_codex_text`, `_codex_env`, `_codex_reasoning_args`,
   `_run_codex`). Adapt to Alif's `_CLEAN_ENV` pattern + cost-logging hooks.
2. **Provider-routing helper**: env var `ALIF_AUDIT_PROVIDER` (default
   `claude`, valid values `claude` / `codex`). When set to `codex`, the
   `claude_haiku` model alias routes to Codex CLI instead of Claude CLI.
3. **Fallback chain** for codex audit calls: Codex CLI → Claude CLI → Claude
   API. Extends the existing `_generate_via_claude_cli` failure path.
4. **Cost logging**: verify limbic.cerebellum cost callbacks fire for Codex
   subprocess calls (different transport than litellm). May need a manual
   log_call entry from the `_call_codex` shim.
5. **Tests**: extend `test_llm.py` with provider-routing assertions; mock
   subprocess.run to simulate Codex / Claude failure modes; assert fallback
   chain order under each provider config.
6. **Rollout**: flag off by default → test in dev with `ALIF_AUDIT_PROVIDER=
   codex` → soak for 24-48h on production with flag on → flip default to
   `codex` → remove flag.

## Estimated effort

~2-4 focused hours of coding + testing + dev validation. Then a soak
period before flipping the production default.

## Open questions to resolve before execution

- **Cost-log compatibility**: does `limbic.cerebellum.cost_log` already
  understand Codex calls (since polyglot uses Codex and routes through the
  same cost DB)? If yes, Alif gets it for free. If no, add a Codex-aware
  log entry.
- **Codex quota / rate limits**: polyglot's cron does 64 lemmas/run at
  ~3h intervals. Alif's cron volume is higher (sentence-gen + audit + now
  enrichment). Need to confirm Codex CLI rate limits handle the combined
  Alif + polyglot load. Failure modes are the same as Claude CLI quota
  (cooldown + failover), but the limit threshold matters.
- ~~**Enrichment A/B**~~ **RESOLVED 2026-05-26.** Codex wins on Arabic
  enrichment (pattern naming canonical 9/9, cultural notes 10/10 all
  correct, full diacritization). Enrichment flips with the audit
  pipelines. See `codex-vs-claude-enrichment-arabic-2026-05-26.md`.

## Rejected alternatives

- **Full Codex migration (all pipelines including generation)**: Codex's
  Arabic naturalness gap on sentence-generation is real (vocab compliance
  is paradoxically *too high* — Codex generates more constrained but less
  fluent prose). Migration there would degrade the user-facing review
  experience.
- **All-Claude (status quo)**: leaves Codex's 3.6× speed advantage on the
  table for high-volume audit calls. Audit pipelines are the natural fit
  for the speed delta because their quality bar is "structured output
  correct" rather than "Arabic reads naturally."
