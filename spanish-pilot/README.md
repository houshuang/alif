# Spanish Pilot

Standalone UX-validation prototype for a Norwegian school testing Alif-style word-level Spanish learning. Norwegian UI throughout (no English).

## Layout

```
spanish-pilot/
├── content/          Pre-generated lemmas + sentences (JSON)
├── backend/          FastAPI + SQLAlchemy + py-fsrs
├── frontend/         Vanilla HTML/CSS/JS
├── scripts/          Content generation + verification pipeline
└── data/             SQLite DB (gitignored, created on first run)
```

## Local dev

```bash
cd spanish-pilot
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/uvicorn backend.main:app --reload --port 3100
# visit http://localhost:3100/
```

## Regenerate content

Only needed if seed_lemmas.json changes or quality tuning:

```bash
python3 scripts/enrich_lemmas.py       # ~5 min — enriches lemmas with gloss, memory hooks, etymology, conjugations
python3 scripts/generate_sentences.py  # ~7 min — generates 150 sentences with verification
python3 scripts/render_preview.py      # produces content/preview.html for review
```

Both scripts use two-pass LLM verification with auto-correction.

## Deploy to Hetzner

First-time setup on server:
```bash
ssh alif
git clone https://github.com/stianhaklev/alif.git /opt/alif-pilot
cd /opt/alif-pilot && git checkout sh/spanish-pilot
cd spanish-pilot
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cp backend/alif-spanish-pilot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now alif-spanish-pilot
```

Subsequent deploys:
```bash
./deploy.sh
```

URL: `http://alifstian.duckdns.org:3100/`

## Scheduling algorithm

Three-phase word lifecycle matching Alif:

- **New** → first review → **Acquiring**
- **Acquiring (Leitner 3-box)**: box 1 (4h) → box 2 (1d) → box 3 (3d) → graduate
- **Learning (FSRS-6)**: py-fsrs handles scheduling
- **Known**: FSRS stability ≥ 1.0
- **Lapsed**: failure after learning/known → back to FSRS with reset

## Key design choices

- Sentence-first review: no bare-word cards
- Tap any word → detail card with memory hook + etymology + conjugation + personal stats
- Two modes toggled per-student: self-grade (FSRS 4-button) or multiple-choice (4 options)
- Name-only login: list of registered students, click to enter
- Separate SQLite at `data/pilot.db` — isolated from main Alif
- All content pre-generated + dual-verified + human-approved before deploy
