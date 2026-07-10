#!/bin/bash
# Pull verified Alif and Koigen durable-state backups from the production host.
#
# Alif retention: all daily backups for 7 days, Sundays through day 30, then
# first-of-month backups indefinitely. Koigen retention: 35 days while always
# keeping at least 7 verified snapshots.
#
# Run by ~/Library/LaunchAgents/com.alif.backup.plist at 09:00 local time. That is
# after Koigen's 02:00 UTC nightly, including during Norwegian daylight saving.

set -euo pipefail
umask 077

# launchd has a deliberately small default PATH and does not see Homebrew/local
# Python. Keep system tools first where possible, but include both common Python
# locations for Koigen's verifier.
PATH="${ALIF_BACKUP_PATH:-/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin}"
export PATH

SERVER="${ALIF_BACKUP_SERVER:-alif}"
BACKUP_DIR="${ALIF_BACKUP_DIR:-$HOME/alif-backups}"
TIMESTAMP="${ALIF_BACKUP_TIMESTAMP:-$(date +%Y%m%d_%H%M%S)}"

ALIF_REMOTE_DB="${ALIF_REMOTE_DB:-/opt/alif/backend/data/alif.db}"
ALIF_REMOTE_LOG_DIR="${ALIF_REMOTE_LOG_DIR:-/opt/alif/backend/data/logs}"

KOIGEN_REMOTE_ROOT="${KOIGEN_REMOTE_ROOT:-/opt/hvaskjer}"
KOIGEN_REMOTE_BACKUP_DIR="${KOIGEN_REMOTE_BACKUP_DIR:-/var/backups/koigen}"
KOIGEN_LOCAL_BACKUP_DIR="${KOIGEN_LOCAL_BACKUP_DIR:-$BACKUP_DIR/koigen}"
KOIGEN_VERIFY_TOOL="${KOIGEN_VERIFY_TOOL:-$HOME/src/hvaskjer/ingest/durable_backup.py}"
KOIGEN_BACKUP_RETENTION_DAYS="${KOIGEN_BACKUP_RETENTION_DAYS:-35}"
KOIGEN_BACKUP_MIN_SNAPSHOTS="${KOIGEN_BACKUP_MIN_SNAPSHOTS:-7}"
KOIGEN_BACKUP_MAX_AGE_HOURS="${KOIGEN_BACKUP_MAX_AGE_HOURS:-48}"

die() {
    echo "backup: $*" >&2
    return 1
}

require_safe_server() {
    case "$SERVER" in
        ""|*[!A-Za-z0-9_.@-]*) die "unsafe SSH server alias" ;;
    esac
}

