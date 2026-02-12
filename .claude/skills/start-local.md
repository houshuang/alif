# Start Local Dev Environment

Start the backend and/or frontend locally for development/testing.

## Backend
```bash
cd backend && python3 -m uvicorn app.main:app --port 8000 --reload
```
- **IMPORTANT**: Use `python3` not `python` (macOS `python` is Python 2.7)
- Runs on http://localhost:8000
- `--reload` watches for file changes
- Verify: `curl http://localhost:8000/api/stats`

## Frontend
```bash
cd frontend && npx expo start --web --port 8081
```
- Opens on http://localhost:8081
- To clear cache: `npx expo start --web --port 8081 --clear`

## Both (sequential)
```bash
cd backend && python3 -m uvicorn app.main:app --port 8000 --reload &
cd frontend && npx expo start --web --port 8081
```

## Common Issues

**Port already in use:**
```bash
lsof -i :8000  # find process using backend port
lsof -i :8081  # find process using frontend port
kill -9 <PID>  # kill it
```

**Expo crashes / stale cache:**
```bash
cd frontend && npx expo start --web --clear
# Or nuclear option:
rm -rf frontend/node_modules/.cache
cd frontend && npm install && npx expo start --web
```

**Backend import errors:**
```bash
cd backend && pip install -e ".[dev]"  # reinstall deps
```

**Watchman errors on macOS:**
Watchman sometimes crashes. Expo works without it (slightly slower reload):
```bash
watchman shutdown-server  # restart watchman
# Or just ignore the warning — Expo falls back to polling
```

## Production (Hetzner)
Backend and frontend run as services on the server:
```bash
ssh alif "docker logs alif-backend-1 --tail 20"     # backend logs
ssh alif "systemctl status alif-expo"                 # frontend status
ssh alif "systemctl restart alif-expo"                # restart frontend
```
