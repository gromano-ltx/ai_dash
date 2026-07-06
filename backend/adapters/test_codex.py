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
    assert run.input_tokens == 3010
    assert run.output_tokens == 128
