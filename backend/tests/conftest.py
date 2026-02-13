import os
import pytest
from contextlib import contextmanager
from sqlalchemy import create_engine, event, StaticPool
from sqlalchemy.orm import sessionmaker

os.environ["ALIF_SKIP_MIGRATIONS"] = "1"
os.environ["TESTING"] = "1"

from app.database import Base, get_db
from app.main import app


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
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


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
