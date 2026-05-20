#!/usr/bin/env bash
# Versioned material cron wrapper for Polyglot.
#
# Installed at /opt/polyglot-update-material.sh on the Hetzner VM; intended
# crontab line (run every 3 hours offset from Alif's so they don't both hammer
# the Claude CLI at the same minute):
#
#     45 */3 * * * /opt/polyglot-update-material.sh >> /var/log/polyglot-update-material.log 2>&1
#
# One pass per run, in order:
#   1. warm_sentence_cache for Modern Greek (primary)
#      — finds acquiring/learning/known lemmas below ACTIVE_TARGET sentence
#        coverage and generates more via Claude CLI (Sonnet) + Haiku verify.
#
# Future passes (deferred — no harvest-side fixes needed yet):
#   - rotate_stale_sentences (when pipeline tiers land)
#   - frequency_core_intake (Polyglot doesn't have a tiered frequency intake
#     pipeline yet — for now Page imports drive the lemma pool).
#
# Update procedure:
#   scp polyglot/deploy/polyglot-update-material.sh alif:/opt/polyglot-update-material.sh
#   ssh alif chmod +x /opt/polyglot-update-material.sh

set -u

LOG="${POLYGLOT_MATERIAL_CRON_LOG:-/var/log/polyglot-update-material.log}"
TIMESTAMP="$(date +"%Y-%m-%d %H:%M:%S")"
WORKDIR="${POLYGLOT_BACKEND_DIR:-/opt/alif/polyglot}"
VENV="${POLYGLOT_PYTHON:-$WORKDIR/.venv/bin/python3}"
PYTHONPATH_VALUE="${PYTHONPATH:-/opt/limbic}"

LANGUAGE="${POLYGLOT_WARM_LANGUAGE:-el}"
MAX_LEMMAS="${POLYGLOT_WARM_MAX_LEMMAS:-16}"
SENTENCES_PER_TARGET="${POLYGLOT_WARM_SENTENCES_PER_TARGET:-2}"
TIMEOUT_SECONDS="${POLYGLOT_WARM_TIMEOUT_SECONDS:-1200}"

export PYTHONUNBUFFERED=1

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

run_phase "warm_sentence_cache" timeout "$TIMEOUT_SECONDS" \
  "$VENV" scripts/warm_sentence_cache.py \
  --language "$LANGUAGE" \
  --max-lemmas "$MAX_LEMMAS" \
  --sentences-per-target "$SENTENCES_PER_TARGET"

echo "[$TIMESTAMP] Polyglot material cron done" >> "$LOG"
echo "---" >> "$LOG"
