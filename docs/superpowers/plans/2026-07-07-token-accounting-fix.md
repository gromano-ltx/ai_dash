# Token Accounting Fix Implementation Plan (AI-54)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `input_tokens` mean the same thing across all three provider adapters (fresh/non-cached tokens only), capture the cached-token breakdown for future cost calculations, backfill existing rows, and document the methodology.

**Architecture:** `codex.py` and `gemini_cli.py` currently sum/report `input_tokens` including re-sent cached context; both are fixed to exclude it, matching `claude_code.py`'s existing (already-correct) methodology. All three adapters gain a `meta.cached_input_tokens` field. An idempotent backfill block in `backend/db.py`'s existing `_seed()` function re-parses already-ingested Codex/Claude Code rows' stored transcripts to correct their data in place. A new README section documents what the numbers mean.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, pytest.

## Global Constraints

- `codex.py`: `input_tokens` becomes the sum of `last_token_usage.input_tokens -
  last_token_usage.cached_input_tokens` across turns (not the last cumulative
  `total_token_usage.input_tokens` value). `output_tokens` is UNCHANGED (still the last
  `total_token_usage.output_tokens` value — output is never cached, so the existing
  cumulative-last-value approach is already correct there).
- `codex.py`'s `token_count` events are logged twice consecutively (verified against real data) —
  dedupe by tracking the last-seen `total_token_usage` snapshot and only processing an event when it
  has actually changed (no event `id` field exists to dedupe by directly, unlike Gemini).
- `gemini_cli.py`: `input_tokens` becomes `tokens.get('input', 0) - tokens.get('cached', 0)` summed
  per turn (was: raw `tokens.get('input', 0)` summed). `output_tokens` is unchanged.
- All three adapters add `meta.cached_input_tokens`: Codex sums `last_token_usage.cached_input_tokens`;
  Gemini sums `tokens.get('cached', 0)`; Claude Code sums `usage.get('cache_read_input_tokens', 0)`
  (reusing its existing `seen_request_ids` dedup — no new dedup logic needed there).
- Claude Code's `cache_creation_input_tokens` (premium-priced fresh cache-writes, not
  discounted-reuse) is deliberately NOT captured — out of scope for this fix.
- Backfill only touches `AgentRun.input_tokens` and `AgentRun.meta` on existing rows — every other
  field (`status`, `started_at`, `ended_at`, `label`, etc.) is left exactly as it is; a fresh re-parse
  can't reliably reconstruct those (e.g. no access to the original file's mtime).
- Backfill applies to `provider in ("openai", "anthropic")` rows only — no Gemini rows currently
  exist in production, so no Gemini backfill is needed.
- Backfill follows the existing idempotent-inline-migration convention already in
  `backend/db.py`'s `_seed()` (see `_add_missing_columns`, the `updated_at`/demo-row/malformed-data
  cleanups already there) — guarded by checking whether `meta.cached_input_tokens` is already set,
  so it's safe to run on every backend startup.

---

### Task 1: Fix `codex.py`'s input-token accounting

**Files:**
- Modify: `backend/adapters/codex.py`
- Test: `backend/adapters/test_codex.py`

**Interfaces:**
- Produces: `codex.parse_transcript_content(...)`'s returned `AgentRun.input_tokens` now excludes
  cached tokens; `AgentRun.meta["cached_input_tokens"]` is a new key. Signature unchanged. Task 4
  (backfill) calls this function with just `content` (no `mtime`/`parent_id`/`agent_id`) and relies
  on `.input_tokens`/`.meta` from the result.

- [ ] **Step 1: Write the failing tests**

Replace the whole of `backend/adapters/test_codex.py`'s `_sample_session` function and the four
tests that depend on token-count behavior. Replace this block (the `_sample_session` function
signature through the end of its `token_count` event lines, i.e. everything from
`def _sample_session(*, second_token_count_is_higher=True) -> str:` through the closing of the
`lines = [...]` list and its `return` statement):

