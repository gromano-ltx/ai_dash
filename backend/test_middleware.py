import pytest

import backend.api.auth_routes as auth_routes_module
import backend.main as main_module
import backend.db as db_module
from backend.auth import hash_password
from backend.models import User
from sqlmodel import Session


@pytest.fixture(autouse=True)
def _disable_secure_cookie(monkeypatch):
    # FastAPI's TestClient hits the app over a plain http://testserver base
    # URL. httpx correctly refuses to resend a Secure cookie on a later
    # request within the same client, which would otherwise 401 every test
    # below that logs in and then makes a second, cookie-authenticated
    # request. Production still gets a Secure cookie (see auth_routes.py).
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)


def test_fallback_mode_open_when_no_password_and_no_users(test_client, monkeypatch):
    monkeypatch.setattr(main_module, "_DASHBOARD_PASSWORD", "")
    res = test_client.get("/api/providers")
    assert res.status_code == 200


def test_fallback_mode_requires_basic_auth_password(test_client, monkeypatch):
    monkeypatch.setattr(main_module, "_DASHBOARD_PASSWORD", "secret")
    res = test_client.get("/api/providers")
    assert res.status_code == 401

    import base64

    good = base64.b64encode(b"ignored:secret").decode()
    res = test_client.get("/api/providers", headers={"Authorization": f"Basic {good}"})
    assert res.status_code == 200

    bad = base64.b64encode(b"ignored:wrong").decode()
    res = test_client.get("/api/providers", headers={"Authorization": f"Basic {bad}"})
    assert res.status_code == 401


def test_public_paths_bypass_auth_even_with_password_set(test_client, monkeypatch):
    monkeypatch.setattr(main_module, "_DASHBOARD_PASSWORD", "secret")
    assert test_client.get("/install.sh").status_code == 200
    assert test_client.get("/collector.py").status_code == 200


def test_session_mode_blocks_api_paths_without_cookie(test_client):
    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash=hash_password("x")))
        session.commit()
    res = test_client.get("/api/providers")
    assert res.status_code == 401


def test_session_mode_redirects_page_paths_without_cookie(test_client):
    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash=hash_password("x")))
        session.commit()
    res = test_client.get("/runs", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/login"


def test_session_mode_allows_api_paths_with_valid_cookie(test_client):
    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash=hash_password("correct-password")))
        session.commit()
    test_client.post("/api/login", json={"username": "alice", "password": "correct-password"})
    res = test_client.get("/api/providers")
    assert res.status_code == 200


def test_session_mode_ignores_dashboard_password_once_a_user_exists(test_client, monkeypatch):
    monkeypatch.setattr(main_module, "_DASHBOARD_PASSWORD", "secret")
    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash=hash_password("x")))
        session.commit()
    import base64

    good = base64.b64encode(b"ignored:secret").decode()
    res = test_client.get("/api/providers", headers={"Authorization": f"Basic {good}"})
    assert res.status_code == 401
