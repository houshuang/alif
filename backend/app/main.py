import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routers import words, review, analyze, stats, import_data, sentences, tts, learn, grammar, stories, chat, ocr, flags, activity, settings, books


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


@app.get("/")
def root():
    return {"app": "alif", "version": "0.1.0"}
