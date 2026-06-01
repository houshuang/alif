#!/usr/bin/env bash
# Versioned material cron wrapper for Polyglot.
#
# Installed at /opt/polyglot-update-material.sh on the Hetzner VM as a symlink
# to /opt/alif/polyglot/deploy/polyglot-update-material.sh by
# deploy/deploy-polyglot.sh; intended crontab line (run every 3 hours offset
# from Alif's so they don't both hammer the LLM CLI at the same minute):
#
#     45 */3 * * * /opt/polyglot-update-material.sh >> /var/log/polyglot-update-material.log 2>&1
#
# One pass per run: the 5 phases below run for EACH language in
# POLYGLOT_LANGUAGES (default "el"; "el la" adds Latin), sequentially — one
# language fully before the next, in this single process (lock-safe; see the
# LANGUAGES comment below). Phase descriptions say "Modern Greek" but apply to
# whichever language the loop is on. In order:
#   1. warm_pages_ahead
#      — keeps the next N (default 10) unread pages of every active story
#        already through the quality gate, so the user never waits when
#        flipping pages. Runs first because freshly verified pages are the
#        source of new lemmas that the sentence cache then needs to cover.
#        Buffer was 5 until 2026-05-26; bumped to 10 because heavy-reading
#        Latin sessions outpaced the 3h cron cadence (user advancing >5 pages
#        between cron passes). Frontend also prefetches 5 pages ahead so the
#        client bridges any in-session gap.
#   2. review_existing_sentences for Modern Greek
#      — backfills the sentence quality gate for legacy LLM-generated rows and
#        retires rows that are unnatural or mistranslated.
#   3. warm_sentence_cache for Modern Greek
#      — finds acquiring/learning/known lemmas below ACTIVE_TARGET sentence
#        coverage and generates more via the configured structured LLM CLI.
#   4. translate_sentences for Modern Greek
#      — fills translation_en for harvested book sentences (left NULL by the
#        harvest, which holds no LLM call) that cover an active-study lemma, so
#        the picker's book-sentence fallback never renders blank. Runs through
#        the configured structured LLM CLI, lazily here and never on the read
#        path.
#   5. enrich_lemma_philology for Modern Greek
#      — fills LemmaEnrichment (etymology, diachrony, cognates, quotes,
#        register) for engaged lemmas. Surfaced in the lookup card + lemma
#        detail screen (Modern Editorial design).
#
# Future passes (deferred — no harvest-side fixes needed yet):
#   - rotate_stale_sentences (when pipeline tiers land)
#   - frequency_core_intake (Polyglot doesn't have a tiered frequency intake
#     pipeline yet — for now Page imports drive the lemma pool).
#
# Update procedure:
#   commit to main, then run polyglot/deploy/deploy-polyglot.sh

set -u

LOG="${POLYGLOT_MATERIAL_CRON_LOG:-/var/log/polyglot-update-material.log}"
TIMESTAMP="$(date +"%Y-%m-%d %H:%M:%S")"
WORKDIR="${POLYGLOT_BACKEND_DIR:-/opt/alif/polyglot}"
VENV="${POLYGLOT_PYTHON:-$WORKDIR/.venv/bin/python3}"
PYTHONPATH_VALUE="${PYTHONPATH:-/opt/limbic}"

