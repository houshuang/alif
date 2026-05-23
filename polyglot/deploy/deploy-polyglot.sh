#!/usr/bin/env bash
# Deploy Polyglot from Git main. No application files are copied over SSH.
#
# Run from anywhere inside the alif repo:
#
#   polyglot/deploy/deploy-polyglot.sh

set -euo pipefail

REMOTE="${POLYGLOT_DEPLOY_REMOTE:-alif}"
REMOTE_DIR="${POLYGLOT_DEPLOY_REMOTE_DIR:-/opt/alif}"
APP_DIR="$REMOTE_DIR/polyglot"
BRANCH="${POLYGLOT_DEPLOY_BRANCH:-main}"
SERVICE="${POLYGLOT_DEPLOY_SERVICE:-polyglot-backend}"
PORT="${POLYGLOT_DEPLOY_PORT:-3002}"
CRON_LINK="${POLYGLOT_CRON_WRAPPER:-/opt/polyglot-update-material.sh}"

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$current_branch" != "$BRANCH" ]; then
  echo "FAIL: deploy must run from local $BRANCH (current: $current_branch)" >&2
  exit 1
fi

if ! git diff --quiet -- polyglot || ! git diff --cached --quiet -- polyglot; then
  echo "FAIL: commit or discard local polyglot changes before deploying" >&2
  git status --short -- polyglot >&2
  exit 1
fi

echo "==> Fetching origin/$BRANCH"
git fetch origin "$BRANCH"

if [ "$(git rev-parse "$BRANCH")" != "$(git rev-parse "origin/$BRANCH")" ]; then
  echo "==> Pushing local $BRANCH to origin/$BRANCH"
  git push origin "$BRANCH"
fi

echo "==> Pulling origin/$BRANCH on $REMOTE"
ssh "$REMOTE" "REMOTE_DIR='$REMOTE_DIR' BRANCH='$BRANCH' bash -s" <<'REMOTE_DEPLOY'
set -euo pipefail
cd "$REMOTE_DIR"
git fetch origin "$BRANCH"
git checkout "$BRANCH"

backup_dir="/tmp/alif-deploy-drift-$(date -u +%Y%m%dT%H%M%SZ)"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  mkdir -p "$backup_dir"
  git diff > "$backup_dir/tracked.diff" || true
  git status --porcelain --untracked-files=no > "$backup_dir/status.txt"
  echo "Backed up tracked drift to $backup_dir"
fi

# Remove untracked files in code-owned paths. This clears old scp-created code
# files without touching runtime state such as .env, .venv, DBs, data, or logs.
while IFS= read -r path; do
  [ -n "$path" ] || continue
  mkdir -p "$backup_dir/untracked/$(dirname "$path")"
  cp -p "$path" "$backup_dir/untracked/$path" 2>/dev/null || true
  rm -f -- "$path"
done < <(
  git ls-files -o --exclude-standard -- \
    polyglot/app \
    polyglot/deploy \
    polyglot/scripts \
    polyglot/tests \
    polyglot/CLAUDE.md \
    polyglot/DESIGN.md \
    polyglot/NEXT_SESSION.md \
    polyglot/README.md
)

git reset --hard "origin/$BRANCH"
REMOTE_DEPLOY

echo "==> Installing Polyglot package"
ssh "$REMOTE" "
  set -e
  cd '$APP_DIR'
  .venv/bin/pip install -e . --no-deps -q
"

echo "==> Linking versioned cron wrapper"
ssh "$REMOTE" "
  set -e
  ln -sfn '$APP_DIR/deploy/polyglot-update-material.sh' '$CRON_LINK'
  chmod +x '$APP_DIR/deploy/polyglot-update-material.sh'
"

echo "==> Restarting $SERVICE"
ssh "$REMOTE" "systemctl restart '$SERVICE'"

echo "==> Health check"
sleep 3
ssh "$REMOTE" "
  set -e
  systemctl is-active --quiet '$SERVICE'
  curl -sf 'http://localhost:$PORT/' >/dev/null
  test \"\$(readlink -f '$CRON_LINK')\" = '$APP_DIR/deploy/polyglot-update-material.sh'
"

echo "==> Done. http://alifstian.duckdns.org:$PORT/"
