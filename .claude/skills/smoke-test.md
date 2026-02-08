# Smoke Test

Run a full smoke test of the backend API.

## Local
1. Check if backend is running: `curl -s http://localhost:8000/api/stats`
2. If not running: `cd backend && python3 -m uvicorn app.main:app --reload &`
3. Test endpoints:
   - `curl http://localhost:8000/api/stats` — word counts
   - `curl http://localhost:8000/api/words?limit=5` — word list
   - `curl http://localhost:8000/api/review/next-sentences?limit=3` — sentence review session
   - `curl http://localhost:8000/api/grammar/features` — grammar features
   - `curl http://localhost:8000/api/learn/next-words?count=3` — next words to learn

## Production (Hetzner)
1. `ssh alif "curl -sf http://localhost:3000/api/stats"` — verify backend is up
2. `curl -sf http://46.225.75.29:3000/api/stats` — verify external access
3. `ssh alif "docker logs alif-backend-1 --tail 10"` — check for errors
