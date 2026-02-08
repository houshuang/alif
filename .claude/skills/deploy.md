# Deploy to Hetzner

Deploy backend and/or frontend to the Hetzner production server.

## Backend Only
1. Run tests: `cd backend && python3 -m pytest --tb=short -q`
2. Commit and push to main
3. Deploy: `ssh alif "cd /opt/alif && git pull && docker compose up -d --build"`
4. Wait 5s, verify: `ssh alif "curl -sf http://localhost:3000/api/stats"`
5. If fails: `ssh alif "docker logs alif-backend-1 --tail 30"`

## Frontend Only
1. Push to main
2. `ssh alif "cd /opt/alif && git pull && systemctl restart alif-expo"`

## Full Deploy (both)
```bash
ssh alif "cd /opt/alif && git pull && docker compose up -d --build && systemctl restart alif-expo"
```

## IMPORTANT: Always display Expo URL after deploy
After every deploy, display the stable Expo URL:
```
exp://alifstian.duckdns.org:8081
http://alifstian.duckdns.org:8081
```
This URL is stable (no more changing tunnel URLs).

## Notes
- Backend: http://46.225.75.29:3000 (docker 3000→8000), container `alif-backend-1`
- Frontend: Expo dev server on port 8081, runs as systemd service `alif-expo`
- Stable URL: `alifstian.duckdns.org` → 46.225.75.29 (DuckDNS, static IP)
- SSH alias `alif` configured in ~/.ssh/config
- `.env` at `/opt/alif/.env` on server
- Server-side backups every 6h via cron to `/opt/alif-backups/`
