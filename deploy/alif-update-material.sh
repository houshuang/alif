#!/usr/bin/env bash
# Versioned material cron wrapper.
#
# Installed at /opt/alif-update-material.sh on the Hetzner VM as a symlink to
# /opt/alif/deploy/alif-update-material.sh by scripts/deploy.sh; invoked every
# 3 hours via the system crontab:
#
#     30 */3 * * * /opt/alif-update-material.sh >> /var/log/alif-update-material.log 2>&1
#
# Wraps four passes per run, in order:
#   1. rotate stale sentences
#   2. update_material.py in maintenance-only mode (no Step A generation)
#   3. plan small material jobs
#   4. execute a bounded number of queued jobs
#
# Update procedure: commit to main, then run scripts/deploy.sh.
# Then verify with a dry-run pass:
#   ssh alif /opt/alif-update-material.sh
#
# Intro supply chain — IMPORTANT
# ------------------------------
# After the 2026-05-12 cost consolidation, several update_material.py steps
# became opt-in via env vars (default off in code) to prevent runaway Claude
# spend. The cron is the one place we DO want them on, because that's the
# only path that refills the high-frequency lemma pool. Without these flags,
# `frequency_core_intake` doesn't run and intros drop to a trickle once
# top-1000 is exhausted (the bug diagnosed 2026-05-13 — see
# research/experiment-log.md). Override per-env via systemd or shell exports
# before invocation if you ever need to dial back.

set -u

LOG="${ALIF_MATERIAL_CRON_LOG:-/var/log/alif-update-material.log}"
TIMESTAMP="$(date +"%Y-%m-%d %H:%M:%S")"
WORKDIR="${ALIF_BACKEND_DIR:-/opt/alif/backend}"
VENV="${ALIF_PYTHON:-$WORKDIR/.venv/bin/python3}"
PYTHONPATH_VALUE="${PYTHONPATH:-/opt/limbic}"

SENTENCE_BUDGET="${ALIF_MATERIAL_SENTENCE_BUDGET:-40}"
PLAN_MAX_JOBS="${ALIF_MATERIAL_PLAN_MAX_JOBS:-10}"
SHARD_SIZE="${ALIF_MATERIAL_JOB_SHARD_SIZE:-2}"
WORKER_MAX_JOBS="${ALIF_MATERIAL_WORKER_MAX_JOBS:-3}"
WORKER_TIMEOUT_SECONDS="${ALIF_MATERIAL_WORKER_TIMEOUT_SECONDS:-1200}"
MAINTENANCE_TIMEOUT_SECONDS="${ALIF_MATERIAL_MAINTENANCE_TIMEOUT_SECONDS:-900}"
WORKER_USE_LEGACY_BATCH="${ALIF_MATERIAL_USE_LEGACY_BATCH:-1}"

# Intro supply chain — keep on by default in the cron context. The values are
# still overridable from the environment (systemd Environment= or shell export
# before the cron line). See the header note above for the back-story.
export ALIF_RUN_CRON_PREGENERATION="${ALIF_RUN_CRON_PREGENERATION:-1}"
export ALIF_RUN_CRON_LEMMA_ENRICHMENT="${ALIF_RUN_CRON_LEMMA_ENRICHMENT:-1}"
export ALIF_FREQ_CORE_INTAKE_MAX_RANK="${ALIF_FREQ_CORE_INTAKE_MAX_RANK:-3000}"
export ALIF_FREQ_CORE_INTAKE_LIMIT="${ALIF_FREQ_CORE_INTAKE_LIMIT:-10}"

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

run_phase "work_material_jobs.py" env ALIF_MATERIAL_USE_LEGACY_BATCH="$WORKER_USE_LEGACY_BATCH" \
  timeout "$WORKER_TIMEOUT_SECONDS" \
  "$VENV" scripts/work_material_jobs.py --max-jobs "$WORKER_MAX_JOBS"

echo "[$TIMESTAMP] Material cron done" >> "$LOG"
echo "---" >> "$LOG"
