# AI-46 OpenAI (Codex CLI) Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest real Codex CLI coding-agent sessions into the dashboard, labeled `provider="openai"`, by adding a new adapter and generalizing the collector/backend pipeline to dispatch between multiple providers.

**Architecture:** A shared extraction module holds the regex/text-processing logic currently duplicated inline in `claude_code.py`; a new `codex.py` adapter parses Codex CLI's JSONL transcript format into the same `AgentRun` shape; the backend's `/v1/ingest` endpoint dispatches to the right adapter based on a new `X-Provider` header; the collector generalizes its single watched directory into a `{provider: path}` registry, watching and shipping from both `~/.claude/projects` and `~/.codex/sessions`.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, pytest, watchfiles/httpx (collector).

## Global Constraints

- Every Codex CLI session is labeled `provider="openai"` regardless of which backend model it
  actually used — no per-session provider detection.
- Codex's `token_count` events are cumulative running totals — take the **last** non-null event's
  values directly; never sum across events (unlike Claude Code's per-message deltas, which are
  summed).
- Provider dispatch uses an explicit `X-Provider` header set by the collector; defaults to
  `"anthropic"` server-side when the header is absent, for backward compatibility with
  not-yet-upgraded collector installs.
- `backend/watcher.py`'s local-only watch loop stays Claude-Code-only — out of scope.
- Shared regex/extraction helpers live in `backend/adapters/_common.py`, imported by both
  adapters — not duplicated.

---

### Task 1: Shared extraction module

**Files:**
- Create: `backend/adapters/_common.py`
- Modify: `backend/adapters/claude_code.py:1-30` (imports + regex/constant definitions),
  `backend/adapters/claude_code.py:205-249` (`_parse_ts`, `_extract_tickets`, `_get_user`)
- Test: `backend/adapters/test_common.py`

**Interfaces:**
- Produces: `backend.adapters._common.{TICKET_RE, _NON_TICKET_PREFIXES, GIT_COMMIT_RE, GH_PR_RE,
  GIT_PUSH_RE, GIT_REMOTE_RE, COMMIT_HASH_RE, PR_URL_RE, GITHUB_REPO_RE}` (compiled regexes /
  frozenset), `_extract_tickets(text: str) -> list[str]`, `_parse_ts(ts_str: str) -> datetime`,
  `_get_user() -> str`. Task 2's `codex.py` imports all of these.

- [ ] **Step 0: Install pytest into the project's venv**

