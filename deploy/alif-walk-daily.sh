#!/usr/bin/env bash
# Daily walk episode.
#
# Installed at /opt/alif-walk-daily.sh as a symlink to this file by
# scripts/deploy.sh; invoked once a day via the system crontab:
#
#     0 4 * * * /opt/alif-walk-daily.sh >> /var/log/alif-walk-daily.log 2>&1
#
# The script decides by itself whether to make an episode: one per day, and
# none after four episodes in a row without a completion or feedback. Speech
# generation is switched on for this process only.

set -u

WORKDIR="${ALIF_BACKEND_DIR:-/opt/alif/backend}"
VENV="${ALIF_PYTHON:-$WORKDIR/.venv/bin/python3}"
TIMEOUT_SECONDS="${ALIF_WALK_DAILY_TIMEOUT_SECONDS:-3600}"

export PYTHONUNBUFFERED=1
export ALIF_AUDIO_ENABLED=1

echo "[$(date +"%Y-%m-%d %H:%M:%S")] walk daily start"
cd "$WORKDIR" &&
  PYTHONPATH="${PYTHONPATH:-/opt/limbic}" timeout "$TIMEOUT_SECONDS" \
    "$VENV" scripts/generate_walk_episode.py daily 2>&1 | grep -v "HTTP Request: POST"
echo "[$(date +"%Y-%m-%d %H:%M:%S")] walk daily exit ${PIPESTATUS[0]}"
