# Deploy to Hetzner

Deploy backend and/or frontend to the Hetzner production server.

## ⚠ BEFORE ANY DEPLOY — Push First!
**This has failed 5+ times.** The server does `git pull` — if you haven't pushed, it pulls stale code and nothing changes. Always:
1. `git push` (verify "To github.com..." output, not "Everything up-to-date" when you have local commits)
2. Then SSH to deploy

## Backend Only
1. Run tests: `cd backend && python3 -m pytest --tb=short -q`
2. **Commit and `git push` to main** — verify push succeeded
3. Deploy: `ssh alif "cd /opt/alif && git pull && cd backend && .venv/bin/pip install -e . --no-deps -q && systemctl restart alif-backend"`
4. Wait 5s, verify: `ssh alif "curl -sf http://localhost:3000/api/stats"`
5. If fails: `ssh alif 'journalctl -u alif-backend --no-pager -n 30'`

**If pyproject.toml dependencies changed**, use the full install (not `--no-deps`):
```bash
ssh alif "cd /opt/alif && git pull && cd backend && .venv/bin/pip install -e /opt/limbic && .venv/bin/pip install -e . -q && systemctl restart alif-backend"
```
Note: limbic must be installed from `/opt/limbic` first — `pyproject.toml`'s `git+https://` URL fails on the server.

## Frontend Only
1. **`git push` to main** — verify push succeeded
2. `ssh alif "cd /opt/alif && git pull && cd frontend && npm install && systemctl restart alif-expo"`

## Full Deploy (both)
**IMPORTANT**: Always include `npm install` — new frontend dependencies are NOT automatically picked up on the server. This has caused repeated deploy failures.
```bash
ssh alif "cd /opt/alif && git pull && cd backend && .venv/bin/pip install -e . --quiet && systemctl restart alif-backend && cd ../frontend && npm install && systemctl restart alif-expo"
```

## IMPORTANT: Always display Expo URL after deploy
After every deploy, display the stable Expo URL:
```
exp://alifstian.duckdns.org:8081
http://alifstian.duckdns.org:8081
```
This URL is stable (no more changing tunnel URLs).

## Post-Deploy Checklist
After every deploy, also do:
1. Update CLAUDE.md if you changed: data model, services, endpoints, scripts, architecture
2. Update `research/experiment-log.md` if you changed: algorithms, data sources, scheduling
3. Update IDEAS.md with any new ideas from the session
4. Do NOT wait to be asked — this is a critical step

## Notes
- Backend: http://46.225.75.29:3000 (binds directly to 0.0.0.0:3000 via systemd ExecStart), service `alif-backend.service`
- Frontend: Expo dev server on port 8081, runs as systemd service `alif-expo`
- Stable URL: `alifstian.duckdns.org` → 46.225.75.29 (DuckDNS, static IP)
- SSH alias `alif` configured in ~/.ssh/config
- `.env` at `/opt/alif/.env` on server
- Server-side backups every 6h via cron to `/opt/alif-backups/`