The backend has zero test coverage today (matches the open AI-17 ticket), so `.venv` (the
project's virtualenv, already containing `fastapi`/`sqlmodel`/etc. per `pyproject.toml`) has never
needed `pytest` before. The bare system `python3` used for the collector's tests does NOT have
`fastapi`/`sqlmodel` installed, so backend tests must run through `.venv` specifically.

Run: `.venv/bin/pip3 install pytest`
Expected: `Successfully installed pytest-<version>` (or similar; already-satisfied is fine on a
re-run).

- [ ] **Step 1: Write the failing tests**

Create `backend/adapters/test_common.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest backend/adapters/test_common.py -v` (from repo root)
Expected: `ModuleNotFoundError: No module named 'backend.adapters._common'` (collection error —
the module doesn't exist yet).

- [ ] **Step 3: Create `backend/adapters/_common.py`**

```python
import os
import subprocess
import re
from datetime import datetime

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
```

- [ ] **Step 4: Update `backend/adapters/claude_code.py` to import from the shared module**

Replace `backend/adapters/claude_code.py:1-24` (everything from the top of the file through the
`GITHUB_REPO_RE` definition):

```python
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
```

with:

```python
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional
from backend.models import AgentRun
from backend.adapters._common import (
    GIT_COMMIT_RE,
    GH_PR_RE,
    GIT_PUSH_RE,
    GIT_REMOTE_RE,
    COMMIT_HASH_RE,
    PR_URL_RE,
    GITHUB_REPO_RE,
    _extract_tickets,
    _parse_ts,
    _get_user,
)
```

`re`, `os`, and `subprocess` are no longer used anywhere in this file after this change — they
were only needed for the regex definitions and the two functions (`_extract_tickets`, `_get_user`)
being removed below. **`datetime` must be kept** — confirmed via
`grep -n "datetime\." backend/adapters/claude_code.py`: `parse_transcript_content` itself still
calls `datetime.utcnow()` directly at two points (the running/done status check, and the
`started_at` fallback) — only `_parse_ts` (which also used `datetime`, moving to `_common.py`) is
being removed, not every use of `datetime` in the file.

Now remove the three functions that moved to `_common.py`. Delete this block (originally at
`backend/adapters/claude_code.py:205-235`, three lines above where `_get_user` used to start —
exact line numbers will have shifted after Step 4's edit above, so locate by content):

```python
def _parse_ts(ts_str: str) -> datetime:
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()
```

and:

```python
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
```

and:

```python
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
```

Leave `_extract_text` exactly where it is (it stays in `claude_code.py` — it hardcodes Claude
Code's specific `content` shape and isn't reusable as-is by Codex's differently-shaped content).

After this edit, `backend/adapters/claude_code.py` still has exactly one `import re` need: check
whether any remaining code in the file (outside what was just removed) uses `re.` directly — if
not, the `import re` at the top can be dropped too (already excluded from the Step 4 replacement
above). Verify with `grep -n "re\.\|os\.\|subprocess\." backend/adapters/claude_code.py` — any
remaining hits outside of variable names like `github_repo` (which merely contain the substring
"re") indicate a real usage that must stay imported; `git config`/`subprocess.run` and `os.environ`
were only used in the now-removed `_get_user`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest backend/adapters/test_common.py -v`
Expected: `9 passed`

- [ ] **Step 6: Verify claude_code.py still imports cleanly**

Run: `.venv/bin/python -c "import backend.adapters.claude_code"`
Expected: no output, no error (confirms the refactor didn't break the existing module's imports).

- [ ] **Step 7: Commit**

```bash
git add backend/adapters/_common.py backend/adapters/claude_code.py backend/adapters/test_common.py
git commit -m "refactor: extract shared ticket/commit/PR extraction into backend/adapters/_common.py (AI-46)"
```

---

### Task 2: Codex adapter

**Files:**
- Create: `backend/adapters/codex.py`
- Test: `backend/adapters/test_codex.py`

**Interfaces:**
- Consumes: `backend.adapters._common.{GIT_COMMIT_RE, GH_PR_RE, GIT_PUSH_RE, GIT_REMOTE_RE,
  COMMIT_HASH_RE, PR_URL_RE, GITHUB_REPO_RE, _extract_tickets, _parse_ts, _get_user}` from Task 1.
- Produces: `parse_transcript_content(content: str, mtime: Optional[float] = None, parent_id:
  Optional[str] = None, agent_id: Optional[str] = None) -> Optional[AgentRun]` — same signature
  shape as `claude_code.parse_transcript_content`. Task 3's dispatch registry calls this directly.
  (`parse_transcript`/`scan_all_transcripts`, which `claude_code.py` has for `watcher.py`'s
  local-only scan loop, are intentionally NOT implemented here — nothing in this plan calls them,
  since `watcher.py` stays Claude-Code-only per the Global Constraints.)

- [ ] **Step 1: Write the failing tests**

Create `backend/adapters/test_codex.py`:

```python
import json

from backend.adapters.codex import parse_transcript_content


def _line(d: dict) -> str:
    return json.dumps(d)


def _sample_session(*, second_token_count_is_higher=True) -> str:
    """A minimal, schema-accurate synthetic Codex CLI session transcript."""
    lines = [
        _line({
            "timestamp": "2026-04-16T16:01:55.734Z",
            "type": "session_meta",
            "payload": {
                "id": "019d9707-10b9-7a42-ba47-8daf19e3639a",
                "timestamp": "2026-04-16T16:01:55.696Z",
                "cwd": "/Users/gromano/repos/ai_dash",
                "originator": "codex_cli_rs",
                "cli_version": "0.46.0",
                "source": "cli",
                "git": {
                    "commit_hash": "ab417a61cf25fbeb672db48c0ca9895ad923fc50",
                    "branch": "feat/ai-46-codex-adapter",
                    "repository_url": "git@github.com:gromano-ltx/ai_dash.git",
                },
            },
        }),
        _line({
            "timestamp": "2026-04-16T16:01:55.750Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": "<environment_context>\n  <cwd>/Users/gromano/repos/ai_dash</cwd>\n</environment_context>",
                }],
            },
        }),
        _line({
            "timestamp": "2026-04-16T16:01:56.000Z",
            "type": "turn_context",
            "payload": {"cwd": "/Users/gromano/repos/ai_dash", "model": "gpt-5-codex", "summary": "auto"},
        }),
        _line({
            "timestamp": "2026-04-16T16:01:57.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Fix the AI-46 ingestion bug please"}],
            },
        }),
        _line({
            "timestamp": "2026-04-16T16:02:00.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "arguments": json.dumps({"command": ["bash", "-lc", "git commit -am 'fix bug'"]}),
                "call_id": "call_commit_1",
            },
        }),
        _line({
            "timestamp": "2026-04-16T16:02:01.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_commit_1",
                "output": json.dumps({"output": "[feat/ai-46-codex-adapter abc1234] fix bug\n", "metadata": {"exit_code": 0}}),
            },
        }),
        _line({
            "timestamp": "2026-04-16T16:02:05.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "arguments": json.dumps({"command": ["bash", "-lc", "gh pr create --title x --body y"]}),
                "call_id": "call_pr_1",
            },
        }),
        _line({
            "timestamp": "2026-04-16T16:02:06.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_pr_1",
                "output": json.dumps({"output": "https://github.com/gromano-ltx/ai_dash/pull/32\n", "metadata": {"exit_code": 0}}),
            },
        }),
        _line({
            "timestamp": "2026-04-16T16:02:10.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 0,
                        "output_tokens": 50,
                        "reasoning_output_tokens": 20,
                        "total_tokens": 1050,
                    },
                    "last_token_usage": {"input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 50, "reasoning_output_tokens": 20, "total_tokens": 1050},
                    "model_context_window": 272000,
                },
                "rate_limits": {"primary": None, "secondary": None},
            },
        }),
        _line({
            "timestamp": "2026-04-16T16:02:20.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 3010 if second_token_count_is_higher else 500,
                        "cached_input_tokens": 0,
                        "output_tokens": 128 if second_token_count_is_higher else 10,
                        "reasoning_output_tokens": 64,
                        "total_tokens": 3138 if second_token_count_is_higher else 510,
                    },
                    "last_token_usage": {},
                    "model_context_window": 272000,
                },
                "rate_limits": {"primary": None, "secondary": None},
            },
        }),
    ]
    return "\n".join(lines) + "\n"


