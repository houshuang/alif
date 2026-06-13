import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routers import words, review, analyze, stats, import_data, sentences, tts, learn, grammar, stories, chat, ocr, flags, activity, settings, books, patterns, roots, podcast, polyglot_proxy, discover


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

    # Recover any OCR pages stuck in 'pending' or 'processing' from a previous
    # crash/restart. Background batch tasks die silently on SIGTERM (systemctl
    # restart, OOM kill) without raising in _process_batch_background's
    # except-handler, so pages can stay stuck forever. The retry endpoint can
    # rerun them since images are saved on disk.
    try:
        from app.models import PageUpload
        from sqlalchemy.orm import Session as _Session
        with _Session(engine) as _db:
            stuck = (
                _db.query(PageUpload)
                .filter(PageUpload.status.in_(("processing", "pending")))
                .all()
            )
            if stuck:
                for u in stuck:
                    u.status = "failed"
                    u.error_message = "Server restarted before processing finished — use Retry to reprocess"
                _db.commit()
                import logging
                logging.getLogger(__name__).info(
                    "Recovered %d stuck OCR page(s) -> failed (saved images still on disk; user can Retry)",
                    len(stuck),
                )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("OCR stuck-page recovery failed")

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
# Reverse proxy to the polyglot backend on localhost:3002, so the client
# only needs to talk to ONE host:port. See routers/polyglot_proxy.py for
# the deliberate constraints — this module is pure HTTP passthrough, no
# code coupling to polyglot.
app.include_router(polyglot_proxy.router)
app.include_router(discover.router)

# Serve voice samples for comparison testing
from pathlib import Path as _Path
_voice_dir = _Path(__file__).resolve().parent.parent / "data" / "voice-samples"
if _voice_dir.exists():
    from starlette.staticfiles import StaticFiles
    app.mount("/api/voice-samples", StaticFiles(directory=str(_voice_dir)), name="voice-samples")


@app.get("/")
def root():
    return {"app": "alif", "version": "0.1.0"}
