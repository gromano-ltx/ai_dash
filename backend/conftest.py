import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine
from fastapi.testclient import TestClient

import backend.db as db_module


@pytest.fixture
def test_client(monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db_module, "engine", test_engine)

    # Import main AFTER monkeypatching db_module.engine, but we still need to
    # also monkeypatch watcher.engine because it imports engine at module level.
    from backend.main import app
    from backend import watcher

    # Monkeypatch watcher.engine to use the test engine
    monkeypatch.setattr(watcher, "engine", test_engine)

    # Prevent the watcher from scanning the developer's file system during tests.
    # We monkeypatch watcher's imported reference since it was bound at import time.
    monkeypatch.setattr(watcher, "scan_all_transcripts", lambda: [])

    with TestClient(app) as client:
        yield client
