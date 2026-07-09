from datetime import datetime, timedelta

from sqlmodel import Session

import backend.db as db_module
from backend.auth import hash_password
from backend.models import AgentRun, User


def _login(client, username: str, password: str):
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200


def _today_key() -> str:
    return datetime.utcnow().strftime("%m/%d")


def test_running_session_buckets_on_today_not_its_start_day(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)

    old_start = datetime.utcnow() - timedelta(days=3)
    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash=hash_password("x"), is_admin=True))
        session.add(AgentRun(
            id="running-old-start", provider="anthropic", model="claude-sonnet-5",
            status="running", started_at=old_start,
            input_tokens=1000, output_tokens=2000, user="alice",
        ))
        session.commit()

    _login(test_client, "alice", "x")
    res = test_client.get("/api/daily?days=7")
    assert res.status_code == 200
    by_date = {d["date"]: d for d in res.json()}

    today = by_date[_today_key()]
    assert today["anthropic"] == 1
    assert today["input_tokens"] == 1000
    assert today["output_tokens"] == 2000

    start_day = by_date[old_start.strftime("%m/%d")]
    assert start_day["anthropic"] == 0
    assert start_day["input_tokens"] == 0


def test_done_session_still_buckets_on_its_start_day(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)

    old_start = datetime.utcnow() - timedelta(days=2)
    with Session(db_module.engine) as session:
        session.add(User(username="bob", password_hash=hash_password("y"), is_admin=True))
        session.add(AgentRun(
            id="done-old-start", provider="anthropic", model="claude-sonnet-5",
            status="done", started_at=old_start, ended_at=old_start + timedelta(hours=1),
            input_tokens=500, output_tokens=700, user="bob",
        ))
        session.commit()

    _login(test_client, "bob", "y")
    res = test_client.get("/api/daily?days=7")
    assert res.status_code == 200
    by_date = {d["date"]: d for d in res.json()}

    start_day = by_date[old_start.strftime("%m/%d")]
    assert start_day["anthropic"] == 1
    assert start_day["input_tokens"] == 500

    today = by_date[_today_key()]
    assert today["anthropic"] == 0


def test_running_session_stays_visible_outside_its_own_start_window(test_client, monkeypatch):
    # Started 10 days ago — outside a 3-day window by start date alone — but
    # still running, so it must not disappear from a 3-day query entirely.
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)

    ancient_start = datetime.utcnow() - timedelta(days=10)
    with Session(db_module.engine) as session:
        session.add(User(username="carol", password_hash=hash_password("z"), is_admin=True))
        session.add(AgentRun(
            id="running-ancient-start", provider="anthropic", model="claude-sonnet-5",
            status="running", started_at=ancient_start,
            input_tokens=42, output_tokens=99, user="carol",
        ))
        session.commit()

    _login(test_client, "carol", "z")
    res = test_client.get("/api/daily?days=3")
    assert res.status_code == 200
    by_date = {d["date"]: d for d in res.json()}

    today = by_date[_today_key()]
    assert today["anthropic"] == 1
    assert today["input_tokens"] == 42
