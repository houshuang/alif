# Setting Up Alif for Yourself

This project is a personal Arabic learning app. It was built for one user's workflow, so there are hardcoded references to a specific server, domain, and deployment setup throughout the codebase (CLAUDE.md, skills files, scripts, app.json). **Don't try to scrub these** — instead, replace them with your own values.

## What You Need

### API Keys (at least one LLM key required)

| Key | Service | Required? | Used for |
|-----|---------|-----------|----------|
| `GEMINI_KEY` | Google AI Studio | Recommended (primary LLM) | Sentence generation, grammar tagging, variant detection, OCR |
| `OPENAI_KEY` | OpenAI | Optional (fallback) | LLM fallback, flag evaluation |
| `ANTHROPIC_API_KEY` | Anthropic | Optional (tertiary) | LLM fallback |
| `ELEVENLABS_API_KEY` | ElevenLabs | Optional | Text-to-speech audio |

The app uses LiteLLM with automatic fallback: Gemini → OpenAI → Anthropic. You can run with just one key — set at least `GEMINI_KEY` or `OPENAI_KEY`.

Without `ELEVENLABS_API_KEY`, TTS/listening mode won't work but everything else will.

### Infrastructure

- A Linux server (or just run locally with docker-compose)
- Docker + docker-compose
- Node.js 20+ (for the Expo frontend dev server)

## Quick Start (Local Development)

```bash
# Backend
cd backend
cp .env.example .env    # fill in your API keys
pip install -e ".[dev]"
python scripts/import_duolingo.py   # seed with starter vocabulary
uvicorn app.main:app --port 8000

# Frontend
cd frontend
npm install
npx expo start --web
```

The frontend connects to the backend via the `apiUrl` in `frontend/app.json`. For local dev it should be `http://localhost:8000` (or `http://localhost:3000` if using docker-compose).

## Quick Start (Docker)

```bash
cp backend/.env.example .env    # fill in your API keys
docker compose up -d --build
# Backend is now at http://localhost:3000
```

## Files You Must Personalize

Ask Claude Code to help you with this. Here's what needs changing:

### 1. `frontend/app.json` — API URL
```json
"extra": {
  "apiUrl": "http://<YOUR_SERVER_IP>:3000"
}
```
For local development, use `http://localhost:8000` (bare python) or `http://localhost:3000` (docker-compose).

### 2. `.env` — API Keys
Copy `backend/.env.example` to `.env` in the project root (docker-compose reads it from here) and/or `backend/.env` (for bare python). Fill in your keys.

### 3. `CLAUDE.md` — Deployment Details
The Hosting section, Hetzner Server section, Expo Dev Server section, and Deployment section all reference the original author's server IP, DuckDNS domain, SSH alias, and systemd service. Update these to match your own setup, or remove them if you're just running locally.

### 4. `.claude/skills/deploy.md` — Deployment Skill
Contains hardcoded server references. Rewrite for your deployment target, or delete if not deploying to a remote server.

### 5. `.claude/skills/backup.md` — Backup Skill
References the original SSH alias and container names. Update or delete.

### 6. `.claude/skills/smoke-test.md` — Smoke Test
Has the original server IP in the production section. Update or remove the production commands.

### 7. `scripts/deploy.sh` — Deploy Script
Hardcoded DuckDNS domain and SSH alias. Update `SERVER`, `EXPO_URL`, and the final echo lines.

### 8. `scripts/backup.sh` — Backup Script
SSH alias `alif` and local paths. Update `SERVER` variable.

## Adapting for a Different Language

The app is built for Modern Standard Arabic (MSA/fusha) and has Arabic-specific NLP deeply integrated. Here's what's language-specific:

### Deeply Arabic-specific (hard to swap)
- **Clitic stripping** (`sentence_validator.py`): Arabic proclitics/enclitics (و، ف، ب، ل + pronouns). Would need complete replacement for another language.
- **CAMeL Tools morphology** (`morphology.py`): Arabic-only morphological analyzer. The `camel-tools` dependency is ~660MB and only handles Arabic.
- **Root system** (`roots` table, root extraction): The tri-consonantal root system is unique to Semitic languages.
- **Function words** (`FUNCTION_WORDS` in `sentence_validator.py`): 60+ hardcoded Arabic function words.
- **Diacritic handling**: Arabic diacritic stripping, tashkeel normalization, hamza/alef normalization.
- **RTL text rendering**: Throughout the frontend.

### Removable/replaceable
- **TTS voice**: Change the ElevenLabs voice ID in `backend/app/services/tts.py`. The learner-pause logic (inserting Arabic commas) would need adjustment.
- **LLM prompts**: All in English, instruct the LLM to produce Arabic. These are in `sentence_generator.py`, `story_service.py`, `grammar_tagger.py`, etc.
- **Import scripts**: `import_duolingo.py`, `import_wiktionary.py`, `import_avp_a1.py` are Arabic vocabulary sources.
- **Transliteration**: ALA-LC romanization, Arabic-specific.

### If you want to disable CAMeL Tools (saves 660MB Docker image size)
Remove `camel-tools>=1.5.0` from `backend/pyproject.toml` and the `camel_data` download line from `backend/Dockerfile`. The code already has graceful fallback stubs — morphology features will return empty results but the app will still run.

## Data

The app starts with an empty database. Vocabulary is added through:
- `python scripts/import_duolingo.py` — 196 starter words from Duolingo Arabic
- `python scripts/import_wiktionary.py` — larger vocabulary from Wiktionary
- `python scripts/import_avp_a1.py` — A1-level vocabulary from Arabic Vocabulary Project
- **Learn mode** in the app — introduces words one at a time
- **Story import** — paste any Arabic text, unknown words are offered for learning
- **Textbook scanner** — OCR Arabic pages, extract and learn words

## Tests

```bash
cd backend && python -m pytest
```

There are ~559 tests. They don't require API keys (LLM/TTS calls are mocked).
