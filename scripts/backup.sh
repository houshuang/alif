#!/bin/bash
# Backup Alif database from Hetzner to local machine
# Retention: daily for 7 days, weekly for 4 weeks, monthly forever
# Run via cron: 0 */6 * * * /Users/stian/src/alif/scripts/backup.sh

set -e

SERVER="alif"
BACKUP_DIR="$HOME/alif-backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# Copy DB via docker cp
ssh $SERVER "docker cp alif-backend-1:/app/data/alif.db /tmp/alif_backup.db" 2>/dev/null
scp $SERVER:/tmp/alif_backup.db "$BACKUP_DIR/alif_${TIMESTAMP}.db"
ssh $SERVER "rm /tmp/alif_backup.db"

# Grab interaction logs
ssh $SERVER "docker cp alif-backend-1:/app/data/logs /tmp/alif_logs 2>/dev/null" && \
    mkdir -p "$BACKUP_DIR/logs" && \
    scp -r $SERVER:/tmp/alif_logs/* "$BACKUP_DIR/logs/" 2>/dev/null && \
    ssh $SERVER "rm -rf /tmp/alif_logs" 2>/dev/null

# Grandfather-father-son retention
# Keep: all from last 7 days, one per week for 4 weeks, one per month forever
find "$BACKUP_DIR" -name "alif_*.db" -mtime +7 ! -name "alif_*_0[1-7]0000.db" | while read f; do
    DAY=$(basename "$f" | sed 's/alif_\([0-9]*\)_.*/\1/' | cut -c7-8)
    # Keep Sunday backups (weekly) if < 30 days old
    DOW=$(date -j -f "%Y%m%d" "$(basename "$f" | sed 's/alif_\([0-9]*\)_.*/\1/')" "+%u" 2>/dev/null || echo "0")
    DAYS_OLD=$(( ($(date +%s) - $(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0)) / 86400 ))
    if [ "$DAYS_OLD" -gt 30 ]; then
        # Monthly: keep 1st of month only
        [ "$DAY" != "01" ] && rm -f "$f"
    elif [ "$DAYS_OLD" -gt 7 ]; then
        # Weekly: keep Sundays only
        [ "$DOW" != "7" ] && rm -f "$f"
    fi
done

echo "Backup saved: $BACKUP_DIR/alif_${TIMESTAMP}.db ($(du -h "$BACKUP_DIR/alif_${TIMESTAMP}.db" | cut -f1))"
