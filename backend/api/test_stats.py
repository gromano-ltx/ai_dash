from datetime import datetime, timedelta

import pytest
from sqlmodel import Session

import backend.db as db_module
from backend import github as github_module
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


def test_avg_tokens_and_cost_per_pr_computed_correctly(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)

    with Session(db_module.engine) as session:
        session.add(User(username="erin", password_hash=hash_password("z"), is_admin=True))
        session.add(AgentRun(
            id="run-pr-1", provider="anthropic", model="claude-sonnet-5",
            input_tokens=1000, output_tokens=1000,
            estimated_cost_usd=10.00, git_prs=["pr-1"],
            user="erin",
        ))
        session.add(AgentRun(
            id="run-pr-2", provider="anthropic", model="claude-sonnet-5",
            input_tokens=2000, output_tokens=1000,
            estimated_cost_usd=20.00, git_prs=["pr-2", "pr-3"],
            user="erin",
        ))
        session.commit()

    _login(test_client, "erin", "z")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    body = res.json()
    # total tokens = 5000, total PRs = 3 -> avg tokens/pr = 1666.67
    assert body["total_prs_7d"] == 3
    assert body["avg_tokens_per_pr"] == pytest.approx(5000 / 3)
    # total cost = 30.00, total PRs = 3 -> avg cost/pr = 10.00
    assert body["avg_cost_per_pr_usd"] == pytest.approx(10.00)


def test_avg_tokens_and_cost_per_pr_is_none_when_no_prs_in_window(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)

    with Session(db_module.engine) as session:
        session.add(User(username="frank", password_hash=hash_password("v"), is_admin=True))
        session.add(AgentRun(
            id="run-no-pr", provider="anthropic", model="claude-sonnet-5",
            input_tokens=1000, output_tokens=1000,
            estimated_cost_usd=10.00, user="frank",
        ))
        session.commit()

    _login(test_client, "frank", "v")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["total_prs_7d"] == 0
    assert body["avg_tokens_per_pr"] is None
    assert body["avg_cost_per_pr_usd"] is None


def test_avg_cost_per_pr_changes_with_days_window(test_client, monkeypatch):
    # A PR-bearing run outside the requested window must not contribute to
    # the average once the window narrows past its start date.
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)

    old_start = datetime.utcnow() - timedelta(days=20)
    with Session(db_module.engine) as session:
        session.add(User(username="grace", password_hash=hash_password("u"), is_admin=True))
        session.add(AgentRun(
            id="run-old-pr", provider="anthropic", model="claude-sonnet-5",
            started_at=old_start, status="done",
            input_tokens=1000, output_tokens=1000,
            estimated_cost_usd=10.00, git_prs=["pr-old"],
            user="grace",
        ))
        session.add(AgentRun(
            id="run-recent-pr", provider="anthropic", model="claude-sonnet-5",
            input_tokens=3000, output_tokens=1000,
            estimated_cost_usd=40.00, git_prs=["pr-recent"],
            user="grace",
        ))
        session.commit()

    _login(test_client, "grace", "u")

    res_wide = test_client.get("/api/stats?days=30")
    assert res_wide.status_code == 200
    body_wide = res_wide.json()
    assert body_wide["total_prs_7d"] == 2
    assert body_wide["avg_cost_per_pr_usd"] == pytest.approx(25.00)

    res_narrow = test_client.get("/api/stats?days=3")
    assert res_narrow.status_code == 200
    body_narrow = res_narrow.json()
    assert body_narrow["total_prs_7d"] == 1
    assert body_narrow["avg_cost_per_pr_usd"] == pytest.approx(40.00)


def test_running_session_stays_in_totals_outside_its_own_start_window(test_client, monkeypatch):
    # A session that started before the requested window but is still
    # running must not silently drop out of the window's totals — it's
    # actively accumulating tokens/cost right now.
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)

    ancient_start = datetime.utcnow() - timedelta(days=10)
    with Session(db_module.engine) as session:
        session.add(User(username="dave", password_hash=hash_password("w"), is_admin=True))
        session.add(AgentRun(
            id="running-ancient-start", provider="anthropic", model="claude-sonnet-5",
            status="running", started_at=ancient_start,
            input_tokens=1000, output_tokens=2000,
            estimated_cost_usd=5.00, user="dave",
        ))
        session.commit()

    _login(test_client, "dave", "w")
    res = test_client.get("/api/stats?days=3")
    assert res.status_code == 200
    body = res.json()
    assert body["total_runs_7d"] == 1
    assert body["total_input_tokens_7d"] == 1000
    assert body["total_cost_usd"] == 5.00


# ── AI-48: PR merge success rate ─────────────────────────────────────────────

