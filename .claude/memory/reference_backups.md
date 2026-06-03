---
name: reference_backups
description: Alif/Polyglot backup, restore, and retention — server cron + local script + GFS policy
metadata:
  type: reference
---

Backup and restore for the Hetzner-hosted Alif DB (`/opt/alif/backend/data/alif.db`).

- **Server-side**: cron every 6h, script at `/opt/alif-backup.sh`, backups in `/opt/alif-backups/`.
- **Local**: `./scripts/backup.sh` pulls DB + logs to `~/alif-backups/`.
- **Retention (GFS)**: daily 7 days, weekly (Sundays) 4 weeks, monthly (1st) forever.
- **Log rotation**: compress after 7 days, delete after 90 days.
- **Restore**: `scp backup.db alif:/tmp/ && ssh alif "cp /tmp/backup.db /opt/alif/backend/data/alif.db && systemctl restart alif-backend"`.
- **Before manual data changes**, always back up first: `ssh alif "cp /opt/alif/backend/data/alif.db /opt/alif-backups/alif_pre_fix_$(date +%Y%m%d_%H%M%S).db"` then log the action via `scripts/log_activity.py` (see [[reference_activity_logging]]).
