# AI-47 Gemini CLI Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest real Gemini CLI coding-agent sessions into the dashboard, labeled `provider="gemini"`, by adding a new adapter and one new source/dispatch entry to the pipeline AI-46 already generalized.

**Architecture:** A new `backend/adapters/gemini_cli.py` parses Gemini CLI's hybrid checkpoint/event JSONL format into the shared `AgentRun` shape, reusing `backend/adapters/_common.py`'s regex/extraction helpers unchanged. `backend/api/routes.py` gets one new `PROVIDER_ADAPTERS` entry plus a new `X-Parent-Id` header read-through. `collector/collector.py` gets one new `SOURCES` entry plus a small path-based helper that detects Gemini's subagent-transcript path convention and ships the parent id as that new header, necessary because, unlike Claude Code, Gemini subagent transcript *content* never records its own parent session id, and the backend's `/v1/ingest` endpoint never sees the original file path (only content + a few headers).

**Tech Stack:** Python 3.12, FastAPI, SQLModel, pytest, watchfiles/httpx (collector).

## Global Constraints

- Target is Gemini CLI specifically (confirmed installed, v0.49.0), not Antigravity (a separate
  Google agentic-IDE product also rooted under `~/.gemini/`), which is out of scope for this ticket.
- Token mapping: sum `tokens.input` across deduped `"gemini"`-type events → `input_tokens`; sum
  `(tokens.output + tokens.thoughts + tokens.tool)` → `output_tokens`. Verified against real local
  data that `total = input + output + thoughts + tool` and `cached` is a subset of `input`, not
  additive.
- Real sessions on this machine log the same message `id` twice in a row (a debounced-write
  artifact of the checkpoint format); dedupe by `id` before accumulating tokens/text/tool calls.
- `meta.git_branch` and `meta.cwd` are always `None` for this adapter: no equivalent field exists
  in Gemini CLI transcript content, and (per a planning-time decision, see Task 2) the backend never
  receives the original file path needed to read a sibling `.project_root` file. Known, accepted gap;
  neither field is in this ticket's DoD.
