# Smoke Test

Run a full smoke test of the backend API.

## Steps
1. Check if backend is running: `curl -s http://localhost:8000/docs`
2. If not running, start it: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload &`
3. Wait for startup, then test endpoints:
   - `curl http://localhost:8000/api/words` — should return word list
   - `curl http://localhost:8000/api/review/next` — should return due cards
   - `curl -X POST http://localhost:8000/api/analyze/word -H 'Content-Type: application/json' -d '{"word": "كتاب"}'` — should return morphological analysis
4. Report status of each endpoint
