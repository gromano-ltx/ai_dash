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

DEFAULT_MODEL = "gpt-5-codex"


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
    last_ts = None
    git_branch = None
    cwd = None
    model = DEFAULT_MODEL
    first_user_text = None
    git_commits: list[str] = []
    git_prs: list[str] = []
    bash_commands: list[str] = []

    pending_commit_ids: set[str] = set()
    pending_pr_ids: set[str] = set()
    pending_remote_ids: set[str] = set()
    github_repo: Optional[str] = None

    input_tokens = 0
    output_tokens = 0

    for event in events:
        etype = event.get('type')
        payload = event.get('payload', {})
        ts_str = event.get('timestamp')
        ts = _parse_ts(ts_str) if ts_str else None

        if ts and (started_at is None or ts < started_at):
            started_at = ts
        if ts and (last_ts is None or ts > last_ts):
            last_ts = ts

        if etype == 'session_meta':
            session_id = payload.get('id') or session_id
            cwd = cwd or payload.get('cwd')
            git_info = payload.get('git') or {}
            git_branch = git_branch or git_info.get('branch')

        elif etype == 'turn_context':
            model = payload.get('model') or model

        elif etype == 'response_item':
            ptype = payload.get('type')

            if ptype == 'message' and payload.get('role') == 'user':
                if first_user_text:
                    continue
                for item in payload.get('content', []):
                    if not isinstance(item, dict):
                        continue
                    if item.get('type') == 'input_text':
                        text = item.get('text', '').strip()
                        if text and not text.startswith('<'):
                            first_user_text = text[:500]
                            break

            elif ptype == 'function_call' and payload.get('name') == 'shell':
                call_id = payload.get('call_id', '')
                try:
                    args = json.loads(payload.get('arguments') or '{}')
                except json.JSONDecodeError:
                    args = {}
                cmd_list = args.get('command', [])
                cmd = ' '.join(cmd_list) if isinstance(cmd_list, list) else str(cmd_list)
                if cmd:
                    bash_commands.append(cmd)
                    _classify_shell_command(
                        cmd, call_id,
                        pending_commit_ids, pending_pr_ids, pending_remote_ids,
                    )

            elif ptype == 'function_call_output':
                call_id = payload.get('call_id', '')
                raw_output = payload.get('output', '')
                try:
                    output = json.loads(raw_output).get('output', '') if raw_output else ''
                except (json.JSONDecodeError, AttributeError):
                    output = raw_output if isinstance(raw_output, str) else ''

                github_repo = _resolve_command_output(
                    call_id, output,
                    pending_commit_ids, pending_pr_ids, pending_remote_ids,
                    git_commits, git_prs, github_repo,
                )

        elif etype == 'event_msg' and payload.get('type') == 'token_count':
            info = payload.get('info')
            if info:
                usage = info.get('total_token_usage') or {}
                input_tokens = usage.get('input_tokens', input_tokens)
                output_tokens = usage.get('output_tokens', output_tokens)

    if not session_id:
        session_id = next(
            (e.get('payload', {}).get('id') for e in events if e.get('type') == 'session_meta'),
            None,
        )

    run_id = (f"agent-{agent_id}" if agent_id else None) or session_id or str(uuid.uuid4())

    if mtime is not None:
        # time.time() (not datetime.utcnow().timestamp()) — utcnow() is naive and
        # .timestamp() reinterprets naive datetimes as local time, which skews this
        # comparison by the host's UTC offset on any non-UTC machine.
        status = "running" if (time.time() - mtime) < 300 else "done"
    else:
        status = "done"

    ended_at = last_ts if status == "done" else None

    label = (first_user_text or "")[:80] or "Codex session"
    search_text = ' '.join(filter(None, [git_branch, first_user_text, label] + bash_commands))
    ticket_refs = _extract_tickets(search_text)

    return AgentRun(
        id=run_id,
        provider="openai",
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
        meta={"git_branch": git_branch, "cwd": cwd, "github_repo": github_repo},
    )
