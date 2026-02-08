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
3. Get new tunnel URL: `ssh alif /opt/alif/expo-url.sh`

## Full Deploy (both)
```bash
ssh alif "cd /opt/alif && git pull && docker compose up -d --build && systemctl restart alif-expo"
```

## Notes
- Backend: http://46.225.75.29:3000 (docker 3000→8000), container `alif-backend-1`
- Frontend: Expo dev server with tunnel, runs as systemd service `alif-expo`
- Tunnel URL changes on restart — always check with `expo-url.sh`
- SSH alias `alif` configured in ~/.ssh/config
- `.env` at `/opt/alif/.env` on server
- Server-side backups every 6h via cron to `/opt/alif-backups/`
