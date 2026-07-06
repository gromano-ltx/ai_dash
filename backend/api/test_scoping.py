import pytest
from sqlmodel import Session

import backend.api.auth_routes as auth_routes_module
import backend.db as db_module
from backend.auth import hash_password
from backend.models import AgentRun, User


@pytest.fixture(autouse=True)
def _disable_secure_cookie(monkeypatch):
    # See backend/api/test_auth_routes.py for why this is needed: TestClient
    # talks to http://testserver, and httpx won't resend a Secure cookie set
    # by /api/login on subsequent requests within the same client.
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)


def _seed_users_and_runs(session: Session):
    session.add(User(username="alice", password_hash=hash_password("x")))
    session.add(User(username="bob", password_hash=hash_password("y"), is_admin=True))
    session.add(AgentRun(id="run-alice", provider="anthropic", model="m", input_tokens=10, output_tokens=10, user="alice"))
    session.add(AgentRun(id="run-bob", provider="anthropic", model="m", input_tokens=10, output_tokens=10, user="bob"))
    session.commit()


def _login(client, username: str, password: str):
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200


def test_non_admin_sees_only_own_runs(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "alice", "x")
    res = test_client.get("/api/runs")
    assert res.status_code == 200
    ids = {r["id"] for r in res.json()}
    assert ids == {"run-alice"}


def test_admin_sees_all_runs(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "bob", "y")
    res = test_client.get("/api/runs")
    assert res.status_code == 200
    ids = {r["id"] for r in res.json()}
    assert ids == {"run-alice", "run-bob"}


def test_non_admin_cannot_view_others_run_detail(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "alice", "x")
    res = test_client.get("/api/runs/run-bob")
    assert res.status_code == 404


def test_admin_can_view_any_run_detail(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "bob", "y")
    res = test_client.get("/api/runs/run-alice")
    assert res.status_code == 200


def test_non_admin_users_endpoint_only_lists_self(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "alice", "x")
    res = test_client.get("/api/users")
    assert res.json() == {"users": ["alice"]}


def test_non_admin_cannot_manage_api_keys(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "alice", "x")
    assert test_client.get("/api/keys").status_code == 403
    assert test_client.post("/api/keys", json={"user": "alice"}).status_code == 403
    assert test_client.delete("/api/keys/adk_devkey_local").status_code == 403


def test_admin_can_manage_api_keys(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "bob", "y")
    assert test_client.get("/api/keys").status_code == 200