def test_parse_transcript_content_returns_none_for_empty_content():
    assert parse_transcript_content("") is None


def test_parse_transcript_content_sets_provider_openai():
    run = parse_transcript_content(_sample_session())
    assert run.provider == "openai"


def test_parse_transcript_content_uses_model_from_turn_context():
    run = parse_transcript_content(_sample_session())
    assert run.model == "gpt-5-codex"


def test_parse_transcript_content_uses_last_token_count_not_summed():
    run = parse_transcript_content(_sample_session())
    # Last token_count event has input=3010/output=128 — must NOT be
    # 1000+3010=4010 (summed); summing would wildly over-count since each
    # event already carries the whole-session-so-far cumulative total.
    assert run.input_tokens == 3010
    assert run.output_tokens == 128


def test_parse_transcript_content_extracts_commit_hash():
    run = parse_transcript_content(_sample_session())
    assert run.git_commits == ["abc1234"]


def test_parse_transcript_content_extracts_pr_url():
    run = parse_transcript_content(_sample_session())
    assert run.git_prs == ["https://github.com/gromano-ltx/ai_dash/pull/32"]


def test_parse_transcript_content_extracts_ticket_ref():
    run = parse_transcript_content(_sample_session())
    assert "AI-46" in run.ticket_refs


def test_parse_transcript_content_skips_environment_context_for_label():
    run = parse_transcript_content(_sample_session())
    # The first user message is <environment_context>...</environment_context>;
    # the label must come from the real second user message instead.
    assert "environment_context" not in run.label
    assert "Fix the AI-46" in run.label


def test_parse_transcript_content_uses_session_id_as_run_id():
    run = parse_transcript_content(_sample_session())
    assert run.id == "019d9707-10b9-7a42-ba47-8daf19e3639a"


def test_parse_transcript_content_status_done_when_mtime_is_old():
    import time
    run = parse_transcript_content(_sample_session(), mtime=time.time() - 3600)
    assert run.status == "done"
    assert run.ended_at is not None


