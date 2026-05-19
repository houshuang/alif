import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


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

    from app.database import Base
    from app import models  # noqa: F401 — ensure all tables register
    Base.metadata.create_all(bind=test_engine)

    # Seed languages
    with TestSession() as db:
        from app.models import Language
        db.add(Language(code="el", name="Modern Greek", script="greek",
                        direction="ltr", accent_display="monotonic"))
        db.commit()

    yield TestSession
    os.unlink(path)
