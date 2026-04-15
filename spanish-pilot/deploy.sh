#!/usr/bin/env bash
# Deploy Spanish Pilot to Hetzner.
# Prereq: server has /opt/spanish-pilot/ with git clone + systemd unit installed.
# Run locally: ./deploy.sh

set -euo pipefail

REMOTE="alif"
REMOTE_DIR="/opt/alif-pilot"
PILOT_DIR="$REMOTE_DIR/spanish-pilot"

echo "==> Pushing latest code"
git push origin HEAD

echo "==> Pulling on server"
ssh "$REMOTE" "cd $REMOTE_DIR && git fetch origin && git checkout sh/spanish-pilot && git pull origin sh/spanish-pilot"

echo "==> Installing deps"
ssh "$REMOTE" "cd $PILOT_DIR && .venv/bin/pip install -r backend/requirements.txt -q"

echo "==> Restarting service"
ssh "$REMOTE" "systemctl restart alif-spanish-pilot"

echo "==> Health check"
sleep 2
ssh "$REMOTE" "curl -sf http://localhost:3100/healthz" && echo " OK"

echo "==> Done. http://alifstian.duckdns.org:3100/"
