import json
import time
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional
from backend.models import AgentRun
from backend.adapters._common import (
    _classify_shell_command,
    _extract_tickets,
    _get_user,
    _parse_ts,
    _resolve_command_output,
)


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
    cached_input_tokens = 0
    cache_creation_input_tokens = 0

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
                        github_repo = _resolve_command_output(
                            tid, output,
                            pending_commit_ids, pending_pr_ids, pending_remote_ids,
                            git_commits, git_prs, github_repo,
                        )
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
                # A single API request is sometimes logged as more than one
                # assistant event with the same requestId and identical usage
                # (seen in real transcripts) — dedupe by requestId so those
                # tokens aren't counted twice.
                seen_request_ids.add(rid)
                usage = msg.get('usage', {})
                input_tokens += usage.get('input_tokens', 0)
                output_tokens += usage.get('output_tokens', 0)
                cached_input_tokens += usage.get('cache_read_input_tokens', 0)
                cache_creation_input_tokens += usage.get('cache_creation_input_tokens', 0)

            content = msg.get('content', [])
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict) or item.get('type') != 'tool_use':
                        continue
                    if item.get('name') == 'Bash':
                        cmd = item.get('input', {}).get('command', '')
                        tid = item.get('id', '')
                        _classify_shell_command(
                            cmd, tid,
                            pending_commit_ids, pending_pr_ids, pending_remote_ids,
                        )
                        bash_commands.append(cmd)

    # isMeta/ai-title events are rare; most transcript lines carry sessionId regardless
    # of event type, so fall back to that for stable run identity across ingests.
    if not session_id:
        session_id = next((e.get('sessionId') for e in events if e.get('sessionId')), None)

    # Subagent transcripts share sessionId with the parent — use agentId to avoid collision
    run_id = (f"agent-{agent_id}" if agent_id else None) or session_id or str(uuid.uuid4())

    if mtime is not None:
        # time.time() (not datetime.utcnow().timestamp()) — utcnow() is naive and
        # .timestamp() reinterprets naive datetimes as local time, which skews this
        # comparison by the host's UTC offset on any non-UTC machine.
        status = "running" if (time.time() - mtime) < 300 else "done"
    else:
        status = "done"

    ended_at = last_assistant_ts if status == "done" else None

    search_text = ' '.join(filter(None, [git_branch, first_user_text, label] + bash_commands))
    ticket_refs = _extract_tickets(search_text, git_prs)

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
        meta={
            "git_branch": git_branch, "cwd": cwd, "github_repo": github_repo,
            "cached_input_tokens": cached_input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
        },
    )


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
    # Capped at the 50 most recently modified files — this only runs once at
    # startup (watch() takes over incrementally after), so it's a bound on
    # startup cost, not an attempt to enumerate every historical transcript.
    for jsonl in sorted(base.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        run = parse_transcript(jsonl)
        if run:
            runs.append(run)
    return runs
