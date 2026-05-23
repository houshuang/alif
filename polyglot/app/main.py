from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base, SessionLocal, ensure_schema
from app.routers import chat, flags, languages, materials, profile, reviews, stats, texts


def _seed_languages():
    """Idempotently insert the three language rows on startup. Eventual home
    is a real Alembic data migration; create_all + this seeder is fine while
    we're pre-schema-change."""
    from app.models import Language
    seeds = [
        {
            "code": "el", "name": "Modern Greek", "script": "greek", "direction": "ltr",
            "accent_display": "monotonic",
            "config_json": {
                "frequency_source": "subtlex_gr",
                "sentence_splitter": "default",
            },
        },
        {
            "code": "grc", "name": "Ancient Greek", "script": "greek", "direction": "ltr",
            "accent_display": "polytonic",
            "config_json": {
                "frequency_source": "perseus",
                "dialect_default": "attic",
            },
        },
        {
            "code": "la", "name": "Latin", "script": "latin", "direction": "ltr",
            "accent_display": "macrons_off",
            "config_json": {
                "frequency_source": "dickinson_core",
                "register_default": "classical",
            },
        },
    ]
    with SessionLocal() as db:
        for s in seeds:
            existing = db.query(Language).filter(Language.code == s["code"]).first()
            if existing:
                continue
            db.add(Language(**s))
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    from app.services.knowledge_lifecycle import backfill_knowledge_lifecycle
    with SessionLocal() as db:
        backfill_knowledge_lifecycle(db)
    _seed_languages()
    yield


app = FastAPI(
    title="Polyglot Reading Trainer",
    version="0.1.0",
    description="Reading-comprehension SRS for Modern Greek (primary), Ancient Greek, Latin. Sister app to Alif.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(languages.router)
app.include_router(texts.router)
app.include_router(profile.router)
app.include_router(reviews.router)
app.include_router(stats.router)
app.include_router(materials.router)
app.include_router(flags.router)
app.include_router(chat.router)


@app.get("/")
def root():
    return {"app": "polyglot", "version": "0.1.0"}
