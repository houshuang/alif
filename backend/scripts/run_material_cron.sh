#!/usr/bin/env bash
# Versioned material cron wrapper.
#
# This keeps the frequent cron pass cheap and bounded:
#   1. rotate stale sentences
#   2. run non-generation maintenance from update_material.py
#   3. enqueue small material jobs
#   4. execute a small number of queued jobs

set -u

LOG="${ALIF_MATERIAL_CRON_LOG:-/var/log/alif-update-material.log}"
TIMESTAMP="$(date +"%Y-%m-%d %H:%M:%S")"
WORKDIR="${ALIF_BACKEND_DIR:-/opt/alif/backend}"
VENV="${ALIF_PYTHON:-$WORKDIR/.venv/bin/python3}"
PYTHONPATH_VALUE="${PYTHONPATH:-/opt/limbic}"

SENTENCE_BUDGET="${ALIF_MATERIAL_SENTENCE_BUDGET:-40}"
PLAN_MAX_JOBS="${ALIF_MATERIAL_PLAN_MAX_JOBS:-10}"
SHARD_SIZE="${ALIF_MATERIAL_JOB_SHARD_SIZE:-4}"
WORKER_MAX_JOBS="${ALIF_MATERIAL_WORKER_MAX_JOBS:-3}"
WORKER_TIMEOUT_SECONDS="${ALIF_MATERIAL_WORKER_TIMEOUT_SECONDS:-1200}"
MAINTENANCE_TIMEOUT_SECONDS="${ALIF_MATERIAL_MAINTENANCE_TIMEOUT_SECONDS:-900}"

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

echo "[$TIMESTAMP] Material cron start" >> "$LOG"

run_phase "rotate_stale_sentences.py" "$VENV" scripts/rotate_stale_sentences.py

run_phase "update_material.py maintenance-only" timeout "$MAINTENANCE_TIMEOUT_SECONDS" \
  "$VENV" scripts/update_material.py --limit 50 --skip-audio --max-step-a-sentences 0

run_phase "plan_material_jobs.py" "$VENV" scripts/plan_material_jobs.py \
  --sentence-budget "$SENTENCE_BUDGET" \
  --max-jobs "$PLAN_MAX_JOBS" \
  --shard-size "$SHARD_SIZE"

run_phase "work_material_jobs.py" timeout "$WORKER_TIMEOUT_SECONDS" \
  "$VENV" scripts/work_material_jobs.py --max-jobs "$WORKER_MAX_JOBS"

echo "[$TIMESTAMP] Material cron done" >> "$LOG"
echo "---" >> "$LOG"
