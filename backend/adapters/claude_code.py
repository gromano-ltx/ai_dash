import json
import re
import os
import uuid
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional
from backend.models import AgentRun

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


def parse_transcript_content(
    content: str,
    mtime: Optional[float] = None,
    parent_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Optional[AgentRun]:
    events = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not events:
        return None

    session_id = None
    started_at = None
    last_assistant_ts = None
    git_branch = None
    cwd = None
    model = "claude-sonnet-4-6"
    label = None
    first_user_text = None
    git_commits: list[str] = []
    git_prs: list[str] = []
    # Ticket refs are searched for in this text too — in a session that
    # touches several tickets, the ref usually shows up in a commit message,
    # branch name, or PR title/body rather than the first user message.
    bash_commands: list[str] = []

    seen_request_ids: set[str] = set()
    pending_commit_ids: set[str] = set()
    pending_pr_ids: set[str] = set()
    pending_remote_ids: set[str] = set()
    github_repo: str | None = None
    input_tokens = 0
    output_tokens = 0

    # Extract agentId and parent linkage from first event if not provided by caller
    if not agent_id and events:
        agent_id = events[0].get('agentId')
    if not parent_id and agent_id and events:
        # In subagent transcripts, sessionId is the parent session's ID
        parent_id = events[0].get('sessionId')

    for event in events:
        etype = event.get('type')
        ts_str = event.get('timestamp')
        ts = _parse_ts(ts_str) if ts_str else None

        if ts and (started_at is None or ts < started_at):
            started_at = ts

        if etype == 'user' and event.get('isMeta'):
            session_id = event.get('sessionId') or session_id
            git_branch = git_branch or event.get('gitBranch')
            cwd = cwd or event.get('cwd')

        elif etype == 'ai-title':
            session_id = event.get('sessionId') or session_id
            label = event.get('aiTitle')

        elif etype == 'user' and not event.get('isMeta'):
            msg = event.get('message', {})
            content_items = msg.get('content', '')
            if isinstance(content_items, list):
                for item in content_items:
                    if not isinstance(item, dict):
                        continue
                    if item.get('type') == 'tool_result':
                        tid = item.get('tool_use_id', '')
                        output = _extract_text(item.get('content', ''))
                        if tid in pending_remote_ids and not github_repo:
                            m = GITHUB_REPO_RE.search(output)
                            if m:
                                github_repo = f"https://github.com/{m.group(1)}"
                            pending_remote_ids.discard(tid)
                        if tid in pending_commit_ids:
                            # findall, not search: a single Bash call can run `git commit`
                            # more than once (e.g. a loop over several branches), and
                            # search() would silently keep only the first hash.
                            git_commits.extend(COMMIT_HASH_RE.findall(output))
                            pending_commit_ids.discard(tid)
                        if tid in pending_pr_ids:
                            # Same reasoning as above: a single Bash call can invoke
                            # `gh pr create` multiple times (or print several PR URLs,
                            # e.g. via `gh pr list`), so capture all of them.
                            pr_urls = PR_URL_RE.findall(output)
                            git_prs.extend(pr_urls)
                            if pr_urls and not github_repo:
                                repo_m = GITHUB_REPO_RE.match(pr_urls[0])
                                if repo_m:
                                    github_repo = f"https://github.com/{repo_m.group(1)}"
                            pending_pr_ids.discard(tid)
                    elif item.get('type') == 'text' and not first_user_text:
                        text = item.get('text', '').strip()
                        if text and not text.startswith('<'):
                            first_user_text = text[:500]
            elif isinstance(content_items, str) and not first_user_text:
                text = content_items.strip()
                if text and not text.startswith('<'):
                    first_user_text = text[:500]

        elif etype == 'assistant':
            msg = event.get('message', {})
            model = msg.get('model') or model
            rid = event.get('requestId')
            if ts:
                last_assistant_ts = ts

            if rid and rid not in seen_request_ids:
                seen_request_ids.add(rid)
                usage = msg.get('usage', {})
                input_tokens += usage.get('input_tokens', 0)
                output_tokens += usage.get('output_tokens', 0)

            content = msg.get('content', [])
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict) or item.get('type') != 'tool_use':
                        continue
                    if item.get('name') == 'Bash':
                        cmd = item.get('input', {}).get('command', '')
                        tid = item.get('id', '')
                        if GIT_COMMIT_RE.search(cmd) and tid:
                            pending_commit_ids.add(tid)
                        if GH_PR_RE.search(cmd) and tid:
                            pending_pr_ids.add(tid)
                        if (GIT_PUSH_RE.search(cmd) or GIT_REMOTE_RE.search(cmd)) and tid:
                            pending_remote_ids.add(tid)
                        bash_commands.append(cmd)

    # isMeta/ai-title events are rare; most transcript lines carry sessionId regardless
    # of event type, so fall back to that for stable run identity across ingests.
    if not session_id:
        session_id = next((e.get('sessionId') for e in events if e.get('sessionId')), None)

    # Subagent transcripts share sessionId with the parent — use agentId to avoid collision
    run_id = (f"agent-{agent_id}" if agent_id else None) or session_id or str(uuid.uuid4())

    if mtime is not None:
        status = "running" if (datetime.utcnow().timestamp() - mtime) < 300 else "done"
    else:
        status = "done"

    ended_at = last_assistant_ts if status == "done" else None

    search_text = ' '.join(filter(None, [git_branch, first_user_text, label] + bash_commands))
    ticket_refs = _extract_tickets(search_text)

    return AgentRun(
        id=run_id,
        provider="anthropic",
        model=model,
        status=status,
        started_at=started_at or datetime.utcnow(),
        ended_at=ended_at,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        label=label or (first_user_text or "")[:80] or "Claude Code session",
        task_description=first_user_text if first_user_text and len(first_user_text.split()) >= 3 else None,
        user=_get_user(),
        git_commits=list(dict.fromkeys(git_commits)),
        git_prs=list(dict.fromkeys(git_prs)),
        ticket_refs=ticket_refs,
        parent_id=parent_id,
        meta={"git_branch": git_branch, "cwd": cwd, "github_repo": github_repo},
    )


def _parse_ts(ts_str: str) -> datetime:
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get('type') == 'text':
                parts.append(c.get('text', ''))
        return ' '.join(parts).strip()
    return ''


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


def parse_transcript(path: Path) -> Optional[AgentRun]:
    try:
        content = path.read_text(errors='replace')
        mtime = path.stat().st_mtime
    except Exception:
        return None
    # Detect subagent transcripts: <parent_session_id>/subagents/agent-<agentId>.jsonl
    parent_id = None
    agent_id = None
    parts = path.parts
    if 'subagents' in parts:
        subagent_idx = parts.index('subagents')
        parent_id = parts[subagent_idx - 1]
        stem = path.stem  # e.g. "agent-ad288d9a6846a387d"
        if stem.startswith('agent-'):
            agent_id = stem[len('agent-'):]
    return parse_transcript_content(content, mtime=mtime, parent_id=parent_id, agent_id=agent_id)


def scan_all_transcripts() -> list[AgentRun]:
    base = Path.home() / ".claude" / "projects"
    runs = []
    if not base.exists():
        return runs
    for jsonl in sorted(base.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        run = parse_transcript(jsonl)
        if run:
            runs.append(run)
    return runs
