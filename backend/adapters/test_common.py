from datetime import datetime

from backend.adapters._common import (
    COMMIT_HASH_RE,
    PR_URL_RE,
    _classify_shell_command,
    _extract_tickets,
    _get_user,
    _parse_ts,
    _resolve_command_output,
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


def test_extract_tickets_excludes_number_already_in_pr_urls():
    # Squash-merge commit messages read "<title> (#40)" — that's a PR
    # self-reference, already shown as a full URL in git_prs, not a ticket.
    text = "Merge pull request #40 from gromano-ltx/feature-x"
    pr_urls = ["https://github.com/gromano-ltx/ai_dash/pull/40"]
    assert _extract_tickets(text, pr_urls) == []


def test_extract_tickets_keeps_issue_number_not_in_pr_urls():
    assert _extract_tickets("closes #123", ["https://github.com/gromano-ltx/ai_dash/pull/40"]) == ["#123"]


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


def test_classify_shell_command_marks_commit_pending():
    pending_commit_ids, pending_pr_ids, pending_remote_ids = set(), set(), set()
    _classify_shell_command(
        "git commit -am 'fix bug'", "call1",
        pending_commit_ids, pending_pr_ids, pending_remote_ids,
    )
    assert pending_commit_ids == {"call1"}
    assert pending_pr_ids == set()
    assert pending_remote_ids == set()


def test_classify_shell_command_marks_pr_pending():
    pending_commit_ids, pending_pr_ids, pending_remote_ids = set(), set(), set()
    _classify_shell_command(
        "gh pr create --title x", "call2",
        pending_commit_ids, pending_pr_ids, pending_remote_ids,
    )
    assert pending_pr_ids == {"call2"}


def test_classify_shell_command_marks_remote_pending_for_push_or_remote():
    pending_commit_ids, pending_pr_ids, pending_remote_ids = set(), set(), set()
    _classify_shell_command(
        "git push origin main", "call3",
        pending_commit_ids, pending_pr_ids, pending_remote_ids,
    )
    assert pending_remote_ids == {"call3"}


def test_classify_shell_command_ignores_unrelated_command():
    pending_commit_ids, pending_pr_ids, pending_remote_ids = set(), set(), set()
    _classify_shell_command(
        "ls -la", "call4",
        pending_commit_ids, pending_pr_ids, pending_remote_ids,
    )
    assert not pending_commit_ids and not pending_pr_ids and not pending_remote_ids


def test_classify_shell_command_noop_without_call_id():
    pending_commit_ids, pending_pr_ids, pending_remote_ids = set(), set(), set()
    _classify_shell_command(
        "git commit -am x", "",
        pending_commit_ids, pending_pr_ids, pending_remote_ids,
    )
    assert pending_commit_ids == set()


def test_resolve_command_output_extracts_commit_hash():
    git_commits, git_prs = [], []
    pending_commit_ids = {"call1"}
    github_repo = _resolve_command_output(
        "call1", "[main abc1234] fix bug\n",
        pending_commit_ids, set(), set(),
        git_commits, git_prs, None,
    )
    assert git_commits == ["abc1234"]
    assert github_repo is None
    assert pending_commit_ids == set()


def test_resolve_command_output_extracts_pr_url_and_repo():
    git_commits, git_prs = [], []
    pending_pr_ids = {"call2"}
    github_repo = _resolve_command_output(
        "call2", "https://github.com/gromano-ltx/ai_dash/pull/32\n",
        set(), pending_pr_ids, set(),
        git_commits, git_prs, None,
    )
    assert git_prs == ["https://github.com/gromano-ltx/ai_dash/pull/32"]
    assert github_repo == "https://github.com/gromano-ltx/ai_dash"
    assert pending_pr_ids == set()


def test_resolve_command_output_ignores_unrelated_call_id():
    git_commits, git_prs = [], []
    github_repo = _resolve_command_output(
        "unrelated_call", "[main abc1234] fix bug\n",
        {"call1"}, set(), set(),
        git_commits, git_prs, None,
    )
    assert git_commits == []
    assert github_repo is None