# Languages processed per run, space-separated. Default Greek only; set
# POLYGLOT_LANGUAGES="el la" to add Latin once its vocab is seeded.
#
# CRITICAL — SQLite single-writer lock: the phases below run SEQUENTIALLY, one
# language fully before the next, inside this one process. Each phase follows the
# read→LLM→write discipline (commit between LLM calls), so the write lock is only
# held briefly. Do NOT add Latin as a second, concurrent cron job — two writers
# on one polyglot.db (WAL = one writer at a time) would contend and throw
# "database is locked". Sequential-in-one-process is the lock-safe design.
LANGUAGES="${POLYGLOT_LANGUAGES:-${POLYGLOT_WARM_LANGUAGE:-el}}"
# 2026-05-24: session selection now avoids recent sentence repeats and caps
# textbook fallbacks, so the generated cache needs more depth. ACTIVE_TARGET=5
# gives each retrieval target enough approved LLM rows for repeated acquisition
# reviews without resurfacing yesterday's sentence. 64 lemmas/run × 8 runs/day,
# 3 requested sentences/target, clears the current small backlog quickly while
# quality review still fails closed before storage.
MAX_LEMMAS="${POLYGLOT_WARM_MAX_LEMMAS:-64}"
SENTENCES_PER_TARGET="${POLYGLOT_WARM_SENTENCES_PER_TARGET:-3}"
export POLYGLOT_ACTIVE_TARGET="${POLYGLOT_ACTIVE_TARGET:-5}"
# Lever B (coverage): after retrieval gaps, plant this many never-confirmed
# assumed-known words into the corpus per pass so the confirmation sweep can
# reach words currently in zero reviewable sentences. Lower priority than
# retrieval gaps and disjoint by knowledge state, so it never starves them.
# 24/run × 8 runs/day chips through the unconfirmed-in-zero-sentences tail
# (~459 Greek / ~553 Latin at last count) without dominating LLM budget.
COVERAGE_MAX_LEMMAS="${POLYGLOT_WARM_COVERAGE_MAX_LEMMAS:-24}"
# 64 lemmas / BATCH_WORD_SIZE=4 = 16 Sonnet+verify+quality batches. Give the
# phase 45 minutes so slow LLM calls don't truncate an otherwise healthy pass.
TIMEOUT_SECONDS="${POLYGLOT_WARM_TIMEOUT_SECONDS:-2700}"
PAGES_BUFFER="${POLYGLOT_PAGES_AHEAD_BUFFER:-10}"
PAGES_MAX_PER_RUN="${POLYGLOT_PAGES_AHEAD_MAX_PER_RUN:-10}"
PAGES_TIMEOUT_SECONDS="${POLYGLOT_PAGES_AHEAD_TIMEOUT_SECONDS:-1800}"
REVIEW_EXISTING_MAX_SENTENCES="${POLYGLOT_REVIEW_EXISTING_MAX_SENTENCES:-80}"
REVIEW_EXISTING_BATCH_SIZE="${POLYGLOT_REVIEW_EXISTING_BATCH_SIZE:-10}"
REVIEW_EXISTING_TIMEOUT_SECONDS="${POLYGLOT_REVIEW_EXISTING_TIMEOUT_SECONDS:-900}"
# 2026-05-22: translate harvested book sentences whose translation_en is still
# NULL (covering an active-study lemma). Batched at 12/call by default — 200
# sentences is ~17 calls, comfortably under the phase timeout.
TRANSLATE_MAX_SENTENCES="${POLYGLOT_TRANSLATE_MAX_SENTENCES:-200}"
TRANSLATE_TIMEOUT_SECONDS="${POLYGLOT_TRANSLATE_TIMEOUT_SECONDS:-1200}"
# 2026-05-21: fourth phase enriches lemmas with philological data (etymology,
# diachrony, cognates, quotes, register). Selector prioritises active study:
# acquiring (sorted by next-due) → learning/lapsed → encountered. `known`
# lemmas are excluded — once a word is learnt the lookup card stops being
# load-bearing. See find_unenriched_lemmas docstring for the full policy.
# Cap sized for heavy-reading days: at ~70s/batch × 4 lemmas/batch, 30 lemmas
# takes ~9 min — well under the 30-min phase timeout, leaving headroom for
# slow LLM responses. An in-process lock in batch_enrich prevents two
# overlapping cron runs from double-spending LLM calls on the same lemmas.
ENRICH_MAX_LEMMAS="${POLYGLOT_ENRICH_MAX_LEMMAS:-30}"
ENRICH_TIMEOUT_SECONDS="${POLYGLOT_ENRICH_TIMEOUT_SECONDS:-1800}"