- Subagent parent linkage requires a new `X-Parent-Id` header, populated by the collector (which has
  path access) and read by the backend (which doesn't): this is an addition beyond what
  `docs/superpowers/specs/2026-07-06-gemini-cli-adapter-design.md` originally described, discovered
  while writing this plan: that spec assumed a path-aware `parse_transcript(path)` wrapper would run
  in production, but `/v1/ingest` only ever calls `parse_transcript_content(content, mtime=...)`;
  the file path is never available backend-side.
- No filename filtering needed in the collector: `SOURCES["gemini"] = Path.home() / ".gemini" /
  "tmp"` works with the existing `base.rglob("*.jsonl")` walk; verified all 16 `.jsonl` files under
  `~/.gemini/tmp` on this machine live under a `chats/` directory.
- Shared regex/extraction helpers already live in `backend/adapters/_common.py` (added in AI-46),
  reused as-is, not duplicated.
- `backend/watcher.py`'s local-only watch loop stays Claude-Code-only, out of scope.

---

### Task 1: Gemini CLI adapter

**Files:**
- Create: `backend/adapters/gemini_cli.py`
- Test: `backend/adapters/test_gemini_cli.py`

**Interfaces:**
- Consumes: `backend.adapters._common.{_classify_shell_command, _resolve_command_output,
  _extract_tickets, _parse_ts, _get_user}` (existing, from AI-46).
- Produces: `parse_transcript_content(content: str, mtime: Optional[float] = None, parent_id:
  Optional[str] = None, agent_id: Optional[str] = None) -> Optional[AgentRun]`, identical
  signature shape to `claude_code.py`/`codex.py`. Task 2's `PROVIDER_ADAPTERS` registry calls this
  directly. (`parse_transcript`/`scan_all_transcripts`, which `claude_code.py` has for
  `watcher.py`'s local-only scan loop, are intentionally NOT implemented here, matching `codex.py`'s
  precedent; nothing in this plan calls them.)

- [ ] **Step 1: Write the failing tests**

Create `backend/adapters/test_gemini_cli.py`:

```python
import json

from backend.adapters.gemini_cli import parse_transcript_content


def _line(d: dict) -> str:
    return json.dumps(d)


def _sample_session() -> str:
    """A minimal, schema-accurate synthetic Gemini CLI session transcript.

    Real Gemini CLI JSONL is a hybrid checkpoint/event log: the first line is a
    header (sessionId/startTime/kind, no "type" key), most lines are standalone
    events with a top-level "type" ("user"/"gemini"/"info"), and one early line
    wraps the very first real message inside {"$set": {"messages": [...]}}.
    Later housekeeping "$set" lines (lastUpdated-only, or the final
    summary/memoryScratchpad line) carry no "messages" key and are ignored.
    """
    lines = [
        # Header line: no "type" key, no "$set" key.
        _line({
            "sessionId": "06ba9b64-5701-4a4e-b7eb-4bac2b449d5c",
            "projectHash": "7caa8e06c56b60fb427b988dd636bd970a01de49287b4b5f3231498ba62d6096",
            "startTime": "2026-06-29T13:49:00.000Z",
            "lastUpdated": "2026-06-29T13:49:00.000Z",
            "kind": "main",
        }),
        # First real message, wrapped in the initial $set checkpoint: injected
        # <session_context> text that must be skipped for label/task purposes.
        _line({
            "$set": {
                "messages": [{
                    "id": "d04923d38bb0f6017037e74183378ef4",
                    "timestamp": "2026-06-29T13:49:00.100Z",
                    "type": "user",
                    "content": [{"text": "<session_context>\nThis is the Gemini CLI...\n"}],
                }],
                "lastUpdated": "2026-06-29T13:49:00.100Z",
            },
        }),
        # Housekeeping $set line with no "messages" key; must be ignored, not crash.
        _line({"$set": {"lastUpdated": "2026-06-29T13:49:00.200Z"}}),
        # An "info" event: must be ignored for content extraction, but its
        # timestamp still counts toward the session's last-seen timestamp.
        _line({
            "id": "info-1",
            "timestamp": "2026-06-29T13:49:00.300Z",
            "type": "info",
            "content": "You have 1 extension with an update available.",
        }),
        # The real first user message.
        _line({
            "id": "user-1",
            "timestamp": "2026-06-29T13:49:01.000Z",
            "type": "user",
            "content": [{"text": "Fix the AI-47 ingestion bug please"}],
        }),
        # First "gemini" turn, with a shell tool call that commits; command
        # and result live together in the same object (no cross-event pairing
        # needed, unlike Claude Code/Codex).
        _line({
            "id": "gemini-1",
            "timestamp": "2026-06-29T13:49:05.000Z",
            "type": "gemini",
            "content": "",
            "model": "gemini-3.5-flash",
            "tokens": {"input": 100, "output": 10, "cached": 0, "thoughts": 5, "tool": 0, "total": 115},
            "toolCalls": [{
                "id": "run_shell_command__commit1",
                "name": "run_shell_command",
                "args": {"command": "git commit -am 'fix bug'"},
                "result": [{
                    "functionResponse": {
                        "id": "run_shell_command__commit1",
                        "name": "run_shell_command",
                        "response": {"output": "<untrusted_context>\nOutput: [main abc1234] fix bug\n</untrusted_context>"},
                    },
                }],
                "status": "success",
            }],
        }),
        # Duplicate of the exact same "gemini" event (same id): a verified
        # real debounced-write artifact. Must not be double-counted.
        _line({
            "id": "gemini-1",
            "timestamp": "2026-06-29T13:49:05.000Z",
            "type": "gemini",
            "content": "",
            "model": "gemini-3.5-flash",
            "tokens": {"input": 100, "output": 10, "cached": 0, "thoughts": 5, "tool": 0, "total": 115},
            "toolCalls": [{
                "id": "run_shell_command__commit1",
                "name": "run_shell_command",
                "args": {"command": "git commit -am 'fix bug'"},
                "result": [{
                    "functionResponse": {
                        "id": "run_shell_command__commit1",
                        "name": "run_shell_command",
                        "response": {"output": "<untrusted_context>\nOutput: [main abc1234] fix bug\n</untrusted_context>"},
                    },
                }],
                "status": "success",
            }],
        }),
        # Second "gemini" turn, with a shell tool call that opens a PR.
        _line({
            "id": "gemini-2",
            "timestamp": "2026-06-29T13:49:10.000Z",
            "type": "gemini",
            "content": "",
            "model": "gemini-3.5-flash",
            "tokens": {"input": 150, "output": 20, "cached": 50, "thoughts": 8, "tool": 2, "total": 180},
            "toolCalls": [{
                "id": "run_shell_command__pr1",
                "name": "run_shell_command",
                "args": {"command": "gh pr create --title x --body y"},
                "result": [{
                    "functionResponse": {
                        "id": "run_shell_command__pr1",
                        "name": "run_shell_command",
                        "response": {"output": "https://github.com/gromano-ltx/ai_dash/pull/33\n"},
                    },
                }],
                "status": "success",
            }],
        }),
    ]
    return "\n".join(lines) + "\n"


def test_parse_transcript_content_returns_none_for_empty_content():
    assert parse_transcript_content("") is None


def test_parse_transcript_content_sets_provider_gemini():
    run = parse_transcript_content(_sample_session())
    assert run.provider == "gemini"


def test_parse_transcript_content_uses_session_id_as_run_id():
    run = parse_transcript_content(_sample_session())
    assert run.id == "06ba9b64-5701-4a4e-b7eb-4bac2b449d5c"


def test_parse_transcript_content_uses_model_from_gemini_event():
    run = parse_transcript_content(_sample_session())
    assert run.model == "gemini-3.5-flash"


def test_parse_transcript_content_sums_input_tokens_across_turns():
    run = parse_transcript_content(_sample_session())
    # 100 (turn 1, deduped) + 150 (turn 2) = 250, NOT 100+100+150=350, which
    # would double-count the verified real duplicate-line case.
    assert run.input_tokens == 250


def test_parse_transcript_content_sums_output_plus_thoughts_plus_tool():
    run = parse_transcript_content(_sample_session())
    # Turn 1 (deduped): 10+5+0=15. Turn 2: 20+8+2=30. Total: 45.
    assert run.output_tokens == 45


def test_parse_transcript_content_extracts_commit_hash_from_combined_tool_call():
    run = parse_transcript_content(_sample_session())
    assert run.git_commits == ["abc1234"]


def test_parse_transcript_content_extracts_pr_url_from_combined_tool_call():
    run = parse_transcript_content(_sample_session())
    assert run.git_prs == ["https://github.com/gromano-ltx/ai_dash/pull/33"]


def test_parse_transcript_content_extracts_ticket_ref():
    run = parse_transcript_content(_sample_session())
    assert "AI-47" in run.ticket_refs


def test_parse_transcript_content_skips_session_context_for_label():
    run = parse_transcript_content(_sample_session())
    # The first real message (unwrapped from $set) is <session_context>...;
    # the label must come from the real second user message instead.
    assert "session_context" not in run.label
    assert "Fix the AI-47" in run.label


def test_parse_transcript_content_meta_has_no_git_branch_or_cwd():
    run = parse_transcript_content(_sample_session())
    assert run.meta["git_branch"] is None
    assert run.meta["cwd"] is None


def test_parse_transcript_content_passes_through_parent_id():
    run = parse_transcript_content(_sample_session(), parent_id="parent-session-xyz")
    assert run.parent_id == "parent-session-xyz"


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


def test_parse_transcript_content_ignores_info_events_but_counts_their_timestamp():
    # The "info" event's timestamp (13:49:00.300Z) is earlier than the last
    # "gemini" event (13:49:10.000Z), so it shouldn't change ended_at here;
    # this just confirms parsing an "info" event doesn't crash or corrupt
    # first_user_text/tokens.
    run = parse_transcript_content(_sample_session(), mtime=0.0)
    assert run is not None
    assert "update available" not in run.label
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest backend/adapters/test_gemini_cli.py -v`
Expected: `ModuleNotFoundError: No module named 'backend.adapters.gemini_cli'` (collection error).

- [ ] **Step 3: Create `backend/adapters/gemini_cli.py`**

```python
import json
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

    # Line 0 is always the header (sessionId/startTime/kind): it has neither
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

    # Real sessions on this machine log the same message id twice in a row (a
    # debounced-write artifact of the checkpoint format); dedupe before
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
            input_tokens += tokens.get('input', 0)
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
                # carries both the command and its result together: classify
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

    run_id = (f"agent-{agent_id}" if agent_id else None) or session_id or str(uuid.uuid4())

    if mtime is not None:
        status = "running" if (datetime.utcnow().timestamp() - mtime) < 300 else "done"
    else:
        status = "done"

    ended_at = last_ts if status == "done" else None

    label = (first_user_text or "")[:80] or "Gemini CLI session"
    search_text = ' '.join(filter(None, [first_user_text, label] + bash_commands))
    ticket_refs = _extract_tickets(search_text)

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
        meta={"git_branch": None, "cwd": None, "github_repo": github_repo},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest backend/adapters/test_gemini_cli.py -v`
Expected: `15 passed`

- [ ] **Step 5: Run the full backend adapter test suite together**

Run: `.venv/bin/python -m pytest backend/adapters/ -v`
Expected: all tests pass (existing `test_common.py`/`test_codex.py` + 15 new).

- [ ] **Step 6: Commit**

```bash
git add backend/adapters/gemini_cli.py backend/adapters/test_gemini_cli.py
git commit -m "feat: add Gemini CLI transcript adapter, labeled provider=gemini (AI-47)"
```

---

### Task 2: Backend provider dispatch + parent-id header

**Files:**
- Modify: `backend/api/routes.py:12` (import), `backend/api/routes.py:16-19` (`PROVIDER_ADAPTERS`),
  `backend/api/routes.py:205-263` (`ingest_transcript`)
- Test: `backend/api/test_routes.py`

**Interfaces:**
- Consumes: `backend.adapters.gemini_cli.parse_transcript_content` (Task 1).
- Produces: `PROVIDER_ADAPTERS["gemini"]` entry; `ingest_transcript` now reads a new
  `x_parent_id: Optional[str] = Header(None)` and passes it as `parent_id=` into whichever adapter
  `_select_parser` returns. This is the first time any provider's `parent_id` parameter is actually
  populated via the network ingest path (previously always `None`, since only the local-only
  `parse_transcript(path)` wrapper used it). Task 3 (collector) is the producer of this header's
  value.

- [ ] **Step 1: Write the failing test**

Add to `backend/api/test_routes.py`:

```python
from backend.adapters import gemini_cli
```

(add to the existing `from backend.adapters import claude_code, codex` import line, making it
`from backend.adapters import claude_code, codex, gemini_cli`)

```python
def test_select_parser_dispatches_gemini():
    assert _select_parser("gemini") is gemini_cli.parse_transcript_content
```

Also update the existing rejection test: `"gemini"` is no longer an unknown provider once this
task lands, so it must be removed from the "bad" list (otherwise this existing test would start
failing once Step 3 below adds the real dispatch entry):

Replace:

```python
def test_select_parser_rejects_unknown_provider():
    # Previously silently fell back to the Claude Code parser, mislabeling
    # any typo'd/case-mismatched/not-yet-supported (e.g. "gemini") provider
    # as provider="anthropic" with no error. Must now reject explicitly.
    for bad_provider in ("gemini", "bogus", "Anthropic", "openAI"):
        with pytest.raises(HTTPException) as exc_info:
            _select_parser(bad_provider)
        assert exc_info.value.status_code == 422
```

with:

```python
def test_select_parser_rejects_unknown_provider():
    # Previously silently fell back to the Claude Code parser, mislabeling
    # any typo'd/case-mismatched/not-yet-supported provider as
    # provider="anthropic" with no error. Must now reject explicitly.
    for bad_provider in ("bogus", "Anthropic", "openAI", "geminiCLI"):
        with pytest.raises(HTTPException) as exc_info:
            _select_parser(bad_provider)
        assert exc_info.value.status_code == 422
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `.venv/bin/python -m pytest backend/api/test_routes.py -v`
Expected: `ImportError: cannot import name 'gemini_cli' from 'backend.adapters'` (collection error).

- [ ] **Step 3: Add the dispatch registry entry**

Replace `backend/api/routes.py:12`:

```python
from backend.adapters import claude_code, codex
```

with:

```python
from backend.adapters import claude_code, codex, gemini_cli
```

Replace `backend/api/routes.py:16-19`:

```python
PROVIDER_ADAPTERS = {
    "anthropic": claude_code.parse_transcript_content,
    "openai": codex.parse_transcript_content,
}
```

with:

```python
PROVIDER_ADAPTERS = {
    "anthropic": claude_code.parse_transcript_content,
    "openai": codex.parse_transcript_content,
    "gemini": gemini_cli.parse_transcript_content,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest backend/api/test_routes.py -v`
Expected: `4 passed`

- [ ] **Step 5: Wire the `X-Parent-Id` header into `ingest_transcript`**

Replace this block in `ingest_transcript`'s signature:

```python
    x_file_mtime: Optional[float] = Header(None),
    x_provider: str = Header("anthropic"),
    session: Session = Depends(get_session),
):
```

with:

```python
    x_file_mtime: Optional[float] = Header(None),
    x_provider: str = Header("anthropic"),
    x_parent_id: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
```

Replace this line later in the same function:

```python
    parse_fn = _select_parser(x_provider)
    run = parse_fn(content, mtime=x_file_mtime)
```

with:

```python
    parse_fn = _select_parser(x_provider)
    run = parse_fn(content, mtime=x_file_mtime, parent_id=x_parent_id)
```

This is safe for the existing `anthropic`/`openai` providers too: both adapters already accept a
`parent_id` keyword (previously always `None` via this path), and passing an explicit `None` when
the header is absent (which is FastAPI's default whenever the collector doesn't send
`X-Parent-Id`) behaves identically to today for Claude Code and Codex sessions. No endpoint-level
integration test exists for this yet (matches the existing scope of `test_routes.py`, which only
unit-tests `_select_parser`, not the full endpoint via a `TestClient`); `parent_id` passthrough is
covered at the adapter level by Task 1's
`test_parse_transcript_content_passes_through_parent_id`, and this one-line wiring change mirrors
how `x_file_mtime` already flows through with no dedicated endpoint test either.

- [ ] **Step 6: Confirm the backend still imports cleanly**

Run: `.venv/bin/python -c "import backend.main"`
Expected: no output, no error.

- [ ] **Step 7: Commit**

```bash
git add backend/api/routes.py backend/api/test_routes.py
git commit -m "feat: dispatch /v1/ingest to the Gemini adapter and pass through X-Parent-Id (AI-47)"
```

---

### Task 3: Collector Gemini source + parent-id header

**Files:**
- Modify: `collector/collector.py` (throughout; see steps below)
- Modify: `collector/test_collector.py` (fix one existing test's `fake_ship` signature, plus new
  tests)

**Interfaces:**
- Consumes: nothing from Tasks 1-2 directly (collector/ and backend/ are separate processes with no
  import dependency), but functionally depends on Task 2 already being deployed, since shipping
  `X-Provider: gemini` against a backend that doesn't yet have the `"gemini"` key in
  `PROVIDER_ADAPTERS` would get every Gemini session rejected with a 422. Naturally satisfied as
  long as all three tasks merge and deploy together.
- Produces: `SOURCES["gemini"]`, `_parent_id_for_path(path: Path, provider: str) -> str | None`;
  nothing later in this plan consumes these (this is the final task), but this is the collector's
  half of the `X-Parent-Id` contract Task 2 established.

- [ ] **Step 1: Write the failing tests**

Append to `collector/test_collector.py`:

```python
def test_parent_id_for_path_detects_gemini_subagent_path(tmp_path):
    path = tmp_path / "chats" / "06ba9b64-parent" / "9c128235-subagent.jsonl"
    assert collector_mod._parent_id_for_path(path, "gemini") == "06ba9b64-parent"


def test_parent_id_for_path_returns_none_for_gemini_main_session(tmp_path):
    path = tmp_path / "chats" / "session-2026-06-29T13-49-06ba9b64.jsonl"
    assert collector_mod._parent_id_for_path(path, "gemini") is None


def test_parent_id_for_path_returns_none_for_non_gemini_provider(tmp_path):
    # Same subagent-shaped nesting, but this convention only applies to the
    # "gemini" source: Claude Code/Codex paths never mean this.
    path = tmp_path / "chats" / "06ba9b64-parent" / "9c128235-subagent.jsonl"
    assert collector_mod._parent_id_for_path(path, "openai") is None


def test_ship_urllib_sends_x_parent_id_header_when_present(tmp_path, monkeypatch):
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
        captured["parent_id"] = req.get_header("X-parent-id")
        return FakeResponse()

    monkeypatch.setattr(collector_mod.urllib.request, "urlopen", fake_urlopen)

    ok, new_offset, _ = collector_mod._ship_urllib(
        f, "https://example.test", "test-key", "gemini",
        offset=0, mtime=1.0, parent_id="parent-session-xyz",
    )

    assert ok
    assert captured["parent_id"] == "parent-session-xyz"


def test_ship_urllib_omits_x_parent_id_header_when_none(tmp_path, monkeypatch):
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
        captured["parent_id"] = req.get_header("X-parent-id")
        return FakeResponse()

    monkeypatch.setattr(collector_mod.urllib.request, "urlopen", fake_urlopen)

    ok, new_offset, _ = collector_mod._ship_urllib(
        f, "https://example.test", "test-key", "anthropic", offset=0, mtime=1.0,
    )

    assert ok
    assert captured["parent_id"] is None
```

Also fix the existing `fake_ship` in `test_sync_all_dispatches_correct_provider_per_source` so it
accepts the new trailing `parent_id` keyword `sync_all` now passes (this test's own directories are
plain `anthropic`/`openai` dirs, not Gemini subagent paths, so `parent_id` will always resolve to
`None` for it, but the fake must still accept the argument or `sync_all` will raise `TypeError`).
Replace:

```python
    async def fake_ship(path, url, key, provider, client, offset=0, mtime=0.0):
        calls.append((path.name, provider))
        return True, len(path.read_text()), 10
```

with:

```python
    async def fake_ship(path, url, key, provider, client, offset=0, mtime=0.0, parent_id=None):
        calls.append((path.name, provider))
        return True, len(path.read_text()), 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest collector/test_collector.py -v` (from repo root)
Expected: `AttributeError: <module 'collector.collector' ...> does not have the attribute
'_parent_id_for_path'` for the new tests; the two `_ship_urllib` tests fail with `TypeError:
_ship_urllib() got an unexpected keyword argument 'parent_id'`.

- [ ] **Step 3: Add the `gemini` source and the `_parent_id_for_path` helper**

Replace `collector/collector.py`'s `SOURCES`/`_provider_for_path` block:

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

with:

```python
SOURCES = {
    "anthropic": Path.home() / ".claude" / "projects",
    "openai": Path.home() / ".codex" / "sessions",
    "gemini": Path.home() / ".gemini" / "tmp",
}


def _provider_for_path(path: Path) -> str:
    for provider, base in SOURCES.items():
        try:
            path.relative_to(base)
            return provider
        except ValueError:
            continue
    return "anthropic"


def _parent_id_for_path(path: Path, provider: str) -> str | None:
    """Gemini CLI subagent transcripts live at .../chats/<parent-id>/<subagent-id>.jsonl,
    two directories under "chats", vs. main sessions directly at
    .../chats/session-*.jsonl, one directory under "chats". The subagent's own
    transcript content never records its parent's session id anywhere (unlike
    Claude Code's agentId/sessionId fields), so this must be derived from the
    path here, before the file is shipped: the backend never receives the
    original file path.
    """
    if provider != "gemini":
        return None
    if path.parent.name != "chats" and path.parent.parent.name == "chats":
        return path.parent.name
    return None
```

- [ ] **Step 4: Add `parent_id` to `_ship_urllib` and send `X-Parent-Id` when present**

Replace the signature:

```python
def _ship_urllib(
    path: Path, url: str, key: str, provider: str, offset: int = 0, mtime: float = 0.0
) -> tuple[bool, int, int]:
```

with:

```python
def _ship_urllib(
    path: Path, url: str, key: str, provider: str, offset: int = 0, mtime: float = 0.0,
    parent_id: str | None = None,
) -> tuple[bool, int, int]:
```

Replace:

```python
    req.add_header("X-File-Mtime", str(mtime))
    req.add_header("X-Provider", provider)

    try:
```

with:

```python
    req.add_header("X-File-Mtime", str(mtime))
    req.add_header("X-Provider", provider)
    if parent_id:
        req.add_header("X-Parent-Id", parent_id)

    try:
```

Replace the recursive resend-from-0 call:

```python
            return _ship_urllib(path, url, key, provider, offset=0, mtime=mtime)
```

with:

```python
            return _ship_urllib(path, url, key, provider, offset=0, mtime=mtime, parent_id=parent_id)
```

- [ ] **Step 5: Update `_sync_all_stdlib` to compute and pass `parent_id`**

Replace:

```python
            offset = entry["offset"] if size >= entry["offset"] else 0

            # Three attempts with backoff for transient network errors
            for attempt in range(3):
                ok, new_offset, gz_len = _ship_urllib(path, url, key, provider, offset, mtime)
                if ok:
```

with:

```python
            offset = entry["offset"] if size >= entry["offset"] else 0
            parent_id = _parent_id_for_path(path, provider)

            # Three attempts with backoff for transient network errors
            for attempt in range(3):
                ok, new_offset, gz_len = _ship_urllib(
                    path, url, key, provider, offset, mtime, parent_id=parent_id
                )
                if ok:
```

- [ ] **Step 6: Add `parent_id` to `ship` (async) and send `X-Parent-Id` when present**

Replace the signature:

```python
async def ship(
    path: Path, url: str, key: str, provider: str, client, offset: int = 0, mtime: float = 0.0
) -> tuple[bool, int, int]:
```

with:

```python
async def ship(
    path: Path, url: str, key: str, provider: str, client, offset: int = 0, mtime: float = 0.0,
    parent_id: str | None = None,
) -> tuple[bool, int, int]:
```

Replace the request block:

```python
            resp = await client.post(
                f"{url.rstrip('/')}/api/v1/ingest",
                content=compressed,
                headers={
                    "X-API-Key": key,
                    "Content-Type": "text/plain",
                    "Content-Encoding": "gzip",
                    "X-Session-Id": path.stem,
                    "X-File-Offset": str(offset),
                    "X-File-Mtime": str(mtime),
                    "X-Provider": provider,
                },
                timeout=15,
            )
```

with:

```python
            headers = {
                "X-API-Key": key,
                "Content-Type": "text/plain",
                "Content-Encoding": "gzip",
                "X-Session-Id": path.stem,
                "X-File-Offset": str(offset),
                "X-File-Mtime": str(mtime),
                "X-Provider": provider,
            }
            if parent_id:
                headers["X-Parent-Id"] = parent_id
            resp = await client.post(
                f"{url.rstrip('/')}/api/v1/ingest",
                content=compressed,
                headers=headers,
                timeout=15,
            )
```

Replace the recursive resend-from-0 call:

```python
                return await ship(path, url, key, provider, client, offset=0, mtime=mtime)
```

with:

```python
                return await ship(path, url, key, provider, client, offset=0, mtime=mtime, parent_id=parent_id)
```

- [ ] **Step 7: Update `sync_all` (async) to compute and pass `parent_id`**

Replace:

```python
            offset = entry["offset"] if size >= entry["offset"] else 0
            ok, new_offset, gz_len = await ship(path, url, key, provider, client, offset, mtime)
            if ok:
```

with:

```python
            offset = entry["offset"] if size >= entry["offset"] else 0
            parent_id = _parent_id_for_path(path, provider)
            ok, new_offset, gz_len = await ship(
                path, url, key, provider, client, offset, mtime, parent_id=parent_id
            )
            if ok:
```

- [ ] **Step 8: Update `watch()`'s per-changed-file loop to compute and pass `parent_id`**

Replace:

```python
                            key_str = str(path)
                            entry = state.get(key_str, {"mtime": 0, "offset": 0})
                            offset = entry["offset"] if size >= entry["offset"] else 0
                            provider = _provider_for_path(path)
                            ok, new_offset, _ = await ship(path, url, key, provider, client, offset, mtime)
                            if ok:
```

with:

```python
                            key_str = str(path)
                            entry = state.get(key_str, {"mtime": 0, "offset": 0})
                            offset = entry["offset"] if size >= entry["offset"] else 0
                            provider = _provider_for_path(path)
                            parent_id = _parent_id_for_path(path, provider)
                            ok, new_offset, _ = await ship(
                                path, url, key, provider, client, offset, mtime, parent_id=parent_id
                            )
                            if ok:
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `python3 -m pytest collector/test_collector.py -v`
Expected: all tests pass (13 existing + 5 new).

- [ ] **Step 10: Manual verification: the Gemini source is watched on this real machine**

```bash
python3 -c "
import collector.collector as c
print('SOURCES:', c.SOURCES)
for provider, base in c.SOURCES.items():
    print(f'{provider}: {base} exists={base.exists()}')
"
```

Expected: prints `anthropic`, `openai`, and `gemini` entries, all showing `exists=True` on this
machine (all three directories confirmed present during the design/planning phase).

- [ ] **Step 11: Commit**

```bash
git add collector/collector.py collector/test_collector.py
git commit -m "feat: collector watches ~/.gemini/tmp and ships X-Parent-Id for subagent sessions (AI-47)"
```

---

### Task 4: Live verification

**Files:** none (verification only, no code changes)

- [ ] **Step 1: Point a local collector config at the running backend**

If not already configured from AI-46's work, create `~/.ai_dash/config.json` with the local/deployed
backend URL and a valid API key (see `README.md`'s Collector Setup section).

- [ ] **Step 2: Run the collector once**

```bash
python3 -m collector.collector
```

Let it run long enough to complete an initial sync pass (logs to `~/.ai_dash/collector.log` and
stdout), then stop it (Ctrl-C); a one-shot sync of existing sessions is enough for verification, no
need to leave it running.

- [ ] **Step 3: Confirm at least one Gemini session was ingested**

```bash
curl -s "$(python3 -c "import json;print(json.load(open('$HOME/.ai_dash/config.json'))['url'])")/api/runs?provider=gemini" \
  -H "Authorization: Basic $(echo -n ':YOUR_DASHBOARD_PASSWORD' | base64)" | python3 -m json.tool | head -40
```

Expected: at least one run with `"provider": "gemini"`, non-zero `input_tokens`/`output_tokens`, and
(if the machine has a Gemini CLI session that spawned a subagent; several exist locally, e.g. the
`code-review`/`superpowers` skill-invocation sessions) a `parent_id` pointing at another real
`gemini`-provider run's `id`, confirming the `X-Parent-Id` header round-tripped correctly end to
end.

- [ ] **Step 4: Confirm the dashboard renders it**

Open the dashboard frontend, filter by provider = Gemini, and visually confirm the ingested
session(s) appear with correct token counts and (for the subagent case) correct parent/child linkage
in the trace-tree view on the run detail page.

---

## Self-Review

**Spec coverage:** Header/`$set` hybrid parsing, dedup-by-id, the sum-based token formula, the
combined command+result tool-call extraction (no pending-id state), the `<`-prefix label skip,
model default, ticket refs, and the `git_branch`/`cwd` known-gap → Task 1. `PROVIDER_ADAPTERS`
dispatch entry → Task 2. `SOURCES["gemini"]` entry and the collector side of parent-id detection →
Task 3. Live end-to-end verification, including the subagent trace-tree case → Task 4. The
`X-Parent-Id` header addition (a refinement discovered during planning, not in the original spec) is
covered across Tasks 2 and 3 together and called out explicitly in Global Constraints.

**Placeholder scan:** No TBD/TODO; every step shows complete code, exact diffs, or exact commands
with expected output.

**Type consistency:** `parse_transcript_content(content: str, mtime: Optional[float] = None,
parent_id: Optional[str] = None, agent_id: Optional[str] = None) -> Optional[AgentRun]` matches
`claude_code.py`/`codex.py`'s exact signature shape, so Task 2's `PROVIDER_ADAPTERS` registry and
`ingest_transcript`'s `parse_fn(content, mtime=x_file_mtime, parent_id=x_parent_id)` call work
identically across all three adapters. `_ship_urllib`/`ship`'s new `parent_id: str | None = None`
parameter is appended as a trailing keyword-defaulted parameter in both (after `mtime`), preserving
every existing positional call site untouched except where Task 3 explicitly adds the new keyword;
confirmed against the one existing test that constructs its own `fake_ship` stand-in, which is
updated in the same task to accept the new parameter.
