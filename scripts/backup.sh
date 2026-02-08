#!/bin/bash
# Backup Alif database from Hetzner to local machine
# Run manually or via cron: 0 */6 * * * /Users/stian/src/alif/scripts/backup.sh

set -e

SERVER="alif"
BACKUP_DIR="$HOME/alif-backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# Get volume mountpoint and copy the SQLite DB via docker cp
ssh $SERVER "docker cp alif-backend-1:/app/data/alif.db /tmp/alif_backup.db" 2>/dev/null
scp $SERVER:/tmp/alif_backup.db "$BACKUP_DIR/alif_${TIMESTAMP}.db"
ssh $SERVER "rm /tmp/alif_backup.db"

# Also grab interaction logs
scp -r $SERVER:/tmp/alif_logs/ "$BACKUP_DIR/logs_${TIMESTAMP}/" 2>/dev/null || \
    ssh $SERVER "docker cp alif-backend-1:/app/data/logs /tmp/alif_logs" && \
    scp -r $SERVER:/tmp/alif_logs/ "$BACKUP_DIR/logs_${TIMESTAMP}/" 2>/dev/null && \
    ssh $SERVER "rm -rf /tmp/alif_logs" 2>/dev/null

# Keep last 30 backups
ls -t "$BACKUP_DIR"/alif_*.db 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null

echo "Backup saved: $BACKUP_DIR/alif_${TIMESTAMP}.db"
ls -lh "$BACKUP_DIR/alif_${TIMESTAMP}.db"
