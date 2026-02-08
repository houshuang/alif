# Backup Database

Backup the production database from Hetzner to local machine.

## Steps
1. Run: `./scripts/backup.sh`
2. Or manually: `ssh alif "docker cp alif-backend-1:/app/data/alif.db /tmp/alif_backup.db" && scp alif:/tmp/alif_backup.db ~/alif-backups/`
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
# Stop container, replace DB, restart
ssh alif "docker stop alif-backend-1 && docker cp /tmp/restore.db alif-backend-1:/app/data/alif.db && docker start alif-backend-1"
```
