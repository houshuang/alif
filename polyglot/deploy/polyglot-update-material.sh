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
# One pass per run, in order:
#   1. warm_pages_ahead for Modern Greek (primary)
#      — keeps the next N (default 5) unread pages of every active story
#        already through the quality gate, so the user never waits when
#        flipping pages. Runs first because freshly verified pages are the
#        source of new lemmas that the sentence cache then needs to cover.
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

LANGUAGE="${POLYGLOT_WARM_LANGUAGE:-el}"
# 2026-05-22: bumped 16→48. With the picker now strictly preferring generated
# sentences over book fallbacks, the engaged-vocabulary pool (~2.1k active
# lemmas) had only ~98 LLM sentences — 1,890 active lemmas had zero generated
# coverage. At 16 lemmas/run × 8 runs/day the backlog cleared too slowly. 48 ×
# 8 = ~384 lemma-attempts/day (×SENTENCES_PER_TARGET), clearing the backlog in
# days while comfortably keeping up with new acquiring words (≤30/day cap).
MAX_LEMMAS="${POLYGLOT_WARM_MAX_LEMMAS:-48}"
SENTENCES_PER_TARGET="${POLYGLOT_WARM_SENTENCES_PER_TARGET:-2}"
# 1200→1800: 48 lemmas / BATCH_WORD_SIZE=4 = 12 Sonnet+Haiku batches; at up to
# ~2 min/batch worst case that's ~24 min, so give the phase 30 min of headroom.
TIMEOUT_SECONDS="${POLYGLOT_WARM_TIMEOUT_SECONDS:-1800}"
PAGES_BUFFER="${POLYGLOT_PAGES_AHEAD_BUFFER:-5}"
PAGES_MAX_PER_RUN="${POLYGLOT_PAGES_AHEAD_MAX_PER_RUN:-5}"
PAGES_TIMEOUT_SECONDS="${POLYGLOT_PAGES_AHEAD_TIMEOUT_SECONDS:-1200}"
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

echo "[$TIMESTAMP] Polyglot material cron start" >> "$LOG"

run_phase "warm_pages_ahead" timeout "$PAGES_TIMEOUT_SECONDS" \
  "$VENV" scripts/warm_pages_ahead.py \
  --language "$LANGUAGE" \
  --buffer "$PAGES_BUFFER" \
  --max-per-story "$PAGES_MAX_PER_RUN"

run_phase "review_existing_sentences" timeout "$REVIEW_EXISTING_TIMEOUT_SECONDS" \
  "$VENV" scripts/review_existing_sentences.py \
  --language "$LANGUAGE" \
  --source llm \
  --only-unreviewed \
  --limit "$REVIEW_EXISTING_MAX_SENTENCES" \
  --batch-size "$REVIEW_EXISTING_BATCH_SIZE"

run_phase "warm_sentence_cache" timeout "$TIMEOUT_SECONDS" \
  "$VENV" scripts/warm_sentence_cache.py \
  --language "$LANGUAGE" \
  --max-lemmas "$MAX_LEMMAS" \
  --sentences-per-target "$SENTENCES_PER_TARGET"

run_phase "translate_sentences" timeout "$TRANSLATE_TIMEOUT_SECONDS" \
  "$VENV" scripts/translate_sentences.py \
  --language "$LANGUAGE" \
  --max-sentences "$TRANSLATE_MAX_SENTENCES"

run_phase "enrich_lemma_philology" timeout "$ENRICH_TIMEOUT_SECONDS" \
  "$VENV" scripts/enrich_lemma_philology.py \
  --language "$LANGUAGE" \
  --max-lemmas "$ENRICH_MAX_LEMMAS" \
  --include-failed

echo "[$TIMESTAMP] Polyglot material cron done" >> "$LOG"
echo "---" >> "$LOG"
