"""GitHub API lookups for AI-48 (PR merge success rate).

Falls back to a no-op (GITHUB_TOKEN unset) so local dev and any deployment
that hasn't configured a token still work — the /stats endpoint just omits
the PR-outcome stat rather than crashing. Production sets a real
GITHUB_TOKEN via Terraform/Secret Manager (see infra/main.tf), same pattern
as SESSION_SECRET/DASHBOARD_PASSWORD.
"""
import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

_PR_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)(?:/|$)")

# Terminal states (merged / closed-unmerged) never change again, so they're
# cached for the process lifetime. Open PRs are re-queried every call since
# their state can still change — see module docstring.
_state_cache: dict[tuple[str, str, int], str] = {}


def parse_pr_url(url: str) -> Optional[tuple[str, str, int]]:
    """Parse a GitHub PR URL into (owner, repo, number), or None if it isn't one."""
    match = _PR_URL_RE.search(url.strip())
    if not match:
        return None
    owner, repo, number = match.group(1), match.group(2), match.group(3)
    return owner, repo, int(number)


def fetch_pr_state(owner: str, repo: str, number: int) -> Optional[str]:
    """Return 'merged' | 'closed' | 'open' for a PR, or None if unresolvable
    (no GITHUB_TOKEN configured, or the lookup failed/was rate-limited).
    """
    cache_key = (owner, repo, number)
    cached = _state_cache.get(cache_key)
    if cached is not None:
        return cached

    if not GITHUB_TOKEN:
        return None

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    try:
        response = httpx.get(url, headers=headers, timeout=5.0)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("GitHub PR lookup failed for %s/%s#%s: %s", owner, repo, number, exc)
        return None

    if data.get("merged"):
        state = "merged"
    elif data.get("state") == "closed":
        state = "closed"
    else:
        state = "open"

    if state != "open":
        _state_cache[cache_key] = state
    return state


def get_pr_state(url: str) -> Optional[str]:
    """Parse a PR URL and look up its state in one step."""
    parsed = parse_pr_url(url)
    if not parsed:
        return None
    return fetch_pr_state(*parsed)