```python
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
        # Verified real-world artifact: every token_count event is logged twice
        # consecutively. Must not be double-counted.
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
        # Second turn: cumulative total_token_usage grows to input=3010 (of
        # which 1500 is cached, carried over from turn 1's context), output=128.
        # last_token_usage is THIS turn's own delta: input=2010 (of which 1500
        # is cached — i.e. 510 genuinely new), output=78.
        # Sanity check: 1000+2010=3010 (total.input), 50+78=128 (total.output).
        _line({
            "timestamp": "2026-04-16T16:02:20.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 3010 if second_token_count_is_higher else 500,
                        "cached_input_tokens": 1500,
                        "output_tokens": 128 if second_token_count_is_higher else 10,
                        "reasoning_output_tokens": 64,
                        "total_tokens": 3138 if second_token_count_is_higher else 510,
                    },
                    "last_token_usage": {
                        "input_tokens": 2010,
                        "cached_input_tokens": 1500,
                        "output_tokens": 78,
                        "reasoning_output_tokens": 44,
                        "total_tokens": 2088,
                    },
                    "model_context_window": 272000,
                },
                "rate_limits": {"primary": None, "secondary": None},
            },
        }),
        # Duplicate of the second turn's event — must not be double-counted either.
        _line({
            "timestamp": "2026-04-16T16:02:20.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 3010 if second_token_count_is_higher else 500,
                        "cached_input_tokens": 1500,
                        "output_tokens": 128 if second_token_count_is_higher else 10,
                        "reasoning_output_tokens": 64,
                        "total_tokens": 3138 if second_token_count_is_higher else 510,
                    },
                    "last_token_usage": {
                        "input_tokens": 2010,
                        "cached_input_tokens": 1500,
                        "output_tokens": 78,
                        "reasoning_output_tokens": 44,
                        "total_tokens": 2088,
                    },
                    "model_context_window": 272000,
                },
                "rate_limits": {"primary": None, "secondary": None},
            },
        }),
    ]
    return "\n".join(lines) + "\n"
```

Replace this test (which asserted the old, now-incorrect, cached-inclusive last-value behavior):

```python
def test_parse_transcript_content_uses_last_token_count_not_summed():
    run = parse_transcript_content(_sample_session())
    # Last token_count event has input=3010/output=128 — must NOT be
    # 1000+3010=4010 (summed); summing would wildly over-count since each
    # event already carries the whole-session-so-far cumulative total.
    assert run.input_tokens == 3010
    assert run.output_tokens == 128
```

with:

```python
def test_parse_transcript_content_sums_new_input_tokens_excluding_cached():
    run = parse_transcript_content(_sample_session())
    # Turn 1 (deduped despite being logged twice): last_token_usage input=1000,
    # cached=0 → new=1000. Turn 2 (deduped): last_token_usage input=2010,
    # cached=1500 → new=510. Total: 1000+510=1510 — NOT the old last-cumulative
    # value (3010), which double-counted turn 1's context inside turn 2's
    # cumulative total.
    assert run.input_tokens == 1510


def test_parse_transcript_content_output_tokens_unaffected_by_fix():
    run = parse_transcript_content(_sample_session())
    # output_tokens still uses the last cumulative total_token_usage.output_tokens
    # value — output is never cached, so this was already correct.
    assert run.output_tokens == 128


def test_parse_transcript_content_captures_cached_input_tokens_in_meta():
    run = parse_transcript_content(_sample_session())
    # Turn 1 cached=0, turn 2 cached=1500 (each deduped despite being logged twice).
    assert run.meta["cached_input_tokens"] == 1500
```

Replace the existing null-handling test's assertions (the fixture content is unchanged, only the
expected preserved values change to match the new methodology):

