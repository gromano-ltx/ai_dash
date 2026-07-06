import pytest
from sqlmodel import Session, select

import backend.api.auth_routes as auth_routes_module
import backend.db as db_module
from backend.auth import hash_password
from backend.models import User


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


def test_me_returns_null_username_when_no_accounts_exist(test_client):
    res = test_client.get("/api/me")
    assert res.status_code == 200
    assert res.json() == {"username": None, "is_admin": False}


def test_login_fails_with_wrong_password(test_client):
    _seed_user("alice", "correct-password")
    res = test_client.post("/api/login", json={"username": "alice", "password": "wrong"})
    assert res.status_code == 401


def test_login_fails_for_unknown_user(test_client):
    res = test_client.post("/api/login", json={"username": "nobody", "password": "x"})
    assert res.status_code == 401


def test_login_succeeds_and_sets_session_cookie(test_client):
    _seed_user("alice", "correct-password", is_admin=True)
    res = test_client.post("/api/login", json={"username": "alice", "password": "correct-password"})
    assert res.status_code == 200
    assert res.json() == {"username": "alice", "is_admin": True}
    assert "ai_dash_session" in res.cookies


def test_me_requires_session_once_an_account_exists(test_client):
    _seed_user("alice", "correct-password")
    res = test_client.get("/api/me")
    assert res.status_code == 401


def test_me_returns_identity_after_login(test_client):
    _seed_user("alice", "correct-password")
    test_client.post("/api/login", json={"username": "alice", "password": "correct-password"})
    res = test_client.get("/api/me")
    assert res.status_code == 200
    assert res.json() == {"username": "alice", "is_admin": False}


def test_logout_clears_session(test_client):
    _seed_user("alice", "correct-password")
    test_client.post("/api/login", json={"username": "alice", "password": "correct-password"})
    test_client.post("/api/logout")
    res = test_client.get("/api/me")
    assert res.status_code == 401


def test_bootstrap_creates_first_account_as_admin_with_no_auth(test_client):
    res = test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    assert res.status_code == 201
    assert res.json()["is_admin"] is True

    with Session(db_module.engine) as session:
        user = session.exec(select(User).where(User.username == "gabby")).one()
        assert user.is_admin is True


def test_create_account_after_bootstrap_requires_admin(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    # No session cookie attached — a second creation attempt must be rejected.
    res = test_client.post("/api/accounts", json={"username": "bob", "password": "x"})
    assert res.status_code == 403


def test_create_account_as_admin_succeeds_and_is_not_admin_by_default(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    res = test_client.post("/api/accounts", json={"username": "bob", "password": "x"})
    assert res.status_code == 201
    assert res.json()["is_admin"] is False


def test_create_account_duplicate_username_conflicts(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    res = test_client.post("/api/accounts", json={"username": "gabby", "password": "x"})
    assert res.status_code == 409


def test_list_accounts_requires_admin(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/accounts", json={"username": "bob", "password": "x"})
    res = test_client.get("/api/accounts")
    assert res.status_code == 200
    usernames = {a["username"] for a in res.json()}
    assert usernames == {"gabby", "bob"}


def test_delete_last_admin_is_blocked(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    res = test_client.delete("/api/accounts/gabby")
    assert res.status_code == 400


def test_demote_last_admin_is_blocked(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    res = test_client.patch("/api/accounts/gabby", json={"is_admin": False})
    assert res.status_code == 400


def test_delete_non_admin_account_succeeds(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/accounts", json={"username": "bob", "password": "x"})
    res = test_client.delete("/api/accounts/bob")
    assert res.status_code == 200
    with Session(db_module.engine) as session:
        assert session.get(User, "bob") is None
