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


def test_parse_transcript_content_returns_none_for_empty_content():
    assert parse_transcript_content("") is None


def test_parse_transcript_content_sets_provider_openai():
    run = parse_transcript_content(_sample_session())
    assert run.provider == "openai"


def test_parse_transcript_content_does_not_set_user():
    # AI-51 finding 1: this adapter's parse_transcript_content() is only ever
    # invoked from backend.api.routes.ingest_transcript (an async route),
    # which always overwrites run.user with api_key.user two lines later —
    # calling the blocking `git config user.name` subprocess here would be
    # pure wasted work with no consumer of its result.
    run = parse_transcript_content(_sample_session())
    assert run.user is None


def test_parse_transcript_content_uses_model_from_turn_context():
    run = parse_transcript_content(_sample_session())
    assert run.model == "gpt-5-codex"


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


def test_parse_transcript_content_has_no_parent_id_or_agent_id_params():
    # AI-51 finding 4: parent_id/agent_id were copied from claude_code.py's
    # signature but never populated by any real call site — Codex CLI has no
    # subagent/nested-session file convention for these. Both params (and
    # their run_id/parent_id branches) must be gone.
    import inspect
    params = inspect.signature(parse_transcript_content).parameters
    assert "parent_id" not in params
    assert "agent_id" not in params


def test_parse_transcript_content_run_parent_id_is_always_none():
    run = parse_transcript_content(_sample_session())
    assert run.parent_id is None


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


def test_parse_transcript_content_handles_null_arguments():
    # A function_call event with "arguments": null (key present, value null) —
    # distinct from a missing key, which the '{}' default already handles.
    line = _line({
        "timestamp": "2026-04-16T16:02:00.000Z",
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "shell",
            "arguments": None,
            "call_id": "call_null_args",
        },
    })
    content = _sample_session().rstrip("\n") + "\n" + line + "\n"
    run = parse_transcript_content(content)
    assert run is not None


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
