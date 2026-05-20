from sqlalchemy import create_engine, event, inspect

from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA cache_size=-64000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Schema deltas applied at startup. SQLite has no `ADD COLUMN IF NOT EXISTS`,
# so this list is the source of truth for nullable columns added after a table
# was first introduced via `Base.metadata.create_all`. When polyglot graduates
# to multi-user / production, fold these into proper Alembic revisions; the
# `alembic/` skeleton is already in place.
_ADDITIVE_COLUMN_DELTAS: list[tuple[str, str, str]] = [
    # (table, column, sqlite_type)
    ("review_log", "client_review_id", "VARCHAR(50)"),
    ("review_log", "comprehension_signal", "VARCHAR(20)"),
    ("review_log", "credit_type", "VARCHAR(20)"),
    ("review_log", "was_confused", "BOOLEAN DEFAULT 0"),
    ("sentences", "page_id", "INTEGER REFERENCES pages(id)"),
    ("sentences", "sentence_index_in_page", "INTEGER"),
    ("user_lemma_knowledge", "experiment_intro_shown_at", "DATETIME"),
]

# Indexes that should exist once the columns above are present.
_ADDITIVE_INDEX_DELTAS: list[tuple[str, str, str, bool]] = [
    # (index_name, table, column, unique)
    ("ix_review_log_client_review_id", "review_log", "client_review_id", True),
    ("ix_sentences_page_id", "sentences", "page_id", False),
    ("uq_sentences_page_sidx", "sentences", "page_id, sentence_index_in_page", True),
    ("ix_sentence_review_log_client_review_id", "sentence_review_log", "client_review_id", True),
    ("ix_sentence_review_log_sentence_id", "sentence_review_log", "sentence_id", False),
]


def ensure_schema() -> None:
    """Apply additive schema changes the model defines that aren't yet on disk.

    Run after `Base.metadata.create_all(bind=engine)`. Idempotent: each delta
    is gated on a schema introspection so re-running is a no-op for an
    up-to-date DB. New tables come from create_all; new columns + new indexes
    on existing tables are what this function adds.
    """
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, sql_type in _ADDITIVE_COLUMN_DELTAS:
            if table not in existing_tables:
                continue
            existing_cols = {c["name"] for c in insp.get_columns(table)}
            if column in existing_cols:
                continue
            conn.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
            )
        # Re-inspect — new columns might enable new indexes
        insp_post = inspect(engine)
        for index_name, table, column, unique in _ADDITIVE_INDEX_DELTAS:
            if table not in existing_tables:
                continue
            existing_indexes = {i["name"] for i in insp_post.get_indexes(table)}
            if index_name in existing_indexes:
                continue
            unique_kw = "UNIQUE " if unique else ""
            conn.exec_driver_sql(
                f"CREATE {unique_kw}INDEX {index_name} ON {table}({column})"
            )
