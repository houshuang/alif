# Backup Database

Backup the production database from Hetzner to local machine.

## Steps
1. Run: `./scripts/backup.sh`
2. Or manually: `ssh alif "sqlite3 /opt/alif/backend/data/alif.db \".timeout 30000\" \".backup /opt/alif-backups/alif_manual.db\""` then `scp alif:/opt/alif-backups/alif_manual.db ~/alif-backups/` (never copy the live DB file directly: changes still in its `-wal` file are missed)
3. Server-side backups are at `/opt/alif-backups/` on the server

## Retention Policy (Grandfather-Father-Son)
- **Daily**: keep all backups from last 7 days
- **Weekly**: keep Sunday backups for 4 weeks
- **Monthly**: keep 1st-of-month backups forever
- **Logs**: compressed after 7 days, deleted after 90 days
- Server cron runs every 6 hours: `crontab -l` on server to verify

## Restore
```bash
# Copy backup to server
scp ~/alif-backups/alif_YYYYMMDD_HHMMSS.db alif:/tmp/restore.db
# Stop service, replace DB, restart
ssh alif "systemctl stop alif-backend && cp /tmp/restore.db /opt/alif/backend/data/alif.db && systemctl start alif-backend"
```