def test_stats_pr_merge_success_rate_is_none_without_github_token(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)
    monkeypatch.setattr(github_module, "GITHUB_TOKEN", "")

    with Session(db_module.engine) as session:
        session.add(User(username="erin", password_hash=hash_password("z"), is_admin=True))
        session.add(AgentRun(
            id="run-no-token", provider="anthropic", model="claude-sonnet-5",
            input_tokens=10, output_tokens=10, user="erin",
            git_prs=["https://github.com/acme/repo/pull/1"],
        ))
        session.commit()

    _login(test_client, "erin", "z")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    assert res.json()["pr_merge_success_rate"] is None


def test_stats_pr_merge_success_rate_counts_merged_pr(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)
    monkeypatch.setattr(github_module, "GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(github_module, "get_pr_state", lambda url: "merged")

    with Session(db_module.engine) as session:
        session.add(User(username="frank", password_hash=hash_password("z"), is_admin=True))
        session.add(AgentRun(
            id="run-merged", provider="anthropic", model="claude-sonnet-5",
            input_tokens=10, output_tokens=10, user="frank",
            git_prs=["https://github.com/acme/repo/pull/1"],
        ))
        session.commit()

    _login(test_client, "frank", "z")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["pr_merge_success_rate"] == 1.0
    assert body["pr_merge_success_merged"] == 1
    assert body["pr_merge_success_resolved"] == 1


def test_stats_pr_merge_success_rate_excludes_open_pr(test_client, monkeypatch):
    # An open PR must not count as a failure — it's simply excluded from the
    # denominator until it resolves one way or the other.
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)
    monkeypatch.setattr(github_module, "GITHUB_TOKEN", "test-token")

    states = {
        "https://github.com/acme/repo/pull/1": "merged",
        "https://github.com/acme/repo/pull/2": "open",
    }
    monkeypatch.setattr(github_module, "get_pr_state", lambda url: states[url])

    with Session(db_module.engine) as session:
        session.add(User(username="gina", password_hash=hash_password("z"), is_admin=True))
        session.add(AgentRun(
            id="run-mixed", provider="anthropic", model="claude-sonnet-5",
            input_tokens=10, output_tokens=10, user="gina",
            git_prs=list(states.keys()),
        ))
        session.commit()

    _login(test_client, "gina", "z")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["pr_merge_success_rate"] == 1.0  # 1 merged / 1 resolved, open excluded
    assert body["pr_merge_success_resolved"] == 1


def test_stats_pr_merge_success_rate_counts_closed_unmerged_as_failure(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)
    monkeypatch.setattr(github_module, "GITHUB_TOKEN", "test-token")

    states = {
        "https://github.com/acme/repo/pull/1": "merged",
        "https://github.com/acme/repo/pull/2": "closed",
    }
    monkeypatch.setattr(github_module, "get_pr_state", lambda url: states[url])

    with Session(db_module.engine) as session:
        session.add(User(username="hank", password_hash=hash_password("z"), is_admin=True))
        session.add(AgentRun(
            id="run-closed", provider="anthropic", model="claude-sonnet-5",
            input_tokens=10, output_tokens=10, user="hank",
            git_prs=list(states.keys()),
        ))
        session.commit()

    _login(test_client, "hank", "z")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["pr_merge_success_rate"] == 0.5
    assert body["pr_merge_success_merged"] == 1
    assert body["pr_merge_success_resolved"] == 2


def test_stats_pr_merge_success_rate_handles_lookup_error_without_crashing(test_client, monkeypatch):
    # A failed/rate-limited lookup (get_pr_state returning None, mirroring a
    # real API error) must not crash /stats — the PR is simply excluded, same
    # as an unresolved/open one.
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)
    monkeypatch.setattr(github_module, "GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(github_module, "get_pr_state", lambda url: None)

    with Session(db_module.engine) as session:
        session.add(User(username="iris", password_hash=hash_password("z"), is_admin=True))
        session.add(AgentRun(
            id="run-error", provider="anthropic", model="claude-sonnet-5",
            input_tokens=10, output_tokens=10, user="iris",
            git_prs=["https://github.com/acme/repo/pull/1"],
        ))
        session.commit()

    _login(test_client, "iris", "z")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["pr_merge_success_rate"] is None  # no resolved PRs to compute a rate from
    assert body["pr_merge_success_resolved"] == 0


def test_stats_pr_merge_success_rate_none_when_no_prs(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)
    monkeypatch.setattr(github_module, "GITHUB_TOKEN", "test-token")

    with Session(db_module.engine) as session:
        session.add(User(username="jack", password_hash=hash_password("z"), is_admin=True))
        session.add(AgentRun(
            id="run-no-prs", provider="anthropic", model="claude-sonnet-5",
            input_tokens=10, output_tokens=10, user="jack",
        ))
        session.commit()

    _login(test_client, "jack", "z")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    assert res.json()["pr_merge_success_rate"] is None
