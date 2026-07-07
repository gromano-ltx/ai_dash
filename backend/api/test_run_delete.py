import logging
import pytest

from sqlmodel import Session

import backend.api.auth_routes as auth_routes_module
import backend.db as db_module
from backend.auth import hash_password
from backend.models import AgentRun, TranscriptStore, User


@pytest.fixture(autouse=True)
def _disable_secure_cookie(monkeypatch):
    # FastAPI's TestClient hits the app over a plain http://testserver base
    # URL. httpx correctly refuses to resend a Secure cookie on a later
    # request within the same client, which would otherwise 401 every test
    # below that logs in and then makes a second, cookie-authenticated
    # request. Production still gets a Secure cookie (see auth_routes.py).
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)


def _seed_user(username: str, password: str, is_admin: bool = False) -> None:
    with Session(db_module.engine) as session:
        session.add(User(username=username, password_hash=hash_password(password), is_admin=is_admin))
        session.commit()


def _seed_run(run_id: str, *, parent_id: str | None = None, with_transcript: bool = True) -> None:
    with Session(db_module.engine) as session:
        session.add(AgentRun(id=run_id, provider="gemini", model="gemini-3.5-flash", parent_id=parent_id))
        if with_transcript:
            session.add(TranscriptStore(session_id=run_id, content="{}"))
        session.commit()


def _login_admin(test_client) -> None:
    _seed_user("gabby", "hunter2", is_admin=True)
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})


def test_delete_runs_removes_run_and_transcript(test_client):
    _login_admin(test_client)
    _seed_run("run1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": ["run1"]})

    assert res.status_code == 200
    assert res.json() == {"deleted": ["run1"], "not_found": []}
    with Session(db_module.engine) as session:
        assert session.get(AgentRun, "run1") is None
        assert session.get(TranscriptStore, "run1") is None


def test_delete_runs_cascades_to_children(test_client):
    _login_admin(test_client)
    _seed_run("parent1")
    _seed_run("child1", parent_id="parent1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": ["parent1"]})

    assert res.status_code == 200
    assert set(res.json()["deleted"]) == {"parent1", "child1"}
    assert res.json()["not_found"] == []
    with Session(db_module.engine) as session:
        assert session.get(AgentRun, "parent1") is None
        assert session.get(AgentRun, "child1") is None
        assert session.get(TranscriptStore, "child1") is None


def test_delete_runs_reports_not_found_for_missing_ids(test_client):
    _login_admin(test_client)
    _seed_run("run1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": ["run1", "nonexistent"]})

    assert res.status_code == 200
    assert res.json() == {"deleted": ["run1"], "not_found": ["nonexistent"]}


def test_delete_runs_rejects_batch_over_cap(test_client):
    _login_admin(test_client)
    _seed_run("run1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": [f"id-{i}" for i in range(101)]})

    assert res.status_code == 422
    with Session(db_module.engine) as session:
        # Nothing deleted — the cap check must happen before any deletion.
        assert session.get(AgentRun, "run1") is not None


def test_delete_runs_requires_admin(test_client):
    _seed_user("gabby", "hunter2", is_admin=True)
    _seed_user("bob", "x", is_admin=False)
    test_client.post("/api/login", json={"username": "bob", "password": "x"})
    _seed_run("run1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": ["run1"]})

    assert res.status_code == 403
    with Session(db_module.engine) as session:
        assert session.get(AgentRun, "run1") is not None


def test_delete_runs_requires_authentication(test_client):
    # Once any account exists, the auth middleware blocks all unauthenticated
    # /api/* requests with 401 before the route's own admin check ever runs
    # (same behavior as test_create_account_after_bootstrap_requires_admin
    # in test_auth_routes.py).
    _seed_user("gabby", "hunter2", is_admin=True)
    _seed_run("run1")

    res = test_client.request("DELETE", "/api/runs", json={"ids": ["run1"]})

    assert res.status_code == 401
    with Session(db_module.engine) as session:
        assert session.get(AgentRun, "run1") is not None


def test_delete_runs_logs_admin_username_and_ids(test_client, caplog):
    _login_admin(test_client)
    _seed_run("run1")

    with caplog.at_level(logging.INFO, logger="backend.api.routes"):
        test_client.request("DELETE", "/api/runs", json={"ids": ["run1"]})

    assert any("gabby" in r.message and "run1" in r.message for r in caplog.records)