```python
def test_parse_transcript_content_handles_null_total_token_usage():
    # A token_count event with info.total_token_usage: null (key present,
    # value null) — distinct from a missing key, which the {} default
    # already handles. Token counts should stay at whatever they were before.
    line = _line({
        "timestamp": "2026-04-16T16:02:25.000Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": None,
                "last_token_usage": {},
                "model_context_window": 272000,
            },
            "rate_limits": {"primary": None, "secondary": None},
        },
    })
    content = _sample_session().rstrip("\n") + "\n" + line + "\n"
    run = parse_transcript_content(content)
    assert run is not None
    # Preserves the last valid (non-null) token counts rather than crashing
    # or silently resetting to 0.
    assert run.input_tokens == 3010
    assert run.output_tokens == 128
```

with:

```python
def test_parse_transcript_content_handles_null_total_token_usage():
    # A token_count event with info.total_token_usage: null (key present,
    # value null) — distinct from a missing key, which the {} default
    # already handles. Token counts should stay at whatever they were before.
    line = _line({
        "timestamp": "2026-04-16T16:02:25.000Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": None,
                "last_token_usage": {},
                "model_context_window": 272000,
            },
            "rate_limits": {"primary": None, "secondary": None},
        },
    })
    content = _sample_session().rstrip("\n") + "\n" + line + "\n"
    run = parse_transcript_content(content)
    assert run is not None
    # Preserves the last valid (non-null) token counts rather than crashing
    # or silently resetting to 0.
    assert run.input_tokens == 1510
    assert run.output_tokens == 128
    assert run.meta["cached_input_tokens"] == 1500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest backend/adapters/test_codex.py -v` (from repo root)
Expected: `test_parse_transcript_content_sums_new_input_tokens_excluding_cached`,
`test_parse_transcript_content_captures_cached_input_tokens_in_meta`, and
`test_parse_transcript_content_handles_null_total_token_usage` FAIL (the implementation hasn't
changed yet — `input_tokens` is still 3010, and `meta` has no `cached_input_tokens` key).
`test_parse_transcript_content_output_tokens_unaffected_by_fix` PASSES already (no code change
needed for it).

- [ ] **Step 3: Fix `backend/adapters/codex.py`**

Replace:

```python
    input_tokens = 0
    output_tokens = 0

    for event in events:
```

with:

```python
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0
    last_seen_usage_key = None

    for event in events:
```

Replace:

```python
        elif etype == 'event_msg' and payload.get('type') == 'token_count':
            info = payload.get('info')
            if info:
                usage = info.get('total_token_usage') or {}
                input_tokens = usage.get('input_tokens', input_tokens)
                output_tokens = usage.get('output_tokens', output_tokens)
```

with:

```python
        elif etype == 'event_msg' and payload.get('type') == 'token_count':
            info = payload.get('info')
            if info:
                usage = info.get('total_token_usage') or {}
                if usage:
                    output_tokens = usage.get('output_tokens', output_tokens)
                last = info.get('last_token_usage') or {}
                usage_key = (usage.get('input_tokens'), usage.get('output_tokens')) if usage else None
                # token_count events are logged twice consecutively (verified
                # against real Codex session data) — dedupe by only counting a
                # turn's delta once its cumulative total_token_usage snapshot
                # actually changes from the last one seen (Codex events carry
                # no id field to dedupe by directly, unlike Gemini's).
                if last and usage_key is not None and usage_key != last_seen_usage_key:
                    li = last.get('input_tokens', 0)
                    lc = last.get('cached_input_tokens', 0)
                    input_tokens += max(li - lc, 0)
                    cached_input_tokens += lc
                    last_seen_usage_key = usage_key
```

Replace the return statement's `meta`:

```python
        meta={"git_branch": git_branch, "cwd": cwd, "github_repo": github_repo},
```

with:

```python
        meta={"git_branch": git_branch, "cwd": cwd, "github_repo": github_repo, "cached_input_tokens": cached_input_tokens},
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest backend/adapters/test_codex.py -v`
Expected: `15 passed` (13 existing tests, minus the one replaced by three new ones: 13 - 1 + 3 = 15)

- [ ] **Step 5: Commit**

