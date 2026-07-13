import httpx
import pytest

from backend import github


# ── parse_pr_url ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/acme/repo/pull/42", ("acme", "repo", 42)),
        ("http://github.com/acme/repo/pull/42", ("acme", "repo", 42)),
        ("https://github.com/acme/repo/pull/42/", ("acme", "repo", 42)),
        ("https://github.com/acme/repo/pull/42/files", ("acme", "repo", 42)),
        ("https://github.com/acme/some-repo.name/pull/7", ("acme", "some-repo.name", 7)),
    ],
)
def test_parse_pr_url_valid(url, expected):
    assert github.parse_pr_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/repo/issues/42",       # issue, not a PR
        "https://gitlab.com/acme/repo/pull/42",         # wrong host
        "https://github.com/acme/repo/pull/",           # missing number
        "https://github.com/acme/repo",                 # no pull segment at all
        "not a url",
        "",
    ],
)
def test_parse_pr_url_invalid_returns_none(url):
    assert github.parse_pr_url(url) is None


# ── fetch_pr_state ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_cache_and_set_token(monkeypatch):
    monkeypatch.setattr(github, "GITHUB_TOKEN", "test-token")
    github._state_cache.clear()
    yield
    github._state_cache.clear()


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json


def test_fetch_pr_state_merged(monkeypatch):
    monkeypatch.setattr(
        github.httpx, "get",
        lambda *a, **k: _FakeResponse({"merged": True, "state": "closed"}),
    )
    assert github.fetch_pr_state("acme", "repo", 1) == "merged"


def test_fetch_pr_state_closed_unmerged(monkeypatch):
    monkeypatch.setattr(
        github.httpx, "get",
        lambda *a, **k: _FakeResponse({"merged": False, "state": "closed"}),
    )
    assert github.fetch_pr_state("acme", "repo", 2) == "closed"


def test_fetch_pr_state_open(monkeypatch):
    monkeypatch.setattr(
        github.httpx, "get",
        lambda *a, **k: _FakeResponse({"merged": False, "state": "open"}),
    )
    assert github.fetch_pr_state("acme", "repo", 3) == "open"


def test_fetch_pr_state_no_token_returns_none(monkeypatch):
    monkeypatch.setattr(github, "GITHUB_TOKEN", "")

    def _boom(*a, **k):
        raise AssertionError("should not call GitHub API without a token")
    monkeypatch.setattr(github.httpx, "get", _boom)

    assert github.fetch_pr_state("acme", "repo", 4) is None


def test_fetch_pr_state_http_error_returns_none_and_logs(monkeypatch, caplog):
    def _raise(*a, **k):
        raise httpx.HTTPError("boom")
    monkeypatch.setattr(github.httpx, "get", _raise)

    with caplog.at_level("WARNING"):
        result = github.fetch_pr_state("acme", "repo", 5)

    assert result is None
    assert any("acme" in r.message and "repo" in r.message for r in caplog.records)


def test_fetch_pr_state_rate_limit_status_returns_none(monkeypatch):
    monkeypatch.setattr(
        github.httpx, "get",
        lambda *a, **k: _FakeResponse({}, status_code=403),
    )
    assert github.fetch_pr_state("acme", "repo", 6) is None


def test_fetch_pr_state_caches_merged_state(monkeypatch):
    calls = []

    def _get(*a, **k):
        calls.append(1)
        return _FakeResponse({"merged": True, "state": "closed"})

    monkeypatch.setattr(github.httpx, "get", _get)
    assert github.fetch_pr_state("acme", "repo", 7) == "merged"
    assert github.fetch_pr_state("acme", "repo", 7) == "merged"
    assert len(calls) == 1  # second call served from cache, no network hit


def test_fetch_pr_state_does_not_cache_open_state(monkeypatch):
    calls = []

    def _get(*a, **k):
        calls.append(1)
        return _FakeResponse({"merged": False, "state": "open"})

    monkeypatch.setattr(github.httpx, "get", _get)
    assert github.fetch_pr_state("acme", "repo", 8) == "open"
    assert github.fetch_pr_state("acme", "repo", 8) == "open"
    assert len(calls) == 2  # open PRs are re-queried every time


# ── get_pr_state (URL-parsing wrapper) ────────────────────────────────────────

def test_get_pr_state_parses_and_delegates(monkeypatch):
    monkeypatch.setattr(
        github.httpx, "get",
        lambda *a, **k: _FakeResponse({"merged": True, "state": "closed"}),
    )
    assert github.get_pr_state("https://github.com/acme/repo/pull/9") == "merged"


def test_get_pr_state_returns_none_for_unparsable_url():
    assert github.get_pr_state("not-a-pr-url") is None
