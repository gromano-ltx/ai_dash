import os
import subprocess
import re
from datetime import datetime
from typing import Optional

TICKET_RE = re.compile(r'\b([A-Z]{2,10}-\d+)\b|#(\d+)\b')
# Common technical abbreviations that match the ticket-key shape
# ([A-Z]{2,10}-\d+) but are never real ticket prefixes, e.g. "UTF-8",
# "SHA-256", "ISO-8601" — these show up constantly in commit messages
# and code discussion and would otherwise render as bogus ticket refs.
_NON_TICKET_PREFIXES = frozenset({
    "UTF", "SHA", "HTTP", "HTTPS", "ISO", "RFC", "MD", "CRC", "JSON", "XML",
    "HTML", "CSS", "URL", "URI", "API", "SQL", "CPU", "GPU", "RAM", "TCP",
    "UDP", "DNS", "CDN", "JWT", "CORS", "REST", "AES", "RSA", "SSH", "SSL",
    "TLS", "IPV", "USB", "PDF", "CSV", "YAML", "TOML", "GRPC", "OAUTH",
    "ASCII", "UUID", "GUID", "IP", "OS", "IO", "ID",
})
GIT_COMMIT_RE = re.compile(r'\bgit commit\b')
GH_PR_RE = re.compile(r'\bgh pr create\b')
GIT_PUSH_RE = re.compile(r'\bgit push\b')
GIT_REMOTE_RE = re.compile(r'\bgit remote\b')
COMMIT_HASH_RE = re.compile(r'\[[\w/._-]+ ([0-9a-f]{7,40})\]')
PR_URL_RE = re.compile(r'https://github\.com/\S+/pull/\d+')
GITHUB_REPO_RE = re.compile(r'(?:https://github\.com/|github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?(?:[/\s]|$)')


def _parse_ts(ts_str: str) -> datetime:
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def _extract_tickets(text: str) -> list[str]:
    refs = []
    for m in TICKET_RE.finditer(text):
        if m.group(1):
            if m.group(1).split('-')[0] in _NON_TICKET_PREFIXES:
                continue
            ref = m.group(1)
        else:
            ref = f"#{m.group(2)}"
        if ref not in refs:
            refs.append(ref)
    return refs


def _get_user() -> str:
    try:
        result = subprocess.run(
            ['git', 'config', 'user.name'],
            capture_output=True, text=True, timeout=2
        )
        name = result.stdout.strip()
        if name:
            return name
    except Exception:
        pass
    return os.environ.get('USER', 'unknown')


def _classify_shell_command(
    cmd: str,
    call_id: str,
    pending_commit_ids: set[str],
    pending_pr_ids: set[str],
    pending_remote_ids: set[str],
) -> None:
    """Mark call_id as pending in the matching set(s) if cmd looks like a git
    commit/PR-create/push-or-remote operation, so the corresponding output can
    be resolved into commits/PRs/repo once it arrives (see
    _resolve_command_output)."""
    if not call_id:
        return
    if GIT_COMMIT_RE.search(cmd):
        pending_commit_ids.add(call_id)
    if GH_PR_RE.search(cmd):
        pending_pr_ids.add(call_id)
    if GIT_PUSH_RE.search(cmd) or GIT_REMOTE_RE.search(cmd):
        pending_remote_ids.add(call_id)


def _resolve_command_output(
    call_id: str,
    output: str,
    pending_commit_ids: set[str],
    pending_pr_ids: set[str],
    pending_remote_ids: set[str],
    git_commits: list[str],
    git_prs: list[str],
    github_repo: Optional[str],
) -> Optional[str]:
    """Resolve a pending call_id's command output into git_commits/git_prs
    (extended in place) and github_repo (returned, since strings are
    immutable — callers must reassign their local variable from the return
    value)."""
    if call_id in pending_remote_ids and not github_repo:
        m = GITHUB_REPO_RE.search(output)
        if m:
            github_repo = f"https://github.com/{m.group(1)}"
        pending_remote_ids.discard(call_id)
    if call_id in pending_commit_ids:
        # findall, not search: a single command can run `git commit` more
        # than once (e.g. a loop over several branches), and search() would
        # silently keep only the first hash.
        git_commits.extend(COMMIT_HASH_RE.findall(output))
        pending_commit_ids.discard(call_id)
    if call_id in pending_pr_ids:
        # Same reasoning as above: a single command can invoke `gh pr
        # create` multiple times (or print several PR URLs, e.g. via
        # `gh pr list`), so capture all of them.
        pr_urls = PR_URL_RE.findall(output)
        git_prs.extend(pr_urls)
        if pr_urls and not github_repo:
            repo_m = GITHUB_REPO_RE.match(pr_urls[0])
            if repo_m:
                github_repo = f"https://github.com/{repo_m.group(1)}"
        pending_pr_ids.discard(call_id)
    return github_repo