def test_parse_transcript_content_status_running_when_mtime_is_recent():
    import time
    run = parse_transcript_content(_sample_session(), mtime=time.time())
    assert run.status == "running"
    assert run.ended_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest backend/adapters/test_codex.py -v`
Expected: `ModuleNotFoundError: No module named 'backend.adapters.codex'` (collection error).

- [ ] **Step 3: Create `backend/adapters/codex.py`**

```python
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.models import AgentRun
from backend.adapters._common import (
    GIT_COMMIT_RE,
    GH_PR_RE,
    GIT_PUSH_RE,
    GIT_REMOTE_RE,
    COMMIT_HASH_RE,
    PR_URL_RE,
    GITHUB_REPO_RE,
    _extract_tickets,
    _parse_ts,
    _get_user,
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
                    args = json.loads(payload.get('arguments', '{}'))
                except json.JSONDecodeError:
                    args = {}
                cmd_list = args.get('command', [])
                cmd = ' '.join(cmd_list) if isinstance(cmd_list, list) else str(cmd_list)
                if cmd:
                    bash_commands.append(cmd)
                    if GIT_COMMIT_RE.search(cmd) and call_id:
                        pending_commit_ids.add(call_id)
                    if GH_PR_RE.search(cmd) and call_id:
                        pending_pr_ids.add(call_id)
                    if (GIT_PUSH_RE.search(cmd) or GIT_REMOTE_RE.search(cmd)) and call_id:
                        pending_remote_ids.add(call_id)

            elif ptype == 'function_call_output':
                call_id = payload.get('call_id', '')
                raw_output = payload.get('output', '')
                try:
                    output = json.loads(raw_output).get('output', '') if raw_output else ''
                except (json.JSONDecodeError, AttributeError):
                    output = raw_output if isinstance(raw_output, str) else ''

                if call_id in pending_remote_ids and not github_repo:
                    m = GITHUB_REPO_RE.search(output)
                    if m:
                        github_repo = f"https://github.com/{m.group(1)}"
                    pending_remote_ids.discard(call_id)
                if call_id in pending_commit_ids:
                    git_commits.extend(COMMIT_HASH_RE.findall(output))
                    pending_commit_ids.discard(call_id)
                if call_id in pending_pr_ids:
                    pr_urls = PR_URL_RE.findall(output)
                    git_prs.extend(pr_urls)
                    if pr_urls and not github_repo:
                        repo_m = GITHUB_REPO_RE.match(pr_urls[0])
                        if repo_m:
                            github_repo = f"https://github.com/{repo_m.group(1)}"
                    pending_pr_ids.discard(call_id)

        elif etype == 'event_msg' and payload.get('type') == 'token_count':
            info = payload.get('info')
            if info:
                usage = info.get('total_token_usage', {})
                input_tokens = usage.get('input_tokens', input_tokens)
                output_tokens = usage.get('output_tokens', output_tokens)

    if not session_id:
        session_id = next(
            (e.get('payload', {}).get('id') for e in events if e.get('type') == 'session_meta'),
            None,
        )

    run_id = (f"agent-{agent_id}" if agent_id else None) or session_id or str(uuid.uuid4())

    if mtime is not None:
        status = "running" if (datetime.utcnow().timestamp() - mtime) < 300 else "done"
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest backend/adapters/test_codex.py -v`
Expected: `11 passed`

- [ ] **Step 5: Run the full backend adapter test suite together**

Run: `.venv/bin/python -m pytest backend/adapters/ -v`
Expected: `20 passed` (9 from `test_common.py` + 11 from `test_codex.py`)

- [ ] **Step 6: Commit**

```bash
git add backend/adapters/codex.py backend/adapters/test_codex.py
git commit -m "feat: add Codex CLI transcript adapter, labeled provider=openai (AI-46)"
```

---

### Task 3: Backend provider dispatch

**Files:**
- Modify: `backend/api/routes.py:12` (import), `backend/api/routes.py:15` (near `PROVIDERS`),
  `backend/api/routes.py:192-238` (`ingest_transcript`)
- Test: `backend/api/test_routes.py`

**Interfaces:**
- Consumes: `backend.adapters.claude_code.parse_transcript_content` (existing),
  `backend.adapters.codex.parse_transcript_content` (Task 2).
- Produces: `backend.api.routes.PROVIDER_ADAPTERS: dict[str, Callable]` and
  `backend.api.routes._select_parser(provider: str) -> Callable` — Task 4 (collector) doesn't
  import these directly (it just sends the `X-Provider` header), but this is the exact dispatch
  logic that header drives.

- [ ] **Step 1: Write the failing tests**

Create `backend/api/test_routes.py`:

```python
from backend.adapters import claude_code, codex
from backend.api.routes import _select_parser


def test_select_parser_dispatches_anthropic():
    assert _select_parser("anthropic") is claude_code.parse_transcript_content


def test_select_parser_dispatches_openai():
    assert _select_parser("openai") is codex.parse_transcript_content


def test_select_parser_defaults_to_claude_code_for_unknown_provider():
    assert _select_parser("gemini") is claude_code.parse_transcript_content
    assert _select_parser("bogus") is claude_code.parse_transcript_content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest backend/api/test_routes.py -v`
Expected: `ImportError: cannot import name '_select_parser' from 'backend.api.routes'`

- [ ] **Step 3: Update the import and add the dispatch registry**

Replace `backend/api/routes.py:12`:

```python
from backend.adapters.claude_code import parse_transcript_content
```

with:

```python
from backend.adapters import claude_code, codex
```

Replace `backend/api/routes.py:15` (the `PROVIDERS` line):

```python
PROVIDERS = ("anthropic", "openai", "gemini")
```

with:

```python
PROVIDERS = ("anthropic", "openai", "gemini")
PROVIDER_ADAPTERS = {
    "anthropic": claude_code.parse_transcript_content,
    "openai": codex.parse_transcript_content,
}


def _select_parser(provider: str):
    return PROVIDER_ADAPTERS.get(provider, claude_code.parse_transcript_content)
```

- [ ] **Step 4: Wire the header into `ingest_transcript`**

Replace this line in the `ingest_transcript` function signature (`backend/api/routes.py`, in the
`@router.post("/v1/ingest")` handler):

```python
    x_file_mtime: Optional[float] = Header(None),
    session: Session = Depends(get_session),
```

with:

```python
    x_file_mtime: Optional[float] = Header(None),
    x_provider: str = Header("anthropic"),
    session: Session = Depends(get_session),
```

Replace this line later in the same function:

```python
    run = parse_transcript_content(content, mtime=x_file_mtime)
```

with:

```python
    parse_fn = _select_parser(x_provider)
    run = parse_fn(content, mtime=x_file_mtime)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest backend/api/test_routes.py -v`
Expected: `3 passed`

- [ ] **Step 6: Confirm the backend still imports cleanly**

Run: `.venv/bin/python -c "import backend.main"`
Expected: no output, no error.

- [ ] **Step 7: Commit**

```bash
git add backend/api/routes.py backend/api/test_routes.py
git commit -m "feat: dispatch /v1/ingest to the right adapter based on X-Provider header (AI-46)"
```

---

### Task 4: Collector multi-source support

**Files:**
- Modify: `collector/collector.py` (throughout — see steps below)
- Modify: `collector/test_collector.py` (fix the one existing test that references the
  now-renamed `TRANSCRIPTS_BASE`, plus new tests)

**Interfaces:**
- Consumes: nothing from Tasks 1-3 directly (collector/ and backend/ are separate processes with
  no import dependency) — but functionally depends on Task 3 already being deployed, since a
  collector shipping `X-Provider: openai` against a backend that doesn't yet have the `"openai"`
  key in `PROVIDER_ADAPTERS` would have every Codex session mis-parsed by the Claude Code adapter
  and likely rejected with a 422. This is naturally satisfied as long as all four tasks merge and
  deploy together.
- Produces: `SOURCES: dict[str, Path]`, `_provider_for_path(path: Path) -> str` — nothing later in
  this plan consumes these (this is the final task), but they're the collector's half of the
  contract Task 3 established.

- [ ] **Step 1: Write the failing tests**

Append to `collector/test_collector.py`. `asyncio` and `json` are already imported earlier in this
file (from the AI-6 collector-reliability work) — no new imports needed for these tests.

```python
def test_provider_for_path_resolves_correct_source(tmp_path, monkeypatch):
    anthropic_dir = tmp_path / "claude"
    openai_dir = tmp_path / "codex"
    monkeypatch.setattr(collector_mod, "SOURCES", {"anthropic": anthropic_dir, "openai": openai_dir})

    assert collector_mod._provider_for_path(anthropic_dir / "sub" / "file.jsonl") == "anthropic"
    assert collector_mod._provider_for_path(openai_dir / "file.jsonl") == "openai"


def test_provider_for_path_defaults_to_anthropic_for_unrecognized_path(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_mod, "SOURCES", {"anthropic": tmp_path / "claude"})
    assert collector_mod._provider_for_path(tmp_path / "elsewhere" / "file.jsonl") == "anthropic"


def test_sync_all_stdlib_skips_missing_sources(tmp_path, monkeypatch):
    existing = tmp_path / "exists"
    existing.mkdir()
    missing = tmp_path / "missing"
    monkeypatch.setattr(collector_mod, "SOURCES", {"anthropic": existing, "openai": missing})
    monkeypatch.setattr(collector_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(collector_mod, "STATE_FILE", tmp_path / "state.json")

    result = collector_mod._sync_all_stdlib("https://example.test", "test-key", {})
    assert result == {}


def test_ship_urllib_sends_x_provider_header(tmp_path, monkeypatch):
    f = tmp_path / "session.jsonl"
    f.write_text("hello world")

    captured = {}

    class FakeResponse:
        def read(self):
            return json.dumps({"id": "abc", "status": "done"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, context=None, timeout=None):
        captured["provider"] = req.get_header("X-provider")
        return FakeResponse()

    monkeypatch.setattr(collector_mod.urllib.request, "urlopen", fake_urlopen)

    ok, new_offset, _ = collector_mod._ship_urllib(
        f, "https://example.test", "test-key", "openai", offset=0, mtime=1.0
    )

    assert ok
    assert captured["provider"] == "openai"


def test_sync_all_dispatches_correct_provider_per_source(tmp_path, monkeypatch):
    anthropic_dir = tmp_path / "claude"
    anthropic_dir.mkdir()
    (anthropic_dir / "session1.jsonl").write_text("data1")
    openai_dir = tmp_path / "codex"
    openai_dir.mkdir()
    (openai_dir / "session2.jsonl").write_text("data2")

    monkeypatch.setattr(collector_mod, "SOURCES", {"anthropic": anthropic_dir, "openai": openai_dir})

    calls = []

    async def fake_ship(path, url, key, provider, client, offset=0, mtime=0.0):
        calls.append((path.name, provider))
        return True, len(path.read_text()), 10

    monkeypatch.setattr(collector_mod, "ship", fake_ship)

    class FakeClient:
        pass

    asyncio.run(collector_mod.sync_all("https://example.test", "test-key", {}, FakeClient()))

    assert ("session1.jsonl", "anthropic") in calls
    assert ("session2.jsonl", "openai") in calls
```

Also fix the one existing test that references the constant being renamed. In
`collector/test_collector.py`, replace this line inside
`test_watch_falls_back_to_polling_on_awatch_runtime_failure`:

```python
    monkeypatch.setattr(collector_mod, "TRANSCRIPTS_BASE", tmp_path)
```

with:

```python
    monkeypatch.setattr(collector_mod, "SOURCES", {"anthropic": tmp_path})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest collector/test_collector.py -v` (from repo root)
Expected: `AttributeError: <module 'collector.collector' ...> does not have the attribute
'SOURCES'` for the new tests and the fixed existing test; `_provider_for_path`/`_sync_all_stdlib`
calls with a `provider` argument also fail since the current signatures don't accept one yet.

- [ ] **Step 3: Replace `TRANSCRIPTS_BASE` with a `SOURCES` registry**

Replace `collector/collector.py:31`:

```python
TRANSCRIPTS_BASE = Path.home() / ".claude" / "projects"
```

with:

```python
SOURCES = {
    "anthropic": Path.home() / ".claude" / "projects",
    "openai": Path.home() / ".codex" / "sessions",
}


def _provider_for_path(path: Path) -> str:
    for provider, base in SOURCES.items():
        try:
            path.relative_to(base)
            return provider
        except ValueError:
            continue
    return "anthropic"
```

- [ ] **Step 4: Add a `provider` parameter to `_ship_urllib` and send the `X-Provider` header**

Replace `collector/collector.py`'s `_ship_urllib` signature and header block:

```python
def _ship_urllib(
    path: Path, url: str, key: str, offset: int = 0, mtime: float = 0.0
) -> tuple[bool, int, int]:
```

with:

```python
def _ship_urllib(
    path: Path, url: str, key: str, provider: str, offset: int = 0, mtime: float = 0.0
) -> tuple[bool, int, int]:
```

Replace:

```python
    req.add_header("X-Session-Id", path.stem)
    req.add_header("X-File-Offset", str(offset))
    req.add_header("X-File-Mtime", str(mtime))
```

with:

```python
    req.add_header("X-Session-Id", path.stem)
    req.add_header("X-File-Offset", str(offset))
    req.add_header("X-File-Mtime", str(mtime))
    req.add_header("X-Provider", provider)
```

Replace the recursive resend-from-0 call:

```python
            return _ship_urllib(path, url, key, offset=0, mtime=mtime)
```

with:

```python
            return _ship_urllib(path, url, key, provider, offset=0, mtime=mtime)
```

- [ ] **Step 5: Update `_sync_all_stdlib` to iterate all sources**

Replace the full body of `_sync_all_stdlib`:

```python
def _sync_all_stdlib(url: str, key: str, state: dict) -> dict:
    if not TRANSCRIPTS_BASE.exists():
        return state

    total_raw = total_gz = 0
    for path in TRANSCRIPTS_BASE.rglob("*.jsonl"):
        try:
            stat = path.stat()
            mtime, size = stat.st_mtime, stat.st_size
        except Exception:
            continue

        key_str = str(path)
        entry = state.get(key_str, {"mtime": 0, "offset": 0})
        if entry["mtime"] == mtime and entry["offset"] == size:
            continue

        offset = entry["offset"] if size >= entry["offset"] else 0

        # Three attempts with backoff for transient network errors
        for attempt in range(3):
            ok, new_offset, gz_len = _ship_urllib(path, url, key, offset, mtime)
            if ok:
                total_raw += new_offset - offset
                total_gz += gz_len
                state[key_str] = {"mtime": mtime, "offset": new_offset}
                break
            if attempt < 2:
                time.sleep(2 ** attempt)

    if total_raw:
        ratio = (1 - total_gz / total_raw) * 100
        logger.info(f"sync complete — {total_raw:,}B raw → {total_gz:,}B gz ({ratio:.0f}% reduction)")
    return state
```

with:

```python
def _sync_all_stdlib(url: str, key: str, state: dict) -> dict:
    total_raw = total_gz = 0
    for provider, base in SOURCES.items():
        if not base.exists():
            continue
        for path in base.rglob("*.jsonl"):
            try:
                stat = path.stat()
                mtime, size = stat.st_mtime, stat.st_size
            except Exception:
                continue

            key_str = str(path)
            entry = state.get(key_str, {"mtime": 0, "offset": 0})
            if entry["mtime"] == mtime and entry["offset"] == size:
                continue

            offset = entry["offset"] if size >= entry["offset"] else 0

            # Three attempts with backoff for transient network errors
            for attempt in range(3):
                ok, new_offset, gz_len = _ship_urllib(path, url, key, provider, offset, mtime)
                if ok:
                    total_raw += new_offset - offset
                    total_gz += gz_len
                    state[key_str] = {"mtime": mtime, "offset": new_offset}
                    break
                if attempt < 2:
                    time.sleep(2 ** attempt)

    if total_raw:
        ratio = (1 - total_gz / total_raw) * 100
        logger.info(f"sync complete — {total_raw:,}B raw → {total_gz:,}B gz ({ratio:.0f}% reduction)")
    return state
```

- [ ] **Step 6: Update `_watch_poll`'s log messages for multiple sources**

Replace:

```python
def _watch_poll(url: str, key: str, interval: int = 10):
    state = load_state()
    logger.info(f"starting (polling every {interval}s) — syncing to {url}")
    state = _sync_all_stdlib(url, key, state)
    save_state(state)

    if not TRANSCRIPTS_BASE.exists():
        logger.warning(f"{TRANSCRIPTS_BASE} not found, will watch once it appears")

    logger.info(f"watching {TRANSCRIPTS_BASE}")
```

with:

```python
def _watch_poll(url: str, key: str, interval: int = 10):
    state = load_state()
    logger.info(f"starting (polling every {interval}s) — syncing to {url}")
    state = _sync_all_stdlib(url, key, state)
    save_state(state)

    existing = [base for base in SOURCES.values() if base.exists()]
    if not existing:
        logger.warning(f"none of {list(SOURCES.values())} found, will watch once one appears")

    logger.info(f"watching {existing or list(SOURCES.values())}")
```

- [ ] **Step 7: Add a `provider` parameter to `ship` and send the `X-Provider` header**

Replace the `ship` signature:

```python
async def ship(
    path: Path, url: str, key: str, client, offset: int = 0, mtime: float = 0.0
) -> tuple[bool, int, int]:
```

with:

```python
async def ship(
    path: Path, url: str, key: str, provider: str, client, offset: int = 0, mtime: float = 0.0
) -> tuple[bool, int, int]:
```

Replace the headers dict:

```python
                headers={
                    "X-API-Key": key,
                    "Content-Type": "text/plain",
                    "Content-Encoding": "gzip",
                    "X-Session-Id": path.stem,
                    "X-File-Offset": str(offset),
                    "X-File-Mtime": str(mtime),
                },
```

with:

```python
                headers={
                    "X-API-Key": key,
                    "Content-Type": "text/plain",
                    "Content-Encoding": "gzip",
                    "X-Session-Id": path.stem,
                    "X-File-Offset": str(offset),
                    "X-File-Mtime": str(mtime),
                    "X-Provider": provider,
                },
```

Replace the recursive resend-from-0 call:

```python
                return await ship(path, url, key, client, offset=0, mtime=mtime)
```

with:

```python
                return await ship(path, url, key, provider, client, offset=0, mtime=mtime)
```

- [ ] **Step 8: Update `sync_all` (async) to iterate all sources**

Replace the full body of `sync_all`:

```python
async def sync_all(url: str, key: str, state: dict, client) -> dict:
    if not TRANSCRIPTS_BASE.exists():
        return state

    total_raw = total_gz = 0
    for path in TRANSCRIPTS_BASE.rglob("*.jsonl"):
        try:
            stat = path.stat()
            mtime, size = stat.st_mtime, stat.st_size
        except Exception:
            continue

        key_str = str(path)
        entry = state.get(key_str, {"mtime": 0, "offset": 0})
        if entry["mtime"] == mtime and entry["offset"] == size:
            continue

        offset = entry["offset"] if size >= entry["offset"] else 0
        ok, new_offset, gz_len = await ship(path, url, key, client, offset, mtime)
        if ok:
            total_raw += new_offset - offset
            total_gz += gz_len
            state[key_str] = {"mtime": mtime, "offset": new_offset}

    if total_raw:
        ratio = (1 - total_gz / total_raw) * 100
        logger.info(f"sync complete — {total_raw:,}B raw → {total_gz:,}B gz ({ratio:.0f}% reduction)")
    return state
```

with:

```python
async def sync_all(url: str, key: str, state: dict, client) -> dict:
    total_raw = total_gz = 0
    for provider, base in SOURCES.items():
        if not base.exists():
            continue
        for path in base.rglob("*.jsonl"):
            try:
                stat = path.stat()
                mtime, size = stat.st_mtime, stat.st_size
            except Exception:
                continue

            key_str = str(path)
            entry = state.get(key_str, {"mtime": 0, "offset": 0})
            if entry["mtime"] == mtime and entry["offset"] == size:
                continue

            offset = entry["offset"] if size >= entry["offset"] else 0
            ok, new_offset, gz_len = await ship(path, url, key, provider, client, offset, mtime)
            if ok:
                total_raw += new_offset - offset
                total_gz += gz_len
                state[key_str] = {"mtime": mtime, "offset": new_offset}

    if total_raw:
        ratio = (1 - total_gz / total_raw) * 100
        logger.info(f"sync complete — {total_raw:,}B raw → {total_gz:,}B gz ({ratio:.0f}% reduction)")
    return state
```

- [ ] **Step 9: Update `watch` (async) to watch multiple directories and resolve provider per file**

Replace the full body of `watch`:

```python
async def watch(url: str, key: str):
    import httpx
    from watchfiles import awatch

    state = load_state()
    async with httpx.AsyncClient() as client:
        logger.info(f"starting — syncing existing transcripts to {url}")
        state = await sync_all(url, key, state, client)
        save_state(state)

        if not TRANSCRIPTS_BASE.exists():
            logger.warning(f"{TRANSCRIPTS_BASE} not found, will watch once it appears")
            return

        logger.info(f"watching {TRANSCRIPTS_BASE}")
        try:
            async for changes in awatch(str(TRANSCRIPTS_BASE)):
                changed = {Path(p) for _, p in changes if p.endswith(".jsonl")}
                for path in changed:
                    try:
                        stat = path.stat()
                        mtime, size = stat.st_mtime, stat.st_size
                    except Exception:
                        continue
                    key_str = str(path)
                    entry = state.get(key_str, {"mtime": 0, "offset": 0})
                    offset = entry["offset"] if size >= entry["offset"] else 0
                    ok, new_offset, _ = await ship(path, url, key, client, offset, mtime)
                    if ok:
                        state[key_str] = {"mtime": mtime, "offset": new_offset}
                        save_state(state)
        except Exception as exc:
            logger.error(f"watchfiles failed at runtime ({exc}), falling back to stdlib polling")
            _watch_poll(url, key)
```

with:

```python
async def watch(url: str, key: str):
    import httpx
    from watchfiles import awatch

    state = load_state()
    async with httpx.AsyncClient() as client:
        logger.info(f"starting — syncing existing transcripts to {url}")
        state = await sync_all(url, key, state, client)
        save_state(state)

        existing_sources = [base for base in SOURCES.values() if base.exists()]
        if not existing_sources:
            logger.warning(f"none of {list(SOURCES.values())} found, will watch once one appears")
            return

        logger.info(f"watching {existing_sources}")
        try:
            async for changes in awatch(*[str(b) for b in existing_sources]):
                changed = {Path(p) for _, p in changes if p.endswith(".jsonl")}
                for path in changed:
                    try:
                        stat = path.stat()
                        mtime, size = stat.st_mtime, stat.st_size
                    except Exception:
                        continue
                    key_str = str(path)
                    entry = state.get(key_str, {"mtime": 0, "offset": 0})
                    offset = entry["offset"] if size >= entry["offset"] else 0
                    provider = _provider_for_path(path)
                    ok, new_offset, _ = await ship(path, url, key, provider, client, offset, mtime)
                    if ok:
                        state[key_str] = {"mtime": mtime, "offset": new_offset}
                        save_state(state)
        except Exception as exc:
            logger.error(f"watchfiles failed at runtime ({exc}), falling back to stdlib polling")
            _watch_poll(url, key)
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `python3 -m pytest collector/test_collector.py -v`
Expected: `11 passed` (6 existing + 5 new; the one existing fallback test still passes with its
`SOURCES` monkeypatch fix from Step 1)

- [ ] **Step 11: Manual verification — both sources watched on this real machine**

```bash
python3 -c "
import collector.collector as c
print('SOURCES:', c.SOURCES)
for provider, base in c.SOURCES.items():
    print(f'{provider}: {base} exists={base.exists()}')
"
```

Expected: prints both `anthropic` (`~/.claude/projects`) and `openai` (`~/.codex/sessions`)
entries, both showing `exists=True` on this machine (both directories were confirmed present
during the design phase).

- [ ] **Step 12: Commit**

```bash
git add collector/collector.py collector/test_collector.py
git commit -m "feat: collector watches and ships from multiple provider sources (AI-46)"
```

---

## Self-Review

**Spec coverage:** Shared extraction module → Task 1. Codex adapter parsing logic (run
identity/timing, model, cumulative-token handling, commit/PR extraction via call-id pairing,
environment-context skip, ticket refs) → Task 2. Backend `X-Provider` dispatch registry → Task 3.
Collector `SOURCES` registry + `X-Provider` header sending + multi-directory watch → Task 4. All
testing/error-handling bullets from the spec are covered by each task's test steps.

**Placeholder scan:** No TBD/TODO; every step shows complete code, exact diffs, or exact commands
with expected output.

**Type consistency:** `parse_transcript_content(content: str, mtime: Optional[float] = None,
parent_id: Optional[str] = None, agent_id: Optional[str] = None) -> Optional[AgentRun]` is
identical between `claude_code.py` (existing) and `codex.py` (Task 2), matching what Task 3's
`PROVIDER_ADAPTERS` registry expects from both. `_ship_urllib`/`ship`'s new `provider: str`
parameter is inserted in the same position (right after `key`) in both the sync and async paths,
and every call site (including the two recursive resend-from-0 calls and both `_sync_all_stdlib`/
`sync_all` call sites) is updated consistently in Task 4.
