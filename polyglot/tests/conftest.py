import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def _disable_interaction_logging(monkeypatch):
    monkeypatch.setenv("TESTING", "1")


@pytest.fixture
def tmp_db(monkeypatch):
    """Per-test SQLite DB. Patches app.database engine/SessionLocal so tests
    don't share state with the dev DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"

    test_engine = create_engine(url, connect_args={"check_same_thread": False})
    TestSession = sessionmaker(bind=test_engine)

    from app import database
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)

    from app.database import Base, ensure_schema
    from app import models  # noqa: F401 — ensure all tables register
    Base.metadata.create_all(bind=test_engine)
    # New columns added via _ADDITIVE_COLUMN_DELTAS are folded into create_all
    # for fresh test DBs (the model already defines them), but ensure_schema
    # also runs here so the test path exercises the same code as production.
    ensure_schema()

    # Seed languages
    with TestSession() as db:
        from app.models import Language
        db.add(Language(code="el", name="Modern Greek", script="greek",
                        direction="ltr", accent_display="monotonic"))
        db.commit()

    yield TestSession
    os.unlink(path)
