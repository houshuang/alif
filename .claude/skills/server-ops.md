# Server Operations — Remote Script Execution

How to run Python code on the production server reliably.

## CRITICAL RULES

### 1. ALL SSH commands need `dangerouslyDisableSandbox: true`
SSH is always blocked by the local sandbox. Never try without it — you will waste a turn.

### 2. For any Python > 2 lines: write a local file, scp, run
```bash
# Step 1: Write the script locally (use Write tool to /tmp/claude/myscript.py)
# Step 2: Copy to server and run:
scp /tmp/claude/myscript.py alif:/tmp/ && ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 /tmp/myscript.py"
```

### 3. For simple 1-line queries:
```bash
ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 -c 'from app.database import SessionLocal; db=SessionLocal(); print(db.execute(text(\"SELECT count(*) FROM lemmas\")).scalar())'"
```

### 4. Read model code BEFORE writing queries
Before querying the DB, read `backend/app/models.py` to verify:
- Table names (e.g., `lemmas` not `lemma`)
- Column names (e.g., `knowledge_state` not `state`)
- Import paths (e.g., `from app.database import SessionLocal`)

### 5. Check logs with journalctl
```bash
ssh alif "journalctl -u alif-backend --since '2 hours ago' --no-pager | tail -50"
```

### 6. Run existing scripts instead of reinventing
Check `backend/scripts/` first. Common scripts:
- `analyze_progress.py --days 7` — learning analytics
- `update_material.py --limit 50` — generate sentences
- `log_activity.py EVENT 'summary'` — log an action

Run them with:
```bash
ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 scripts/SCRIPT_NAME.py ARGS"
```

## Quick Reference

| Task | Command |
|------|---------|
| Check logs | `ssh alif "journalctl -u alif-backend --since '2h ago' --no-pager \| tail -50"` |
| Run script | `ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 scripts/NAME.py"` |
| Complex query | Write to `/tmp/claude/`, scp, run |
| Deploy backend | `ssh alif "cd /opt/alif && git pull && cd backend && .venv/bin/pip install -e . --no-deps -q && systemctl restart alif-backend"` |
| Restart frontend | `ssh alif "cd /opt/alif && git pull && systemctl restart alif-expo"` |
| Service status | `ssh alif "systemctl status alif-backend"` |
| DB path | `/opt/alif/backend/data/alif.db` |
| DB backup | `ssh alif "cp /opt/alif/backend/data/alif.db /opt/alif-backups/alif_manual.db"` |

## Common Import Paths
```python
from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge, Root, ReviewLog, SentenceReviewLog
from sqlalchemy import func, text
from datetime import datetime, timedelta
```
