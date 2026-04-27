import os
import tempfile
import pytest
from contextlib import contextmanager
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

os.environ["ALIF_SKIP_MIGRATIONS"] = "1"
os.environ["TESTING"] = "1"

# Point the app's engine at a tmp file BEFORE importing app.database, so all
# code paths — request-scoped sessions via Depends(get_db) and ad-hoc calls to
# SessionLocal() from services / BackgroundTasks — share one schema with the
# current model definitions. Using a real file (instead of :memory:) keeps the
# schema visible to fresh connections opened by SessionLocal().
_test_db_fd, _test_db_path = tempfile.mkstemp(suffix=".alif-test.db")
os.close(_test_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_test_db_path}"

from app.database import Base, engine, get_db
from app.main import app

# FastAPI BackgroundTasks runs queued tasks synchronously after the response in
# TestClient. Tasks like evaluate_flag, generate_material_for_word, etc. now
# share the test DB (via the env var above), so they actually execute and
# mutate state — breaking tests that asserted on intermediate state. Make
# add_task a no-op for the whole test run; tests that need background-task
# behavior should test those services directly.
from starlette.background import BackgroundTasks as _BG

_BG.add_task = lambda self, func, *args, **kwargs: None


# Several flag_evaluator tests deliberately seed ContentFlag rows with
# non-existent lemma_id / sentence_id to exercise "row was deleted" handling.
# Production has foreign_keys=ON (set by app.database), but enforcing that in
# tests would require complex fixturing for these legitimate cases. Disable FK
# enforcement on every test connection — runs after app.database's pragmas.
@event.listens_for(engine, "connect")
def _disable_fks_for_tests(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=OFF")
    cursor.close()


@contextmanager
def count_queries(db_session):
    """Context manager that counts SQL queries executed."""
    counter = {"count": 0}

    def _after_execute(conn, *args, **kwargs):
        counter["count"] += 1

    event.listen(db_session.bind, "after_execute", _after_execute)
    try:
        yield counter
    finally:
        event.remove(db_session.bind, "after_execute", _after_execute)


@contextmanager
def count_commits(db_session):
    """Context manager that counts DB commits."""
    counter = {"count": 0}

    def _after_commit(session):
        counter["count"] += 1

    event.listen(db_session, "after_commit", _after_commit)
    try:
        yield counter
    finally:
        event.remove(db_session, "after_commit", _after_commit)


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        # Sharing the production engine means SessionLocal() calls from
        # service code can leave pooled connections behind. Dispose the pool
        # at fixture teardown so the next test starts with a clean slate
        # (otherwise QueuePool overflows after ~15 tests).
        engine.dispose()


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def pytest_sessionfinish(session, exitstatus):
    """Remove the tmp test DB file (and any -wal/-shm sidecars) at end of run."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(_test_db_path + suffix)
        except FileNotFoundError:
            pass