```bash
git add backend/adapters/codex.py backend/adapters/test_codex.py
git commit -m "fix: codex adapter excludes cached tokens from input_tokens, captures cache breakdown (AI-54)"
```

---

### Task 2: Fix `gemini_cli.py`'s input-token accounting

**Files:**
- Modify: `backend/adapters/gemini_cli.py`
- Test: `backend/adapters/test_gemini_cli.py`

**Interfaces:**
- Produces: same shape of change as Task 1 — `input_tokens` excludes cached, `meta["cached_input_tokens"]`
  is new. No signature change.

- [ ] **Step 1: Write the failing tests**

Replace this existing test (the fixture's turns are unchanged — turn 1 `cached: 0`, turn 2
`cached: 50` — only the expected sum changes now that cached tokens are excluded):

```python
def test_parse_transcript_content_sums_input_tokens_across_turns():
    run = parse_transcript_content(_sample_session())
    # 100 (turn 1, deduped) + 150 (turn 2) = 250 — NOT 100+100+150=350, which
    # would double-count the verified real duplicate-line case.
    assert run.input_tokens == 250
```

with:

```python
def test_parse_transcript_content_sums_new_input_tokens_excluding_cached():
    run = parse_transcript_content(_sample_session())
    # Turn 1 (deduped): input=100, cached=0 → new=100. Turn 2: input=150,
    # cached=50 → new=100. Total: 200 — NOT 250 (which would include turn 2's
    # cached tokens), and NOT 100+100+150=350 (which would double-count the
    # verified real duplicate-line case for turn 1).
    assert run.input_tokens == 200


def test_parse_transcript_content_captures_cached_input_tokens_in_meta():
    run = parse_transcript_content(_sample_session())
    # Turn 1 cached=0 (deduped despite the duplicate line), turn 2 cached=50.
    assert run.meta["cached_input_tokens"] == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest backend/adapters/test_gemini_cli.py -v`
Expected: `test_parse_transcript_content_sums_new_input_tokens_excluding_cached` FAILS (`input_tokens`
is currently 250, not 200) and `test_parse_transcript_content_captures_cached_input_tokens_in_meta`
FAILS (no `cached_input_tokens` key in `meta` yet).

- [ ] **Step 3: Fix `backend/adapters/gemini_cli.py`**

Replace:

```python
    input_tokens = 0
    output_tokens = 0

    # Real sessions on this machine log the same message id twice in a row (a
```

with:

```python
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0

    # Real sessions on this machine log the same message id twice in a row (a
```

Replace:

```python
        elif etype == 'gemini':
            model = event.get('model') or model
            tokens = event.get('tokens') or {}
            input_tokens += tokens.get('input', 0)
            output_tokens += (
                tokens.get('output', 0) + tokens.get('thoughts', 0) + tokens.get('tool', 0)
            )
```

with:

```python
        elif etype == 'gemini':
            model = event.get('model') or model
            tokens = event.get('tokens') or {}
            cached = tokens.get('cached', 0)
            input_tokens += max(tokens.get('input', 0) - cached, 0)
            cached_input_tokens += cached
            output_tokens += (
                tokens.get('output', 0) + tokens.get('thoughts', 0) + tokens.get('tool', 0)
            )
```

Replace the return statement's `meta`:

```python
        meta={"git_branch": None, "cwd": None, "github_repo": github_repo},
```

with:

```python
        meta={"git_branch": None, "cwd": None, "github_repo": github_repo, "cached_input_tokens": cached_input_tokens},
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest backend/adapters/test_gemini_cli.py -v`
Expected: `16 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/adapters/gemini_cli.py backend/adapters/test_gemini_cli.py
git commit -m "fix: gemini adapter excludes cached tokens from input_tokens, captures cache breakdown (AI-54)"
```

---

### Task 3: Add `meta.cached_input_tokens` to `claude_code.py`

**Files:**
- Modify: `backend/adapters/claude_code.py`
- Test: `backend/adapters/test_claude_code.py` (new file — this adapter has none yet)

