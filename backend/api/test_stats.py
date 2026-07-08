from sqlmodel import Session

import backend.db as db_module
from backend.auth import hash_password
from backend.models import AgentRun, User


def _login(client, username: str, password: str):
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200


def test_stats_total_cost_sums_matched_runs_and_skips_none(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)

    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash=hash_password("x"), is_admin=True))
        session.add(AgentRun(
            id="run-costed-1", provider="anthropic", model="claude-sonnet-4-5-20250929",
            input_tokens=1_000_000, output_tokens=1_000_000,
            estimated_cost_usd=18.00, estimated_input_cost_usd=3.00, estimated_output_cost_usd=15.00,
            user="alice",
        ))
        session.add(AgentRun(
            id="run-costed-2", provider="anthropic", model="claude-haiku-4-5-20251001",
            input_tokens=1_000_000, output_tokens=1_000_000,
            estimated_cost_usd=4.80, estimated_input_cost_usd=0.80, estimated_output_cost_usd=4.00,
            user="alice",
        ))
        session.add(AgentRun(
            id="run-uncosted", provider="anthropic", model="unknown-model",
            input_tokens=1_000_000, output_tokens=1_000_000,
            user="alice",
        ))
        session.commit()

    _login(test_client, "alice", "x")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    assert res.json()["total_cost_usd"] == 22.80


def test_stats_total_cost_is_zero_when_no_runs_have_cost(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)

    with Session(db_module.engine) as session:
        session.add(User(username="bob", password_hash=hash_password("y"), is_admin=True))
        session.commit()

    _login(test_client, "bob", "y")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    assert res.json()["total_cost_usd"] == 0
