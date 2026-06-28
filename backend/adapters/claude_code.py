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
GIT_COMMIT_RE = re.compile(r'git commit')
GH_PR_RE = re.compile(r'gh pr create')


def parse_transcript(path: Path) -> Optional[AgentRun]:
    try:
        content = path.read_text(errors='replace')
        mtime = path.stat().st_mtime
    except Exception:
        return None
    return parse_transcript_content(content, mtime=mtime)


def parse_transcript_content(content: str, mtime: Optional[float] = None) -> Optional[AgentRun]:
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

    seen_request_ids: set[str] = set()
    input_tokens = 0
    output_tokens = 0

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
            text = _extract_text(msg.get('content', ''))
            if text and not first_user_text and not text.startswith('<'):
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
                    tool_name = item.get('name', '')
                    tool_input = item.get('input', {})
                    if tool_name == 'Bash':
                        cmd = tool_input.get('command', '')
                        if GIT_COMMIT_RE.search(cmd):
                            git_commits.append(cmd[:120])
                        if GH_PR_RE.search(cmd):
                            git_prs.append(cmd[:300])

    run_id = session_id or str(uuid.uuid4())

    # Determine status from file modification time
    if mtime is not None:
        status = "running" if (datetime.utcnow().timestamp() - mtime) < 300 else "done"
    else:
        status = "done"

    ended_at = last_assistant_ts if status == "done" else None

    # Extract ticket refs from branch + task + label
    search_text = ' '.join(filter(None, [git_branch, first_user_text, label]))
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
        task_description=first_user_text,
        user=_get_user(),
        git_commits=list(dict.fromkeys(git_commits)),
        git_prs=list(dict.fromkeys(git_prs)),
        ticket_refs=ticket_refs,
        meta={"git_branch": git_branch, "cwd": cwd},
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
        ref = m.group(1) or f"#{m.group(2)}"
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