**Interfaces:**
- Produces: `claude_code.parse_transcript_content(...)`'s returned `AgentRun.meta["cached_input_tokens"]`
  is new. `input_tokens`/`output_tokens` are UNCHANGED (Anthropic's API already excludes cache reads
  from `usage.input_tokens`). No signature change. Task 4 (backfill) calls this function the same
  way it calls `codex.parse_transcript_content`.

- [ ] **Step 1: Write the failing tests**

Create `backend/adapters/test_claude_code.py`:

```python
import json

from backend.adapters.claude_code import parse_transcript_content


def _line(d: dict) -> str:
    return json.dumps(d)


def _sample_session() -> str:
    """A minimal, schema-accurate synthetic Claude Code session transcript,
    focused on token/cache accounting (this adapter's other behaviors —
    commit/PR extraction, ticket refs, subagent detection — are already
    covered by production usage predating this test file's existence;
    this file's scope is just the new cached_input_tokens capture)."""
    lines = [
        _line({
            "type": "user",
            "timestamp": "2026-04-16T16:01:55.000Z",
            "isMeta": True,
            "sessionId": "sess-1",
            "gitBranch": "main",
            "cwd": "/Users/gromano/repos/ai_dash",
        }),
        _line({
            "type": "assistant",
            "timestamp": "2026-04-16T16:02:00.000Z",
            "sessionId": "sess-1",
            "requestId": "req-1",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 300,
                    "cache_creation_input_tokens": 50,
                },
                "content": [],
            },
        }),
        # Duplicate requestId — must not be double-counted (existing seen_request_ids dedup).
        _line({
            "type": "assistant",
            "timestamp": "2026-04-16T16:02:00.500Z",
            "sessionId": "sess-1",
            "requestId": "req-1",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 300,
                    "cache_creation_input_tokens": 50,
                },
                "content": [],
            },
        }),
        _line({
            "type": "assistant",
            "timestamp": "2026-04-16T16:03:00.000Z",
            "sessionId": "sess-1",
            "requestId": "req-2",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 150,
                    "cache_read_input_tokens": 250,
                    "cache_creation_input_tokens": 0,
                },
                "content": [],
            },
        }),
    ]
    return "\n".join(lines) + "\n"


def test_parse_transcript_content_returns_none_for_empty_content():
    assert parse_transcript_content("") is None


def test_parse_transcript_content_sets_provider_anthropic():
    run = parse_transcript_content(_sample_session())
    assert run.provider == "anthropic"


def test_parse_transcript_content_input_and_output_tokens_unaffected_by_fix():
    run = parse_transcript_content(_sample_session())
    # req-1 (deduped, not double-counted) + req-2: input 5+3=8, output 200+150=350.
    # Anthropic's usage.input_tokens already excludes cache reads — no change
    # to this math from this fix, verified explicitly here.
    assert run.input_tokens == 8
    assert run.output_tokens == 350


def test_parse_transcript_content_captures_cached_input_tokens_in_meta():
    run = parse_transcript_content(_sample_session())
    # req-1 (deduped) cache_read_input_tokens=300 + req-2's 250 = 550.
    assert run.meta["cached_input_tokens"] == 550
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest backend/adapters/test_claude_code.py -v`
Expected: `test_parse_transcript_content_captures_cached_input_tokens_in_meta` FAILS with a `KeyError`
(no `cached_input_tokens` key in `meta` yet). The other three tests PASS already (no code change
needed for them — this proves the fix doesn't touch existing input/output token behavior).

- [ ] **Step 3: Fix `backend/adapters/claude_code.py`**

Replace:

```python
    github_repo: str | None = None
    input_tokens = 0
    output_tokens = 0
```

with:

```python
    github_repo: str | None = None
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0
```

Replace:

```python
            if rid and rid not in seen_request_ids:
                seen_request_ids.add(rid)
                usage = msg.get('usage', {})
                input_tokens += usage.get('input_tokens', 0)
                output_tokens += usage.get('output_tokens', 0)
```

with:

```python
            if rid and rid not in seen_request_ids:
                seen_request_ids.add(rid)
                usage = msg.get('usage', {})
                input_tokens += usage.get('input_tokens', 0)
                output_tokens += usage.get('output_tokens', 0)
                cached_input_tokens += usage.get('cache_read_input_tokens', 0)
```

Replace the return statement's `meta`:

```python
        meta={"git_branch": git_branch, "cwd": cwd, "github_repo": github_repo},
```

with:

```python
        meta={"git_branch": git_branch, "cwd": cwd, "github_repo": github_repo, "cached_input_tokens": cached_input_tokens},
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest backend/adapters/test_claude_code.py -v`
Expected: `4 passed`

- [ ] **Step 5: Run the full adapters test suite together**

Run: `.venv/bin/python -m pytest backend/adapters/ -v`
Expected: all tests pass (existing suites + this task's + Tasks 1-2's changes).

- [ ] **Step 6: Commit**

```bash
git add backend/adapters/claude_code.py backend/adapters/test_claude_code.py
git commit -m "feat: capture cached_input_tokens in claude_code adapter meta (AI-54)"
```

---

### Task 4: Backfill existing rows in `backend/db.py`

**Files:**
- Modify: `backend/db.py`
- Test: `backend/test_db.py` (new file)

**Interfaces:**
- Consumes: `backend.adapters.codex.parse_transcript_content` (Task 1),
  `backend.adapters.claude_code.parse_transcript_content` (Task 3) — both already accept just
  `content` as their only required argument.
- Produces: `backend.db._backfill_cached_input_tokens(session: Session) -> None`, called from
  `_seed()`. Nothing later in this plan consumes it directly, but it's the mechanism that makes the
  live dashboard's existing rows consistent with Tasks 1-3's fix.

- [ ] **Step 1: Write the failing tests**

Create `backend/test_db.py`:

```python
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine, Session, SQLModel

from backend.db import _backfill_cached_input_tokens
from backend.models import AgentRun, TranscriptStore


def _make_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _codex_transcript_content() -> str:
    """A minimal, schema-accurate Codex transcript with a known cached-token
    breakdown, for asserting the backfill recomputes input_tokens/meta correctly."""
    import json

    lines = [
        json.dumps({
            "timestamp": "2026-04-16T16:01:55.000Z",
            "type": "session_meta",
            "payload": {"id": "codex-run-1", "cwd": "/repo"},
        }),
        json.dumps({
            "timestamp": "2026-04-16T16:02:10.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1000, "cached_input_tokens": 0,
                        "output_tokens": 50, "reasoning_output_tokens": 10, "total_tokens": 1050,
                    },
                    "last_token_usage": {
                        "input_tokens": 1000, "cached_input_tokens": 0,
                        "output_tokens": 50, "reasoning_output_tokens": 10, "total_tokens": 1050,
                    },
                    "model_context_window": 272000,
                },
                "rate_limits": {"primary": None, "secondary": None},
            },
        }),
    ]
    return "\n".join(lines) + "\n"


def test_backfill_corrects_codex_input_tokens_and_adds_cached_meta():
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="codex-run-1", provider="openai", model="gpt-5-codex",
            input_tokens=999999, meta={},
        ))
        session.add(TranscriptStore(session_id="codex-run-1", content=_codex_transcript_content()))
        session.commit()

        _backfill_cached_input_tokens(session)

        run = session.get(AgentRun, "codex-run-1")
        assert run.input_tokens == 1000
        assert run.meta["cached_input_tokens"] == 0


def test_backfill_is_idempotent_and_skips_already_migrated_rows():
    engine = _make_engine()
    with Session(engine) as session:
        # No TranscriptStore row exists for this id — if the backfill didn't
        # skip already-migrated rows before looking up the transcript, this
        # would either error or silently do nothing useful either way; the
        # real assertion is that input_tokens/meta stay exactly as seeded.
        session.add(AgentRun(
            id="run-1", provider="openai", model="m",
            input_tokens=42, meta={"cached_input_tokens": 5},
        ))
        session.commit()

        _backfill_cached_input_tokens(session)

        run = session.get(AgentRun, "run-1")
        assert run.input_tokens == 42
        assert run.meta["cached_input_tokens"] == 5


def test_backfill_skips_rows_with_missing_transcript():
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="orphan-1", provider="openai", model="m",
            input_tokens=100, meta={},
        ))
        session.commit()

        _backfill_cached_input_tokens(session)  # must not raise

        run = session.get(AgentRun, "orphan-1")
        assert run.input_tokens == 100
        assert "cached_input_tokens" not in (run.meta or {})


def test_backfill_ignores_gemini_rows():
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="gemini-run-1", provider="gemini", model="gemini-3.5-flash",
            input_tokens=777, meta={},
        ))
        session.commit()

        _backfill_cached_input_tokens(session)

        run = session.get(AgentRun, "gemini-run-1")
        assert run.input_tokens == 777
        assert "cached_input_tokens" not in (run.meta or {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest backend/test_db.py -v`
Expected: `ImportError: cannot import name '_backfill_cached_input_tokens' from 'backend.db'`
(collection error — the function doesn't exist yet).

- [ ] **Step 3: Add `_backfill_cached_input_tokens` to `backend/db.py`**

Add this new function right after `_add_missing_columns` (before `def get_session():`):

```python
def _backfill_cached_input_tokens(session: Session):
    """One-time backfill: recompute input_tokens/meta.cached_input_tokens for
    rows ingested before AI-54's fix, by re-parsing their stored transcript
    content with the corrected adapter logic. Only these two fields are
    touched — status/started_at/ended_at/label etc. on the existing row are
    left exactly as they are; a fresh re-parse can't reliably reconstruct
    those (e.g. it has no access to the original file's mtime), but
    input_tokens/meta are fully and correctly derivable from the stored
    transcript content alone. Gemini rows are skipped — no real ones existed
    at the time of this fix, so nothing to backfill there.
    """
    from backend.adapters import claude_code, codex
    from backend.models import AgentRun, TranscriptStore

    parsers = {"openai": codex.parse_transcript_content, "anthropic": claude_code.parse_transcript_content}
    rows = session.exec(
        select(AgentRun).where(AgentRun.provider.in_(list(parsers.keys())))
    ).all()
    migrated = 0
    for run in rows:
        if isinstance(run.meta, dict) and "cached_input_tokens" in run.meta:
            continue  # already migrated
        stored = session.get(TranscriptStore, run.id)
        if not stored:
            continue
        parse_fn = parsers[run.provider]
        reparsed = parse_fn(stored.content)
        if not reparsed:
            continue
        run.input_tokens = reparsed.input_tokens
        run.meta = reparsed.meta
        session.add(run)
        migrated += 1
    if migrated:
        session.commit()
        print(f"[db] backfilled cached_input_tokens for {migrated} runs")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest backend/test_db.py -v`
Expected: `4 passed`

- [ ] **Step 5: Wire the backfill into `_seed()`**

Add a call to the new function at the end of `_seed()`'s existing `else:` branch (the one that runs
when an `ApiKey` already exists — i.e. not a fresh/first-ever deploy). Insert it right after the
existing `ended_at` backfill block (the last block currently in that branch), so it runs on the
final, already-cleaned-up set of rows. Replace:

```python
            stuck = session.exec(
                select(AgentRun).where(
                    AgentRun.status == "done",
                    AgentRun.ended_at == None,  # noqa: E711
                    AgentRun.updated_at != None,  # noqa: E711
                )
            ).all()
            if stuck:
                for r in stuck:
                    r.ended_at = r.updated_at
                    session.add(r)
                session.commit()
                print(f"[db] backfilled ended_at for {len(stuck)} runs stuck done with null duration")
```

with:

```python
            stuck = session.exec(
                select(AgentRun).where(
                    AgentRun.status == "done",
                    AgentRun.ended_at == None,  # noqa: E711
                    AgentRun.updated_at != None,  # noqa: E711
                )
            ).all()
            if stuck:
                for r in stuck:
                    r.ended_at = r.updated_at
                    session.add(r)
                session.commit()
                print(f"[db] backfilled ended_at for {len(stuck)} runs stuck done with null duration")
            _backfill_cached_input_tokens(session)
```

- [ ] **Step 6: Run the full backend test suite**

Run: `.venv/bin/python -m pytest backend/ -v`
Expected: all tests pass, no regressions.

- [ ] **Step 7: Confirm the backend still imports cleanly**

Run: `.venv/bin/python -c "import backend.main"`
Expected: no output, no error.

- [ ] **Step 8: Commit**

```bash
git add backend/db.py backend/test_db.py
git commit -m "feat: backfill cached_input_tokens for existing Codex/Claude Code runs on startup (AI-54)"
```

---

### Task 5: Document the token accounting methodology in `README.md`

**Files:**
- Modify: `README.md`

**Interfaces:** None — documentation only, depends on Tasks 1-3's methodology being final.

- [ ] **Step 1: Add a `## Token accounting` section**

Insert a new section right after the existing `## Architecture` section (before `## Stack`).
Replace:

```markdown
The **collector** runs locally, watches your Claude Code transcript files, and ships them to the central server. The server parses them into a unified `AgentRun` schema and serves the dashboard, gated by per-user login (see [Auth](#auth)).

---

## Stack
```

with:

```markdown
The **collector** runs locally, watches your Claude Code transcript files, and ships them to the central server. The server parses them into a unified `AgentRun` schema and serves the dashboard, gated by per-user login (see [Auth](#auth)).

---

## Token accounting

`input_tokens` means the same thing for all three providers: the sum of **fresh, non-cached**
prompt tokens across every turn of a session. Each provider's own coding-agent CLI resends the
growing conversation as context on every turn, and most of that gets served from a prompt cache
(a discount, not free) rather than billed at full price — so `input_tokens` deliberately excludes
the re-sent cached portion, otherwise a long session would look inflated by the same context being
counted again on every turn.

`meta.cached_input_tokens` captures that excluded, discounted portion separately — not shown on the
dashboard today, but tracked so future cost-tracking work can price fresh vs. cached tokens
correctly without re-parsing transcripts again.

`output_tokens` is never cached for any provider, so it needs no such adjustment.

One known asymmetry: Claude Code's API also reports `cache_creation_input_tokens` (the cost of
*writing* a new cache entry — a premium-priced, fresh-content category, not a discounted-reuse one).
That's not captured yet; it's a different economic category than `cached_input_tokens` and would
need its own pricing treatment.

---

## Stack
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document token accounting methodology (AI-54)"
```

---

## Self-Review

**Spec coverage:** Codex fix (dedup + new-tokens-only sum) → Task 1. Gemini fix → Task 2. Claude
Code `meta.cached_input_tokens` (first test file for this adapter) → Task 3. Backfill via `_seed()`,
idempotent, skips Gemini/already-migrated/missing-transcript rows → Task 4. README documentation →
Task 5. Every Global Constraint is covered by a task; no gaps.

**Placeholder scan:** No TBD/TODO; every step shows complete code, exact diffs, or exact commands
with expected output.

**Type consistency:** All three adapters' `meta` dicts gain the same key name,
`"cached_input_tokens"` (int), read the same way by Task 4's backfill
(`reparsed.meta` assigned wholesale onto the existing row) and by Task 4's own tests. `codex.py`'s
and `gemini_cli.py`'s `parse_transcript_content` signatures are unchanged (still `content, mtime=None,
parent_id=None, agent_id=None`), so Task 4's `parse_fn(stored.content)` calls (no extra kwargs) work
identically for both providers.
