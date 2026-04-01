import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routers import words, review, analyze, stats, import_data, sentences, tts, learn, grammar, stories, chat, ocr, flags, activity, settings, books, patterns, roots, podcast


@asynccontextmanager
async def lifespan(app: FastAPI):
    alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    if alembic_ini.exists() and os.environ.get("ALIF_SKIP_MIGRATIONS") != "1":
        # Run in subprocess to avoid SQLite/WAL locking issues with uvicorn's event loop
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-c",
             "from alembic import command; from alembic.config import Config; "
             f"c = Config('{alembic_ini}'); "
             f"c.set_main_option('script_location', '{alembic_ini.parent / 'alembic'}'); "
             "command.upgrade(c, 'head')"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            import logging
            logging.getLogger(__name__).error(f"Alembic failed: {result.stderr}")
    else:
        Base.metadata.create_all(bind=engine)

    # Recover any flags stuck in 'reviewing' from a previous crash/restart
    from app.services.flag_evaluator import recover_stuck_flags
    recovered = recover_stuck_flags()
    if recovered:
        import logging
        logging.getLogger(__name__).info("Recovered %d stuck flag(s) back to pending", recovered)

    # Register LLM cost tracking (limbic mounted via PYTHONPATH)
    try:
        import litellm
        from limbic.cerebellum.cost_log import cost_log
        litellm.callbacks = [cost_log.callback("alif")]
        import logging
        logging.getLogger(__name__).info("LLM cost tracking active → %s", cost_log.db_path)
    except ImportError:
        pass

    yield


app = FastAPI(title="Alif Arabic Learning API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(words.router)
app.include_router(review.router)
app.include_router(analyze.router)
app.include_router(stats.router)
app.include_router(import_data.router)
app.include_router(sentences.router)
app.include_router(tts.router)
app.include_router(learn.router)
app.include_router(grammar.router)
app.include_router(stories.router)
app.include_router(chat.router)
app.include_router(ocr.router)
app.include_router(flags.router)
app.include_router(activity.router)
app.include_router(settings.router)
app.include_router(books.router)
app.include_router(patterns.router)
app.include_router(roots.router)
app.include_router(podcast.router)

# Serve voice samples for comparison testing
from pathlib import Path as _Path
_voice_dir = _Path(__file__).resolve().parent.parent / "data" / "voice-samples"
if _voice_dir.exists():
    from starlette.staticfiles import StaticFiles
    app.mount("/api/voice-samples", StaticFiles(directory=str(_voice_dir)), name="voice-samples")


@app.get("/")
def root():
    return {"app": "alif", "version": "0.1.0"}