require_safe_absolute_remote_path() {
    local value="$1"
    case "$value" in
        /*) ;;
        *) die "remote path must be absolute: $value" ;;
    esac
    case "$value" in
        *[!A-Za-z0-9_./-]*) die "unsafe remote path: $value" ;;
    esac
}

require_positive_integer() {
    local label="$1" value="$2" minimum="$3" maximum="$4"
    case "$value" in
        ""|*[!0-9]*) die "$label must be an integer" ;;
    esac
    if [ "$value" -lt "$minimum" ] || [ "$value" -gt "$maximum" ]; then
        die "$label must be between $minimum and $maximum"
    fi
}

fsync_paths() {
    python3 - "$@" <<'PY'
import os
import sys
from pathlib import Path

for raw in sys.argv[1:]:
    path = Path(raw)
    if path.is_dir():
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    else:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
PY
}

prepare_local_directories() {
    if [ -L "$BACKUP_DIR" ] || { [ -e "$BACKUP_DIR" ] && [ ! -d "$BACKUP_DIR" ]; }; then
        die "backup destination must be a real directory: $BACKUP_DIR"
    fi
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"

    if [ -L "$KOIGEN_LOCAL_BACKUP_DIR" ] \
            || { [ -e "$KOIGEN_LOCAL_BACKUP_DIR" ] && [ ! -d "$KOIGEN_LOCAL_BACKUP_DIR" ]; }; then
        die "Koigen backup destination must be a real directory: $KOIGEN_LOCAL_BACKUP_DIR"
    fi
    mkdir -p "$KOIGEN_LOCAL_BACKUP_DIR"
    chmod 700 "$KOIGEN_LOCAL_BACKUP_DIR"
}

snapshot_alif_database() {
    local final="$BACKUP_DIR/alif_${TIMESTAMP}.db"
    local staging="$BACKUP_DIR/.alif_${TIMESTAMP}.$$.tmp"
    local remote_tmp="/tmp/alif-backup-${TIMESTAMP}-$$.db"
    local integrity

    if [ -e "$final" ] || [ -L "$final" ]; then
        die "refusing to replace an existing Alif backup: $final"
    fi

    # SQLite's online backup API folds live WAL state into one coherent database.
    # Stream that private temporary database over SSH, then expose the local name
    # only after a full integrity check. The remote EXIT trap removes its temp file.
    if ! ssh -o BatchMode=yes -o ConnectTimeout=30 "$SERVER" \
        "set -eu; umask 077; tmp='$remote_tmp'; trap 'rm -f \"\$tmp\"' EXIT HUP INT TERM; rm -f \"\$tmp\"; sqlite3 '$ALIF_REMOTE_DB' '.timeout 30000' \".backup '\$tmp'\"; cat \"\$tmp\"" \
        >"$staging"; then
        rm -f "$staging"
        die "Alif SQLite snapshot/transfer failed"
    fi

    integrity=$(sqlite3 "$staging" 'PRAGMA integrity_check;') || {
        rm -f "$staging"
        die "could not verify transferred Alif database"
    }
    if [ "$integrity" != "ok" ]; then
        rm -f "$staging"
        die "transferred Alif database failed integrity_check"
    fi
    chmod 600 "$staging"
    fsync_paths "$staging"
    mv "$staging" "$final"
    fsync_paths "$BACKUP_DIR"
    echo "Alif backup saved: $final ($(du -h "$final" | cut -f1))"
}

verify_koigen_age() {
    local snapshot="$1"
    python3 - "$KOIGEN_VERIFY_TOOL" "$snapshot" "$KOIGEN_BACKUP_MAX_AGE_HOURS" <<'PY'
import datetime as dt
import json
import sys
from pathlib import Path

tool, snapshot, max_age_hours = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
expected = {"manifest.json", "state.tar.gz"}
actual = {path.name for path in snapshot.iterdir()}
if actual != expected or any(
    path.is_symlink() or not path.is_file() for path in snapshot.iterdir()
):
    raise SystemExit("Koigen snapshot directory contains unexpected or unsafe entries")
sys.path.insert(0, str(tool.parent))
import durable_backup  # noqa: E402

result = durable_backup.verify_snapshot(snapshot)
created = dt.datetime.fromisoformat(result["created_at"].replace("Z", "+00:00"))
now = dt.datetime.now(dt.timezone.utc)
age = now - created.astimezone(dt.timezone.utc)
if age > dt.timedelta(hours=max_age_hours):
    raise SystemExit(
        f"Koigen snapshot {result['snapshot_id']} is stale: "
        f"{age.total_seconds() / 3600:.1f}h > {max_age_hours}h"
    )
print(json.dumps(result, separators=(",", ":")))
PY
}

prune_local_koigen_snapshots() {
    local keep="$1"
    python3 - "$KOIGEN_VERIFY_TOOL" "$KOIGEN_LOCAL_BACKUP_DIR" \
        "$KOIGEN_BACKUP_RETENTION_DAYS" "$KOIGEN_BACKUP_MIN_SNAPSHOTS" "$keep" <<'PY'
import json
import sys
from pathlib import Path

tool = Path(sys.argv[1])
destination = Path(sys.argv[2])
retention_days = int(sys.argv[3])
minimum_snapshots = int(sys.argv[4])
keep = Path(sys.argv[5])
sys.path.insert(0, str(tool.parent))
import durable_backup  # noqa: E402

# The Koigen implementation deletes only recognized snapshots which verify in full.
# Unknown, partial, corrupt, or symlinked directories are deliberately retained.
removed = durable_backup.prune_snapshots(
    destination,
    retention_days=retention_days,
    minimum_snapshots=minimum_snapshots,
    keep=keep,
)
print(json.dumps({"pruned": len(removed)}, separators=(",", ":")))
PY
}

pull_koigen_snapshot() {
    local snapshot_name remote_snapshot final staging_parent staging

    if [ ! -f "$KOIGEN_VERIFY_TOOL" ] || [ -L "$KOIGEN_VERIFY_TOOL" ]; then
        die "Koigen verifier is missing or unsafe: $KOIGEN_VERIFY_TOOL"
    fi

    # Fail on the newest candidate if it is corrupt; silently falling back to an
    # older bundle would make a broken nightly look healthy. The verifier prints no
    # captured content, and the selected directory name contains no private data.
    snapshot_name=$(ssh -o BatchMode=yes -o ConnectTimeout=30 "$SERVER" \
        "set -eu; latest=\$(find '$KOIGEN_REMOTE_BACKUP_DIR' -mindepth 1 -maxdepth 1 -type d -name 'koigen-durable-v1-*' -print | LC_ALL=C sort | tail -n 1); [ -n \"\$latest\" ] || { echo 'no Koigen durable snapshot found' >&2; exit 20; }; python3 '$KOIGEN_REMOTE_ROOT/ingest/durable_backup.py' verify \"\$latest\" >/dev/null; basename \"\$latest\"") || {
        die "could not select and verify the newest remote Koigen snapshot"
    }
    if [[ ! "$snapshot_name" =~ ^koigen-durable-v1-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]]; then
        die "remote returned an invalid Koigen snapshot name"
    fi

    remote_snapshot="$KOIGEN_REMOTE_BACKUP_DIR/$snapshot_name"
    final="$KOIGEN_LOCAL_BACKUP_DIR/$snapshot_name"

    # An already-pulled snapshot is still checked for corruption and freshness on
    # every run. Never overwrite a same-ID directory: preserve unexpected evidence.
    if [ -e "$final" ] || [ -L "$final" ]; then
        verify_koigen_age "$final" >/dev/null || return 1
        prune_local_koigen_snapshots "$final" >/dev/null
        echo "Koigen backup already current: $snapshot_name"
        return 0
    fi

    staging_parent="$KOIGEN_LOCAL_BACKUP_DIR/.incoming-${TIMESTAMP}-$$"
    staging="$staging_parent/$snapshot_name"
    if [ -e "$staging_parent" ] || [ -L "$staging_parent" ]; then
        die "Koigen staging path already exists: $staging_parent"
    fi
    mkdir -p "$staging"
    chmod 700 "$staging_parent" "$staging"

    if ! rsync -rlt --include='manifest.json' --include='state.tar.gz' --exclude='*' \
            -- "$SERVER:$remote_snapshot/" "$staging/"; then
        rm -rf "$staging_parent"
        die "Koigen snapshot transfer failed"
    fi
    chmod 700 "$staging"
    [ -f "$staging/manifest.json" ] && chmod 600 "$staging/manifest.json"
    [ -f "$staging/state.tar.gz" ] && chmod 600 "$staging/state.tar.gz"

    # Full local verification includes the bundle hash, member allowlist and hashes,
    # JSON shapes, all three SQLite schemas, and PRAGMA integrity_check. Freshness is
    # checked before the atomic rename so a stale transfer is never published locally.
    if ! verify_koigen_age "$staging" >/dev/null; then
        rm -rf "$staging_parent"
        die "transferred Koigen snapshot failed verification or freshness"
    fi
    fsync_paths "$staging/manifest.json" "$staging/state.tar.gz" \
        "$staging" "$staging_parent"
    mv "$staging" "$final"
    fsync_paths "$final" "$staging_parent" "$KOIGEN_LOCAL_BACKUP_DIR"
    rmdir "$staging_parent"
    fsync_paths "$KOIGEN_LOCAL_BACKUP_DIR"

    # Retention is intentionally last and reuses Koigen's fail-safe pruning policy.
    prune_local_koigen_snapshots "$final" >/dev/null
    echo "Koigen backup saved: $final ($(du -h "$final/state.tar.gz" | cut -f1))"
}

prune_alif_backups() {
    python3 - "$BACKUP_DIR" <<'PY'
import datetime as dt
import os
import re
import sys
from pathlib import Path

destination = Path(sys.argv[1])
pattern = re.compile(r"^alif_(\d{8})_(\d{6})\.db$")
today = dt.datetime.now().date()
removed = 0
for path in destination.iterdir():
    if path.is_symlink() or not path.is_file():
        continue
    match = pattern.fullmatch(path.name)
    if not match:
        continue
    try:
        created = dt.datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    except ValueError:
        continue
    age_days = (today - created.date()).days
    if age_days <= 7 or age_days < 0:
        continue
    if age_days <= 30 and created.weekday() == 6:  # Sunday
        continue
    if age_days > 30 and created.day == 1:
        continue
    path.unlink()
    removed += 1
if removed:
    descriptor = os.open(destination, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
print(f"Alif retention pruned: {removed}")
PY
}

sync_optional_alif_logs() {
    mkdir -p "$BACKUP_DIR/logs"
    chmod 700 "$BACKUP_DIR/logs"
    # Logs are useful but not backup authority; a log transfer failure must not
    # discard verified database/snapshot progress.
    if ! rsync -az -- "$SERVER:$ALIF_REMOTE_LOG_DIR/" "$BACKUP_DIR/logs/"; then
        echo "backup: optional Alif log sync failed" >&2
    fi
}

main() {
    require_safe_server
    require_safe_absolute_remote_path "$ALIF_REMOTE_DB"
    require_safe_absolute_remote_path "$ALIF_REMOTE_LOG_DIR"
    require_safe_absolute_remote_path "$KOIGEN_REMOTE_ROOT"
    require_safe_absolute_remote_path "$KOIGEN_REMOTE_BACKUP_DIR"
    require_positive_integer "KOIGEN_BACKUP_RETENTION_DAYS" "$KOIGEN_BACKUP_RETENTION_DAYS" 1 3650
    require_positive_integer "KOIGEN_BACKUP_MIN_SNAPSHOTS" "$KOIGEN_BACKUP_MIN_SNAPSHOTS" 1 365
    require_positive_integer "KOIGEN_BACKUP_MAX_AGE_HOURS" "$KOIGEN_BACKUP_MAX_AGE_HOURS" 1 720

    prepare_local_directories
    snapshot_alif_database
    pull_koigen_snapshot
    prune_alif_backups
    sync_optional_alif_logs
}

if [ "${ALIF_BACKUP_SOURCE_ONLY:-0}" != "1" ]; then
    main "$@"
fi
