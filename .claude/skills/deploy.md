# Deploy to Hetzner

Deploy the backend to the Hetzner production server.

## Steps
1. Run tests: `cd backend && python3 -m pytest --tb=short -q`
2. Commit and push to main (if uncommitted changes exist)
3. Deploy: `ssh alif "cd /opt/alif && git pull && docker compose up -d --build"`
4. Wait 5 seconds, then verify: `ssh alif "curl -sf http://localhost:3000/api/stats"`
5. If startup fails, check logs: `ssh alif "docker logs alif-backend-1 --tail 30"`
6. Run a backup after successful deploy: `ssh alif "/opt/alif-backup.sh"`

## Notes
- Backend runs at http://46.225.75.29:3000 (docker maps 3000→8000)
- SSH alias `alif` is configured in ~/.ssh/config
- `.env` with API keys lives at `/opt/alif/.env` on the server
- Server-side backups run every 6h via cron to `/opt/alif-backups/`
- No Coolify — direct docker-compose deployment
