# Server Operations — Remote Script Execution

How to run Python code on the production server reliably. This skill prevents the #1 source of wasted time: inline Python via `docker exec` with broken quoting.

## CRITICAL RULES

### 1. ALL SSH commands need `dangerouslyDisableSandbox: true`
SSH is always blocked by the local sandbox. Never try without it — you will waste a turn.

### 2. NEVER write inline Python in `docker exec python3 -c`
Inline Python inside `ssh alif "docker exec ... python3 -c \"...\""` has triple-nested quoting (local shell → SSH → docker → Python). It fails ~50% of the time and wastes turns on syntax retries.

### 3. For any Python > 2 lines: write a local file, scp, docker cp, run
```bash
# Step 1: Write the script locally (use Write tool to /tmp/claude/myscript.py)
# Step 2: Copy to server and run (ONE command):
scp /tmp/claude/myscript.py alif:/tmp/ && ssh alif 'docker cp /tmp/myscript.py alif-backend-1:/tmp/ && docker exec -w /app alif-backend-1 python3 /tmp/myscript.py'
```
This ALWAYS works. No quoting issues.

### 4. For simple 1-line queries: use single-quoted outer shell
```bash
ssh alif 'docker exec alif-backend-1 python3 -c "from app.database import SessionLocal; db=SessionLocal(); print(db.execute(text(\"SELECT count(*) FROM lemmas\")).scalar())"'
```
Outer single quotes, inner double quotes. For anything more complex, use rule #3.

### 5. Read model code BEFORE writing queries
Before querying the DB, read `backend/app/models.py` to verify:
- Table names (e.g., `lemmas` not `lemma`)
- Column names (e.g., `knowledge_state` not `state`)
- Import paths (e.g., `from app.database import SessionLocal`)

### 6. Check docker logs with simple grep — no Python needed
```bash
ssh alif "docker logs alif-backend-1 --since 2h 2>&1 | grep -i 'pattern' | tail -30"
ssh alif "docker logs alif-backend-1 --tail 50 2>&1"
```

### 7. Run existing scripts instead of reinventing
Check `backend/scripts/` first. Common scripts:
- `analyze_progress.py --days 7` — learning analytics
- `update_material.py --limit 50` — generate sentences
- `log_activity.py EVENT 'summary'` — log an action
- `fix_book_glosses.py` — fix book import issues

Run them with:
```bash
ssh alif 'docker exec alif-backend-1 python3 scripts/SCRIPT_NAME.py ARGS 2>&1'
```

## Quick Reference

| Task | Command |
|------|---------|
| Check logs | `ssh alif "docker logs alif-backend-1 --since 2h 2>&1 \| tail -50"` |
| Run script | `ssh alif 'docker exec alif-backend-1 python3 scripts/NAME.py'` |
| Complex query | Write to `/tmp/claude/`, scp+docker cp, run |
| Deploy backend | `ssh alif "cd /opt/alif && git pull && docker compose up -d --build"` |
| Restart frontend | `ssh alif "cd /opt/alif && git pull && systemctl restart alif-expo"` |
| Container status | `ssh alif "docker ps"` |
| DB backup | `ssh alif "docker exec alif-backend-1 cp /app/data/alif.db /app/data/alif.db.bak"` |

## Common Import Paths (inside container)
```python
from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge, Root, ReviewLog, SentenceReviewLog
from sqlalchemy import func, text
from datetime import datetime, timedelta
```
