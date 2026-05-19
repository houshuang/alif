import contextvars
from contextlib import contextmanager
import logging
import os
import threading
import time
import traceback

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session as SQLAlchemySession

from app.config import settings

logger = logging.getLogger(__name__)

_db_context = contextvars.ContextVar("alif_db_context", default=None)
_active_writers: dict[int, dict] = {}
_active_writers_lock = threading.Lock()
_writer_watchdog_started = False
_writer_watchdog_start_lock = threading.Lock()
_WRITE_TX_WARN_AFTER_SECONDS = float(
    os.environ.get("ALIF_DB_WRITE_TX_WARN_AFTER_SECONDS", "10")
)
_LOCK_ERROR_SQL_PREVIEW_CHARS = 240


@contextmanager
def db_operation_context(label: str):
    """Attach a human-readable label to DB work on the current thread/task."""
    token = _db_context.set(label)
    try:
        yield
    finally:
        _db_context.reset(token)


def set_session_context(session, label: str) -> None:
    """Attach a label to a SQLAlchemy session for lock diagnostics."""
    if hasattr(session, "info"):
        session.info["context"] = label


def _current_context(session=None) -> str:
    if session is not None and hasattr(session, "info"):
        context = session.info.get("context")
        if context:
            return context
    return _db_context.get() or "unlabeled"


def _writer_snapshot() -> list[dict]:
    now = time.monotonic()
    with _active_writers_lock:
        return [
            {
                "session_id": record["session_id"],
                "context": record["context"],
                "thread": record["thread"],
                "age_s": round(now - record["started_at"], 2),
                "stack": record["stack"],
            }
            for record in _active_writers.values()
        ]


def _log_active_writers(prefix: str) -> None:
    snapshots = _writer_snapshot()
    if not snapshots:
        logger.warning("%s; no active ORM writer was recorded", prefix)
        return
    for snapshot in snapshots:
        logger.warning(
            "%s; active_writer session=%s context=%s thread=%s age=%.2fs\n%s",
            prefix,
            snapshot["session_id"],
            snapshot["context"],
            snapshot["thread"],
            snapshot["age_s"],
            snapshot["stack"],
        )


def _clear_writer(session, outcome: str) -> None:
    session_id = id(session)
    with _active_writers_lock:
        record = _active_writers.pop(session_id, None)
    session.info.pop("_alif_write_started_at", None)
    if not record:
        return
    held_s = time.monotonic() - record["started_at"]
    if held_s >= _WRITE_TX_WARN_AFTER_SECONDS:
        logger.warning(
            "SQLite write transaction held %.2fs before %s; "
            "session=%s context=%s thread=%s\n%s",
            held_s,
            outcome,
            session_id,
            record["context"],
            record["thread"],
            record["stack"],
        )


class TrackedSession(SQLAlchemySession):
    def close(self) -> None:
        _clear_writer(self, "session close")
        super().close()


def _writer_watchdog_loop() -> None:
    if _WRITE_TX_WARN_AFTER_SECONDS <= 0:
        return
    sleep_s = max(1.0, min(5.0, _WRITE_TX_WARN_AFTER_SECONDS / 2))
    while True:
        time.sleep(sleep_s)
        now = time.monotonic()
        overdue: list[tuple[dict, float]] = []
        with _active_writers_lock:
            for record in _active_writers.values():
                age_s = now - record["started_at"]
                if age_s < _WRITE_TX_WARN_AFTER_SECONDS:
                    continue
                last_warned_at = record.get("last_warned_at", 0)
                if now - last_warned_at < _WRITE_TX_WARN_AFTER_SECONDS:
                    continue
                record["last_warned_at"] = now
                overdue.append((dict(record), age_s))
        for record, age_s in overdue:
            logger.warning(
                "SQLite write transaction still open after %.2fs; "
                "session=%s context=%s thread=%s\n%s",
                age_s,
                record["session_id"],
                record["context"],
                record["thread"],
                record["stack"],
            )


def _ensure_writer_watchdog_started() -> None:
    global _writer_watchdog_started
    if _WRITE_TX_WARN_AFTER_SECONDS <= 0:
        return
    with _writer_watchdog_start_lock:
        if _writer_watchdog_started:
            return
        _writer_watchdog_started = True
        threading.Thread(
            target=_writer_watchdog_loop,
            daemon=True,
            name="sqlite-writer-watch",
        ).start()


@event.listens_for(TrackedSession, "after_begin")
def _track_session_begin(session, transaction, connection):
    session.info.setdefault("context", _current_context())


@event.listens_for(TrackedSession, "after_flush")
def _track_session_write(session, flush_context):
    if session.info.get("_alif_write_started_at") is not None:
        return
    started_at = time.monotonic()
    session.info["_alif_write_started_at"] = started_at
    session_id = id(session)
    record = {
        "session_id": session_id,
        "context": _current_context(session),
        "thread": threading.current_thread().name,
        "started_at": started_at,
        "stack": "".join(traceback.format_stack(limit=12)),
    }
    with _active_writers_lock:
        _active_writers[session_id] = record
    _ensure_writer_watchdog_started()


@event.listens_for(TrackedSession, "after_commit")
def _track_session_commit(session):
    _clear_writer(session, "commit")


@event.listens_for(TrackedSession, "after_rollback")
def _track_session_rollback(session):
    _clear_writer(session, "rollback")


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


@event.listens_for(engine, "handle_error")
def _log_sqlite_lock_error(exception_context):
    original = exception_context.original_exception
    if "database is locked" not in str(original).lower():
        return
    statement = (exception_context.statement or "").replace("\n", " ")
    if len(statement) > _LOCK_ERROR_SQL_PREVIEW_CHARS:
        statement = statement[:_LOCK_ERROR_SQL_PREVIEW_CHARS] + "..."
    _log_active_writers(
        "SQLite database is locked "
        f"context={_current_context()} statement={statement!r}"
    )


SessionLocal = sessionmaker(bind=engine, class_=TrackedSession)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