export PYTHONUNBUFFERED=1
# Belt-and-suspenders: explicitly point at polyglot's DB so the cron run
# doesn't depend on env_file loading order (the systemd unit sets this via
# EnvironmentFile, but cron has its own minimal environment). Caused a
# silent write to alif.db on first run (2026-05-20).
export DATABASE_URL="${DATABASE_URL:-sqlite:////opt/alif/polyglot/polyglot.db}"
export POLYGLOT_QUALITY_GATE="${POLYGLOT_QUALITY_GATE:-1}"
export POLYGLOT_LEMMA_REPAIR="${POLYGLOT_LEMMA_REPAIR:-1}"
export POLYGLOT_LLM_PROVIDER="${POLYGLOT_LLM_PROVIDER:-codex}"
export POLYGLOT_CODEX_MODEL="${POLYGLOT_CODEX_MODEL:-gpt-5.5}"
export POLYGLOT_CODEX_REASONING_EFFORT="${POLYGLOT_CODEX_REASONING_EFFORT:-medium}"
export POLYGLOT_CODEX_HOME="${POLYGLOT_CODEX_HOME:-/opt/alif/.codex}"
export CODEX_HOME="${CODEX_HOME:-$POLYGLOT_CODEX_HOME}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-${OPENAI_KEY:-}}"

run_phase() {
  local name="$1"
  shift
  echo "[$TIMESTAMP] Starting $name" >> "$LOG"
  (
    cd "$WORKDIR" &&
      PYTHONPATH="$PYTHONPATH_VALUE" "$@"
  ) >> "$LOG" 2>&1
  local status=$?
  if [ "$status" -ne 0 ]; then
    echo "[$TIMESTAMP] $name exited with status $status" >> "$LOG"
  fi
  return "$status"
}

echo "[$TIMESTAMP] Polyglot material cron start (languages: $LANGUAGES)" >> "$LOG"

# Sequential per language (lock-safe — see the LANGUAGES comment above).
for LANGUAGE in $LANGUAGES; do
  echo "[$TIMESTAMP] === language: $LANGUAGE ===" >> "$LOG"

  run_phase "$LANGUAGE warm_pages_ahead" timeout "$PAGES_TIMEOUT_SECONDS" \
    "$VENV" scripts/warm_pages_ahead.py \
    --language "$LANGUAGE" \
    --buffer "$PAGES_BUFFER" \
    --max-per-story "$PAGES_MAX_PER_RUN"

  run_phase "$LANGUAGE review_existing_sentences" timeout "$REVIEW_EXISTING_TIMEOUT_SECONDS" \
    "$VENV" scripts/review_existing_sentences.py \
    --language "$LANGUAGE" \
    --source llm \
    --only-unreviewed \
    --limit "$REVIEW_EXISTING_MAX_SENTENCES" \
    --batch-size "$REVIEW_EXISTING_BATCH_SIZE"

  run_phase "$LANGUAGE warm_sentence_cache" timeout "$TIMEOUT_SECONDS" \
    "$VENV" scripts/warm_sentence_cache.py \
    --language "$LANGUAGE" \
    --max-lemmas "$MAX_LEMMAS" \
    --sentences-per-target "$SENTENCES_PER_TARGET" \
    --coverage-max-lemmas "$COVERAGE_MAX_LEMMAS"

  run_phase "$LANGUAGE translate_sentences" timeout "$TRANSLATE_TIMEOUT_SECONDS" \
    "$VENV" scripts/translate_sentences.py \
    --language "$LANGUAGE" \
    --max-sentences "$TRANSLATE_MAX_SENTENCES"

  run_phase "$LANGUAGE enrich_lemma_philology" timeout "$ENRICH_TIMEOUT_SECONDS" \
    "$VENV" scripts/enrich_lemma_philology.py \
    --language "$LANGUAGE" \
    --max-lemmas "$ENRICH_MAX_LEMMAS" \
    --include-failed
done

echo "[$TIMESTAMP] Polyglot material cron done" >> "$LOG"
echo "---" >> "$LOG"
