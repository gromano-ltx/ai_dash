import json
import time
import uuid
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

DEFAULT_MODEL = "gemini-3.5-flash"


def _tool_call_output(tool_call: dict) -> str:
    result = tool_call.get('result')
    if not isinstance(result, list) or not result:
        return ''
    first = result[0]
    if not isinstance(first, dict):
        return ''
    return (
        first.get('functionResponse', {})
        .get('response', {})
        .get('output', '') or ''
    )


def parse_transcript_content(
    content: str,
    mtime: Optional[float] = None,
    parent_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Optional[AgentRun]:
    lines = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not lines:
        return None

    # Line 0 is always the header (sessionId/startTime/kind) — it has neither
    # a "$set" nor a top-level "type" key, so it's naturally excluded from the
    # flattened event list built below.
    header = lines[0]
    session_id = header.get('sessionId')
    started_at = _parse_ts(header['startTime']) if header.get('startTime') else None

    # Flatten both event shapes into one ordered list: the one real message
    # embedded in the initial {"$set": {"messages": [...]}} checkpoint line,
    # plus every standalone top-level {"type": ...} event. Later "$set" lines
    # (lastUpdated-only housekeeping, or the final summary/memoryScratchpad
    # line) carry no "messages" key and contribute nothing.
    events = []
    for obj in lines:
        if '$set' in obj:
            events.extend(obj['$set'].get('messages', []))
        elif 'type' in obj:
            events.append(obj)

    model = DEFAULT_MODEL
    first_user_text = None
    last_ts = None
    git_commits: list[str] = []
    git_prs: list[str] = []
    bash_commands: list[str] = []

    pending_commit_ids: set[str] = set()
    pending_pr_ids: set[str] = set()
    pending_remote_ids: set[str] = set()
    github_repo: Optional[str] = None

    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0

    # Real sessions on this machine log the same message id twice in a row (a
    # debounced-write artifact of the checkpoint format) — dedupe before
    # accumulating anything.
    seen_ids: set[str] = set()

    for event in events:
        eid = event.get('id')
        if eid:
            if eid in seen_ids:
                continue
            seen_ids.add(eid)

        ts_str = event.get('timestamp')
        ts = _parse_ts(ts_str) if ts_str else None
        if ts and (last_ts is None or ts > last_ts):
            last_ts = ts

        etype = event.get('type')

        if etype == 'user':
            if first_user_text:
                continue
            for item in event.get('content', []) or []:
                if not isinstance(item, dict):
                    continue
                text = (item.get('text') or '').strip()
                if text and not text.startswith('<'):
                    first_user_text = text[:500]
                    break

        elif etype == 'gemini':
            model = event.get('model') or model
            tokens = event.get('tokens') or {}
            cached = tokens.get('cached', 0)
            input_tokens += max(tokens.get('input', 0) - cached, 0)
            cached_input_tokens += cached
            output_tokens += (
                tokens.get('output', 0) + tokens.get('thoughts', 0) + tokens.get('tool', 0)
            )

            for tool_call in event.get('toolCalls', []) or []:
                if tool_call.get('name') != 'run_shell_command':
                    continue
                cmd = tool_call.get('args', {}).get('command', '')
                call_id = tool_call.get('id', '')
                if not cmd:
                    continue
                bash_commands.append(cmd)
                # Unlike Claude Code/Codex, a Gemini toolCalls entry already
                # carries both the command and its result together — classify
                # then immediately resolve in the same pass, no cross-event
                # pending-id tracking required.
                _classify_shell_command(
                    cmd, call_id,
                    pending_commit_ids, pending_pr_ids, pending_remote_ids,
                )
                output = _tool_call_output(tool_call)
                github_repo = _resolve_command_output(
                    call_id, output,
                    pending_commit_ids, pending_pr_ids, pending_remote_ids,
                    git_commits, git_prs, github_repo,
                )

    # agent_id is always None for this adapter (kept only for signature parity
    # with claude_code.py/codex.py) — Gemini subagent sessions already have a
    # globally-unique sessionId, so parent/child linkage is carried entirely
    # by parent_id (via the collector's X-Parent-Id header), not agent_id.
    run_id = (f"agent-{agent_id}" if agent_id else None) or session_id or str(uuid.uuid4())

    if mtime is not None:
        status = "running" if (time.time() - mtime) < 300 else "done"
    else:
        status = "done"

    ended_at = last_ts if status == "done" else None

    label = (first_user_text or "")[:80] or "Gemini CLI session"
    search_text = ' '.join(filter(None, [first_user_text, label] + bash_commands))
    ticket_refs = _extract_tickets(search_text, git_prs)

    return AgentRun(
        id=run_id,
        provider="gemini",
        model=model,
        status=status,
        started_at=started_at or datetime.utcnow(),
        ended_at=ended_at,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        label=label,
        task_description=first_user_text if first_user_text and len(first_user_text.split()) >= 3 else None,
        user=_get_user(),
        git_commits=list(dict.fromkeys(git_commits)),
        git_prs=list(dict.fromkeys(git_prs)),
        ticket_refs=ticket_refs,
        parent_id=parent_id,
        meta={"git_branch": None, "cwd": None, "github_repo": github_repo, "cached_input_tokens": cached_input_tokens},
    )
