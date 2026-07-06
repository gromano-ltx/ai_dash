from datetime import datetime

from backend.adapters._common import (
    COMMIT_HASH_RE,
    PR_URL_RE,
    _extract_tickets,
    _get_user,
    _parse_ts,
)


def test_extract_tickets_finds_ticket_ref():
    assert _extract_tickets("fixes AI-46 today") == ["AI-46"]


def test_extract_tickets_filters_non_ticket_prefixes():
    # AI-32 regression: technical abbreviations shaped like ticket keys must not match.
    assert _extract_tickets("encoded as UTF-8, hashed with SHA-256") == []


def test_extract_tickets_finds_issue_number_ref():
    assert _extract_tickets("closes #123") == ["#123"]


def test_extract_tickets_dedupes_preserving_order():
    assert _extract_tickets("AI-46 then AI-46 again, then AI-47") == ["AI-46", "AI-47"]


def test_parse_ts_handles_z_suffix():
    result = _parse_ts("2026-04-16T16:01:55.897Z")
    assert result == datetime(2026, 4, 16, 16, 1, 55, 897000)


def test_parse_ts_falls_back_to_utcnow_on_invalid_input():
    result = _parse_ts("not-a-timestamp")
    assert isinstance(result, datetime)


def test_commit_hash_re_extracts_hash_from_bracket_format():
    assert COMMIT_HASH_RE.findall("[main abc1234] fix bug") == ["abc1234"]


def test_pr_url_re_matches_github_pull_url():
    text = "opened https://github.com/gromano-ltx/ai_dash/pull/31"
    assert PR_URL_RE.findall(text) == ["https://github.com/gromano-ltx/ai_dash/pull/31"]


def test_get_user_returns_a_non_empty_string():
    assert isinstance(_get_user(), str)
    assert _get_user()
