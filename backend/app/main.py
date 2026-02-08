import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routers import words, review, analyze, stats, import_data, sentences, tts, learn


@asynccontextmanager
async def lifespan(app: FastAPI):
    alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    if alembic_ini.exists() and os.environ.get("ALIF_SKIP_MIGRATIONS") != "1":
        from alembic import command
        from alembic.config import Config
        alembic_cfg = Config(str(alembic_ini))
        alembic_cfg.set_main_option("script_location", str(alembic_ini.parent / "alembic"))
        command.upgrade(alembic_cfg, "head")
    else:
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Alif Arabic Learning API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8081",
        "http://localhost:19006",
        "http://localhost:5173",
    ],
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


@app.get("/")
def root():
    return {"app": "alif", "version": "0.1.0"}
